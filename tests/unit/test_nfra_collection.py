from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import CollectionError
from app.models.enums import AuthenticityType, DataType
from app.services.collection.nfra import (
    NFRA_PUBLIC_HOST,
    NfraPublicDocumentCollector,
    parse_nfra_public_payload,
    require_nfra_document_quality,
)
from app.services.collection.security import SafeUrlPolicy
from app.services.parsed_artifacts import ParsedArtifactIntegrityService
from app.services.parsing import NfraJsonParser, ParsingService
from app.services.pilot.service import SAFE_COLLECTION_ERROR_CODES

LANDING_URL = (
    "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=123456&generaltype=0&itemId=4098"
)
RETRIEVAL_URL = (
    "https://www.nfra.gov.cn/cn/static/data/DocInfo/SelectByDocId/data_docId=123456.json"
)
EXPECTED_TITLE = "保险销售行为测试办法"


class RecordingPublicPolicy(SafeUrlPolicy):
    def __init__(self) -> None:
        self.resolved: list[str] = []

    def resolve_and_validate(
        self, url: str
    ) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        self.resolved.append(url)
        return frozenset({ipaddress.ip_address("203.0.113.10")})


def nfra_json(
    *,
    doc_id: str = "123456",
    title: str = EXPECTED_TITLE,
    body: str | None = None,
) -> bytes:
    content = body or ("第一条 保险销售行为应当依法合规。" * 30)
    return json.dumps(
        {
            "data": {
                "docId": doc_id,
                "docTitle": title,
                "docClob": f"<div><p>{content}</p></div>",
                "docSource": "国家金融监督管理总局",
                "publishDate": "2026-07-28 10:30:00",
                "documentNo": "金规〔2026〕1号",
                "attachmentInfoVOList": [],
            },
            "msg": "success",
            "rptCode": "200",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def collector_for(
    tmp_path: Path,
    handler,
    *,
    expected_title: str | None = EXPECTED_TITLE,
    source_type: str | None = None,
    policy: SafeUrlPolicy | None = None,
) -> NfraPublicDocumentCollector:
    return NfraPublicDocumentCollector(
        Settings(database_url="sqlite://", data_dir=tmp_path / "data"),
        allowed_hosts=frozenset({NFRA_PUBLIC_HOST}),
        allow_subdomains=False,
        expected_title=expected_title,
        source_type=source_type,
        transport=httpx.MockTransport(handler),
        url_policy=policy or RecordingPublicPolicy(),
    )


def test_adapter_only_supports_canonical_approved_nfra_landing_url() -> None:
    allowed = frozenset({NFRA_PUBLIC_HOST})

    assert NfraPublicDocumentCollector.supports(LANDING_URL, allowed) is True
    assert (
        NfraPublicDocumentCollector.supports(
            "https://example.test/ItemDetail.html?docId=123456",
            allowed,
        )
        is False
    )
    assert (
        NfraPublicDocumentCollector.supports(
            "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=abc",
            allowed,
        )
        is False
    )


def test_all_four_official_nfra_landing_path_families_are_supported() -> None:
    allowed = frozenset({NFRA_PUBLIC_HOST})
    paths = (
        "/cn/view/pages/ItemDetail.html",
        "/cn/view/pages/governmentDetail.html",
        "/cn/view/pages/rulesDetail.html",
        "/branch/hebei_1-test/view/pages/common/ItemDetail.html",
    )

    for path in paths:
        target = f"https://{NFRA_PUBLIC_HOST}:443{path}?docId=123&itemId=4098"
        assert NfraPublicDocumentCollector.is_dynamic_landing_candidate(target)
        assert NfraPublicDocumentCollector.supports(target, allowed)
        assert NfraPublicDocumentCollector.retrieval_url(target)[1] == "123"


def test_nfra_landing_validation_rejects_untrusted_url_shapes() -> None:
    allowed = frozenset({NFRA_PUBLIC_HOST})
    invalid_targets = (
        f"https://{NFRA_PUBLIC_HOST}/not-a-document?docId=123",
        f"https://{NFRA_PUBLIC_HOST}:444/cn/view/pages/ItemDetail.html?docId=123",
        f"https://{NFRA_PUBLIC_HOST}/cn/view/pages/ItemDetail.html#fragment?docId=123",
        f"https://{NFRA_PUBLIC_HOST}/cn/view/pages/ItemDetail.html?docId=１２３",
        f"https://{NFRA_PUBLIC_HOST}/cn/view/pages/ItemDetail.html?docId=123&docId=123",
        f"https://user:password@{NFRA_PUBLIC_HOST}/cn/view/pages/ItemDetail.html?docId=123",
        f"https://{NFRA_PUBLIC_HOST}./cn/view/pages/ItemDetail.html?docId=123",
    )

    for target in invalid_targets:
        assert not NfraPublicDocumentCollector.supports(target, allowed)
        with pytest.raises(CollectionError, match="nfra_invalid_landing_url"):
            NfraPublicDocumentCollector.retrieval_url(target)


def test_static_nfra_files_are_not_dynamic_landing_candidates() -> None:
    targets = (
        f"https://{NFRA_PUBLIC_HOST}/files/public-rule.pdf",
        f"https://{NFRA_PUBLIC_HOST}/files/public-rule.docx",
    )

    assert all(
        not NfraPublicDocumentCollector.is_dynamic_landing_candidate(target) for target in targets
    )


def test_adapter_derives_deterministic_get_endpoint_from_doc_id() -> None:
    retrieval_url, doc_id = NfraPublicDocumentCollector.retrieval_url(LANDING_URL)

    assert retrieval_url == RETRIEVAL_URL
    assert doc_id == "123456"


def test_adapter_validates_landing_and_retrieval_and_preserves_raw_json(
    tmp_path: Path,
) -> None:
    raw = nfra_json()
    policy = RecordingPublicPolicy()
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        assert request.headers.get("cookie") is None
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(200, headers={"content-type": "application/json"}, content=raw)

    result = collector_for(tmp_path, handler, policy=policy).collect(LANDING_URL)

    assert result.content == raw
    assert result.source_url == LANDING_URL
    assert result.final_url == RETRIEVAL_URL
    assert result.title == EXPECTED_TITLE
    assert result.metadata["doc_id"] == "123456"
    assert result.metadata["landing_http_status"] == 200
    assert result.metadata["quality_gate"] == "passed"
    assert policy.resolved == [LANDING_URL, RETRIEVAL_URL]
    assert requests == [("HEAD", LANDING_URL), ("GET", RETRIEVAL_URL)]


def test_adapter_rejects_non_json_response(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")

    with pytest.raises(CollectionError, match="nfra_unexpected_response_type"):
        collector_for(tmp_path, handler).collect(LANDING_URL)


def test_adapter_rejects_payload_for_different_doc_id(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=nfra_json(doc_id="999999"),
        )

    with pytest.raises(CollectionError, match="nfra_doc_id_mismatch"):
        collector_for(tmp_path, handler).collect(LANDING_URL)


def test_nfra_api_rejects_failed_or_missing_status() -> None:
    base_payload = json.loads(nfra_json())
    assert "nfra_api_status_error" in SAFE_COLLECTION_ERROR_CODES
    for status in (500, "500", "", None):
        payload = dict(base_payload)
        if status is None:
            payload.pop("rptCode")
        else:
            payload["rptCode"] = status

        with pytest.raises(CollectionError, match="nfra_api_status_error"):
            parse_nfra_public_payload(json.dumps(payload).encode())


def test_nfra_api_accepts_string_and_integer_success_status() -> None:
    base_payload = json.loads(nfra_json())
    for status in ("200", 200):
        payload = {**base_payload, "rptCode": status}

        parsed = parse_nfra_public_payload(json.dumps(payload).encode())

        assert parsed.doc_id == "123456"


def test_nfra_payload_doc_id_is_always_ascii_numeric() -> None:
    base_payload = json.loads(nfra_json())
    for doc_id in ("arbitrary", "１２３", " 123 ", True):
        payload = json.loads(json.dumps(base_payload))
        payload["data"]["docId"] = doc_id

        with pytest.raises(CollectionError, match="nfra_doc_id_mismatch"):
            parse_nfra_public_payload(json.dumps(payload).encode())


def test_adapter_rejects_unrelated_title_and_body(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=nfra_json(title="无关公告", body="这是另一份完全无关的公开公告正文。" * 30),
        )

    with pytest.raises(CollectionError, match="nfra_title_mismatch"):
        collector_for(tmp_path, handler).collect(LANDING_URL)


def test_adapter_rejects_short_error_template(tmp_path: Path) -> None:
    payload = parse_nfra_public_payload(nfra_json(body="访问被拒绝"))

    with pytest.raises(CollectionError, match="nfra_error_page"):
        require_nfra_document_quality(payload, EXPECTED_TITLE)


def test_adapter_accepts_expected_title_supported_by_body(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=nfra_json(
                title="正式文件附件",
                body=f"{EXPECTED_TITLE}，风险提示具体内容如下。" * 30,
            ),
        )

    result = collector_for(
        tmp_path,
        handler,
        source_type="regulatory_case",
    ).collect(LANDING_URL)

    assert result.metadata["quality_gate"] == "passed"
    assert result.metadata["page_classifications"] == ["consumer_risk_alert"]


def test_adapter_rejects_long_access_denied_template(tmp_path: Path) -> None:
    cases = (
        ("访问被拒绝，请稍后重试。", "nfra_error_page", None, False),
        ("请输入验证码后继续访问。", "nfra_captcha_page", None, False),
        ("{{data.docClob}} ng-bind", "nfra_angular_template_shell", None, True),
        ("行政许可事项决定公告。", "nfra_administrative_license", "penalty", True),
        ("任职资格批复公告。", "nfra_appointment_qualification", "penalty", True),
    )
    for body, error_code, source_type, repeat in cases:
        payload = parse_nfra_public_payload(
            nfra_json(body=f"{EXPECTED_TITLE} {body}" * (30 if repeat else 1))
        )
        with pytest.raises(CollectionError, match=error_code):
            require_nfra_document_quality(payload, EXPECTED_TITLE, source_type)

    non_insurance = parse_nfra_public_payload(
        nfra_json(
            title="行政处罚决定",
            body="某银行行政处罚决定。" * 30,
        )
    )
    with pytest.raises(CollectionError, match="nfra_non_insurance_penalty"):
        require_nfra_document_quality(
            non_insurance,
            "行政处罚决定",
            "penalty",
        )

    penalty = parse_nfra_public_payload(
        nfra_json(
            title="国家金融监督管理总局巴南监管分局行政处罚信息公开表（巴南金管罚决字〔2025〕3号）",
            body="某保险公司行政处罚决定，决定日期为2025年3月。" * 30,
        )
    )
    assert require_nfra_document_quality(
        penalty,
        "巴南金管罚决字〔2025〕3号行政处罚信息公开表",
        "penalty",
    ) == ("penalty_publication",)


def test_adapter_persists_full_json_with_pending_authenticity(
    session,
    tmp_path: Path,
) -> None:
    raw = nfra_json()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=raw,
        )

    collector = collector_for(tmp_path, handler, source_type="regulation")
    document, created = collector.persist(
        session,
        collector.collect(LANDING_URL),
        DataType.REGULATION.value,
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
    )

    assert created is True
    assert Path(document.raw_file_path).suffix == ".json"
    assert Path(document.raw_file_path).read_bytes() == raw
    assert document.authenticity_type == AuthenticityType.PENDING_VERIFICATION.value
    assert document.source_url == LANDING_URL
    assert document.final_url == RETRIEVAL_URL
    assert document.metadata_json["page_classifications"] == ["valid_regulation"]


def test_nfra_json_parser_is_deterministic_and_removes_markup(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    path.write_bytes(nfra_json(body="<script>danger()</script>保险销售应当合规。" * 12))
    parser = NfraJsonParser()

    first = parser.parse(path)
    second = parser.parse(path)

    assert first == second
    assert first.title == EXPECTED_TITLE
    assert first.plain_text.startswith(f"{EXPECTED_TITLE}\n金规〔2026〕1号")
    assert "<script>" not in first.plain_text
    assert "danger()" not in first.plain_text
    assert first.metadata["doc_id"] == "123456"


def test_nfra_json_enters_existing_immutable_parsed_hash_chain(
    session,
    tmp_path: Path,
) -> None:
    raw = nfra_json()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=raw,
        )

    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    collector = collector_for(tmp_path, handler)
    document, _ = collector.persist(
        session,
        collector.collect(LANDING_URL),
        DataType.REGULATION.value,
    )

    parsed = ParsingService(settings).parse_document(session, document)
    artifact = ParsedArtifactIntegrityService(settings).verify(document, session=session)

    assert document.parse_status == "parsed"
    assert document.raw_text == parsed.plain_text
    assert artifact["title"] == EXPECTED_TITLE
    assert artifact["plain_text"] == document.raw_text
