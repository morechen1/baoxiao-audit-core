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
_NFRA_CENTRAL_LANDING_PATHS = frozenset(
    {
        "/cn/view/pages/ItemDetail.html",
        "/cn/view/pages/governmentDetail.html",
        "/cn/view/pages/rulesDetail.html",
    }
)
_NFRA_BRANCH_LANDING_PATH = re.compile(
    r"/branch/[A-Za-z0-9_-]+/view/pages/common/ItemDetail[.]html",
    flags=re.ASCII,
)
_ERROR_MARKERS = (
    "accessdenied",
    "requestrejected",
    "访问被拒绝",
    "页面不存在",
    "系统错误",
    "统一错误页",
)
_CAPTCHA_MARKERS = ("验证码", "captcha")
_ANGULAR_SHELL_MARKERS = ("{{data.", "ng-bind", "ng-view")
_INSURANCE_MARKERS = ("保险", "人寿", "财产险", "保险代理", "保险经纪")
_RISK_ALERT_MARKERS = ("风险提示", "消费提示", "警惕", "防范", "维护自身合法权益")
_NFRA_DOCUMENT_NUMBER = re.compile(
    r"(?:"
    r"国家金融监督管理总局|"
    r"中国银行保险监督管理委员会|"
    r"中国保险监督管理委员会|"
    r"中国人民银行|"
    r"[\u4e00-\u9fff]{2,20}(?:金融监督管理局|金融监管局|监管局|监管分局)"
    r")令(?:〔|\[)?[0-9]{4}(?:〕|\])?年?第?[0-9]+号",
    flags=re.ASCII,
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
    caption_document_number: str | None


class NfraPublicDocumentCollector(BaseCollector):
    """Collect one approved NFRA landing page through its deterministic public JSON."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        allowed_hosts: frozenset[str],
        allow_subdomains: bool = False,
        expected_title: str | None = None,
        source_type: str | None = None,
        transport: httpx.BaseTransport | None = None,
        url_policy: SafeUrlPolicy | None = None,
    ) -> None:
        super().__init__(settings)
        self.allowed_hosts = allowed_hosts
        self.allow_subdomains = allow_subdomains
        self.expected_title = expected_title
        self.source_type = source_type
        self.transport = transport
        self.url_policy = url_policy or SafeUrlPolicy()

    @staticmethod
    def is_dynamic_landing_candidate(target: str) -> bool:
        parsed = urlparse(target)
        return (parsed.hostname or "").lower() == NFRA_PUBLIC_HOST and _is_allowed_landing_path(
            parsed.path
        )

    @staticmethod
    def supports(target: str, allowed_hosts: frozenset[str]) -> bool:
        if NFRA_PUBLIC_HOST not in allowed_hosts:
            return False
        try:
            _validated_landing_doc_id(target)
        except CollectionError:
            return False
        return True

    @staticmethod
    def retrieval_url(target: str) -> tuple[str, str]:
        doc_id = _validated_landing_doc_id(target)
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
        classifications = require_nfra_document_quality(
            payload,
            self.expected_title,
            self.source_type,
        )
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
                "page_classifications": list(classifications),
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
    if not isinstance(value, dict) or not _successful_api_status(value.get("rptCode")):
        raise CollectionError("nfra_api_status_error")
    if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        raise CollectionError("nfra_error_payload")
    data: dict[str, Any] = value["data"]
    raw_doc_id = data.get("docId")
    if isinstance(raw_doc_id, str):
        doc_id = raw_doc_id
    elif type(raw_doc_id) is int:
        doc_id = str(raw_doc_id)
    else:
        doc_id = ""
    if not re.fullmatch(r"[0-9]+", doc_id, flags=re.ASCII) or (
        expected_doc_id is not None and doc_id != expected_doc_id
    ):
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
        caption_document_number=_caption_document_number(data.get("caption")),
    )


def require_nfra_document_quality(
    payload: NfraPublicPayload,
    expected_title: str | None,
    source_type: str | None = None,
) -> tuple[str, ...]:
    normalized_title = _normalize(payload.title)
    normalized_body = _normalize(payload.plain_text)
    combined = f"{normalized_title}\n{normalized_body}"
    shell_text = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", f"{payload.title}\n{payload.plain_text}"),
    ).lower()
    if any(marker in shell_text for marker in _ANGULAR_SHELL_MARKERS):
        raise CollectionError("nfra_angular_template_shell")
    if any(marker in normalized_title for marker in _CAPTCHA_MARKERS) or (
        len(normalized_body) < 300 and any(marker in normalized_body for marker in _CAPTCHA_MARKERS)
    ):
        raise CollectionError("nfra_captcha_page")
    if any(marker in normalized_title for marker in _ERROR_MARKERS) or (
        len(normalized_body) < 300 and any(marker in normalized_body for marker in _ERROR_MARKERS)
    ):
        raise CollectionError("nfra_error_page")
    minimum_body_length = 80 if source_type in {None, "penalty"} else 200
    if len(normalized_body) < minimum_body_length:
        raise CollectionError("nfra_empty_document_body")
    classifications: list[str] = []
    if source_type == "penalty":
        if "行政许可" in combined:
            raise CollectionError("nfra_administrative_license")
        if "任职资格" in combined:
            raise CollectionError("nfra_appointment_qualification")
        if not any(marker in combined for marker in _INSURANCE_MARKERS):
            raise CollectionError("nfra_non_insurance_penalty")
        classifications.append("penalty_publication")
    elif source_type == "regulatory_case" and any(
        marker in combined for marker in _RISK_ALERT_MARKERS
    ):
        classifications.append("consumer_risk_alert")
    elif source_type == "regulation":
        classifications.append("valid_regulation")
    if expected_title and not nfra_title_supported(
        expected_title,
        payload.title,
        payload.plain_text,
        source_type,
    ):
        raise CollectionError("nfra_title_mismatch")
    return tuple(classifications)


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()
    return "\n".join(
        line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()
    )


def _normalize(value: str) -> str:
    return "".join(
        character.lower()
        for character in unicodedata.normalize("NFKC", value)
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def nfra_title_supported(
    expected_title: str,
    actual_title: str,
    plain_text: str,
    source_type: str | None,
) -> bool:
    expected = _normalize(expected_title)
    actual = _normalize(actual_title)
    body = _normalize(plain_text)
    if expected in actual or expected in body:
        return True
    if source_type != "penalty":
        return False
    combined = f"{actual}{body}"
    penalty_kind_supported = any(
        marker in combined
        for marker in ("行政处罚信息公开表", "行政处罚信息公示表", "行政处罚信息公示列表")
    )
    expected_numbers = re.findall(r"\d+", expected_title)
    return (
        penalty_kind_supported
        and bool(expected_numbers)
        and all(number in combined for number in expected_numbers)
    )


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


def _caption_document_number(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    caption = value.strip()
    if not caption or len(caption) > 500 or "{{" in caption or "}}" in caption or "${" in caption:
        return None
    matches = _NFRA_DOCUMENT_NUMBER.findall(caption)
    return matches[0] if len(matches) == 1 else None


def _is_allowed_landing_path(path: str) -> bool:
    return path in _NFRA_CENTRAL_LANDING_PATHS or bool(_NFRA_BRANCH_LANDING_PATH.fullmatch(path))


def _validated_landing_doc_id(target: str) -> str:
    parsed = urlparse(target)
    try:
        port = parsed.port
    except ValueError as exc:
        raise CollectionError("nfra_invalid_landing_url") from exc
    query = parse_qs(parsed.query, keep_blank_values=True)
    doc_ids = query.get("docId", [])
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != NFRA_PUBLIC_HOST
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
        or not _is_allowed_landing_path(parsed.path)
        or len(doc_ids) != 1
        or not re.fullmatch(r"[0-9]+", doc_ids[0], flags=re.ASCII)
    ):
        raise CollectionError("nfra_invalid_landing_url")
    return doc_ids[0]


def _successful_api_status(value: Any) -> bool:
    return (isinstance(value, str) and value == "200") or (type(value) is int and value == 200)
