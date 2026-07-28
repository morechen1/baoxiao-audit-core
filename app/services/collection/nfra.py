from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.exceptions import CollectionError, UnsafeUrlError
from app.services.collection.base import BaseCollector, CollectionResult
from app.services.collection.security import SafeUrlPolicy

NFRA_PUBLIC_HOST = "www.nfra.gov.cn"
NFRA_DOCUMENT_PATH = "/cn/static/data/DocInfo/SelectByDocId/data_docId={doc_id}.json"
_ERROR_MARKERS = (
    "accessdenied",
    "requestrejected",
    "访问被拒绝",
    "页面不存在",
    "系统错误",
    "统一错误页",
)


@dataclass(frozen=True)
class NfraPublicPayload:
    doc_id: str
    title: str
    html_body: str
    plain_text: str
    publisher: str | None
    published_at: date | None
    document_number: str | None


class NfraPublicDocumentCollector(BaseCollector):
    """Collect one approved NFRA landing page through its deterministic public JSON."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        allowed_hosts: frozenset[str],
        allow_subdomains: bool = False,
        expected_title: str | None = None,
        transport: httpx.BaseTransport | None = None,
        url_policy: SafeUrlPolicy | None = None,
    ) -> None:
        super().__init__(settings)
        self.allowed_hosts = allowed_hosts
        self.allow_subdomains = allow_subdomains
        self.expected_title = expected_title
        self.transport = transport
        self.url_policy = url_policy or SafeUrlPolicy()

    @staticmethod
    def supports(target: str, allowed_hosts: frozenset[str]) -> bool:
        parsed = urlparse(target)
        query = parse_qs(parsed.query, keep_blank_values=True)
        doc_ids = query.get("docId", [])
        return (
            parsed.scheme == "https"
            and (parsed.hostname or "").rstrip(".").lower() == NFRA_PUBLIC_HOST
            and NFRA_PUBLIC_HOST in allowed_hosts
            and len(doc_ids) == 1
            and bool(re.fullmatch(r"\d+", doc_ids[0]))
        )

    @staticmethod
    def retrieval_url(target: str) -> tuple[str, str]:
        parsed = urlparse(target)
        query = parse_qs(parsed.query, keep_blank_values=True)
        doc_ids = query.get("docId", [])
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").rstrip(".").lower() != NFRA_PUBLIC_HOST
            or len(doc_ids) != 1
            or not re.fullmatch(r"\d+", doc_ids[0])
        ):
            raise CollectionError("nfra_invalid_landing_url")
        doc_id = doc_ids[0]
        return (
            f"https://{NFRA_PUBLIC_HOST}{NFRA_DOCUMENT_PATH.format(doc_id=doc_id)}",
            doc_id,
        )

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.25, max=2),
        reraise=True,
    )
    def collect(self, target: str | Path) -> CollectionResult:
        landing_url = str(target)
        retrieval_url, doc_id = self.retrieval_url(landing_url)
        self._require_allowed_host(landing_url)
        self._require_allowed_host(retrieval_url)
        landing_addresses = self.url_policy.resolve_and_validate(landing_url)
        retrieval_addresses = self.url_policy.resolve_and_validate(retrieval_url)

        with httpx.Client(
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.http_timeout_seconds,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            with client.stream("HEAD", landing_url) as landing_response:
                self._validate_peer(landing_response, landing_addresses)
                if landing_response.is_redirect:
                    raise httpx.TooManyRedirects(
                        "NFRA landing page redirects are forbidden",
                        request=landing_response.request,
                    )
                landing_status = landing_response.status_code
            with client.stream("GET", retrieval_url) as response:
                self._validate_peer(response, retrieval_addresses)
                if response.is_redirect:
                    raise httpx.TooManyRedirects(
                        "NFRA public endpoint redirects are forbidden",
                        request=response.request,
                    )
                response.raise_for_status()
                if str(response.url) != retrieval_url:
                    raise CollectionError("nfra_retrieval_url_changed")
                content_type = response.headers.get("content-type", "")
                if content_type.split(";", 1)[0].strip().lower() != "application/json":
                    raise CollectionError("nfra_unexpected_response_type")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.settings.max_download_bytes:
                        raise ValueError("Download exceeds configured size limit")

        raw = bytes(content)
        payload = parse_nfra_public_payload(raw, expected_doc_id=doc_id)
        require_nfra_document_quality(payload, self.expected_title)
        return CollectionResult(
            content=raw,
            source_url=landing_url,
            final_url=retrieval_url,
            content_type=content_type,
            http_status=response.status_code,
            title=payload.title,
            publisher=payload.publisher,
            published_at=payload.published_at,
            metadata={
                "collector": "NfraPublicDocumentCollector",
                "landing_url": landing_url,
                "retrieval_url": retrieval_url,
                "doc_id": doc_id,
                "document_number": payload.document_number,
                "landing_http_status": landing_status,
                "response_headers": {
                    "last-modified": response.headers.get("last-modified"),
                },
                "validated_landing_addresses": sorted(str(value) for value in landing_addresses),
                "validated_retrieval_addresses": sorted(
                    str(value) for value in retrieval_addresses
                ),
                "validated_peer_scope": NFRA_PUBLIC_HOST,
                "quality_gate": "passed",
            },
        )

    def _require_allowed_host(self, url: str) -> None:
        if not self.url_policy.host_allowed_for_hosts(
            url,
            self.allowed_hosts,
            allow_subdomains=self.allow_subdomains,
        ):
            raise UnsafeUrlError("source_host_not_allowed")

    def _validate_peer(
        self,
        response: httpx.Response,
        validated_addresses: frozenset[Any],
    ) -> None:
        if self.transport is not None:
            return
        stream = response.extensions.get("network_stream")
        if stream is None:
            raise RuntimeError("Unable to verify connection peer")
        peer = stream.get_extra_info("server_addr")
        if not peer:
            raise RuntimeError("Unable to read connection peer")
        self.url_policy.validate_peer(str(peer[0]), validated_addresses)


def parse_nfra_public_payload(
    raw: bytes,
    *,
    expected_doc_id: str | None = None,
) -> NfraPublicPayload:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError("nfra_invalid_json") from exc
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise CollectionError("nfra_error_payload")
    data: dict[str, Any] = value["data"]
    doc_id = str(data.get("docId") or "").strip()
    if not doc_id or (expected_doc_id is not None and doc_id != expected_doc_id):
        raise CollectionError("nfra_doc_id_mismatch")
    title = _text_value(data.get("docTitle"))
    html_body = _text_value(data.get("docClob"))
    if not title:
        raise CollectionError("nfra_title_missing")
    if not html_body:
        raise CollectionError("nfra_empty_document_body")
    plain_text = _html_to_text(html_body)
    return NfraPublicPayload(
        doc_id=doc_id,
        title=title,
        html_body=html_body,
        plain_text=plain_text,
        publisher=_optional_text(data.get("docSource")),
        published_at=_parse_date(data.get("publishDate")),
        document_number=_optional_text(data.get("documentNo")),
    )


def require_nfra_document_quality(
    payload: NfraPublicPayload,
    expected_title: str | None,
) -> None:
    normalized_title = _normalize(payload.title)
    normalized_body = _normalize(payload.plain_text)
    if len(normalized_body) < 200:
        raise CollectionError("nfra_empty_document_body")
    if expected_title:
        expected = _normalize(expected_title)
        if expected not in normalized_title and expected not in normalized_body:
            raise CollectionError("nfra_title_mismatch")
    combined = f"{normalized_title}\n{normalized_body}"
    if any(marker in combined for marker in _ERROR_MARKERS):
        raise CollectionError("nfra_error_page")


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()
    return "\n".join(
        line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).lower()


def _text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    text = _text_value(value)
    return text or None


def _parse_date(value: Any) -> date | None:
    text = _text_value(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
