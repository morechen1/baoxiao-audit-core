from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import app.cli.main as cli_module
from app.api.dependencies import get_db
from app.cli.main import app as cli_app
from app.core.config import Settings
from app.core.exceptions import TrustGateError
from app.main import app
from app.models import (
    AuthenticityDecisionLog,
    DataSource,
    DocumentOccurrence,
    KnowledgeChunk,
    KnowledgeIndexRun,
    Penalty,
    ProductDocument,
    Regulation,
    ReviewBatch,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import AuthenticityType, DataType, KnowledgeIndexStatus, ReviewStatus
from app.services.knowledge import (
    KnowledgeIndexService,
    SearchRequest,
    TrustedKnowledgeSearchService,
)
from app.services.knowledge.normalization import han_bigram_tokens, trusted_lexical_normalize
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.penalty_entries import (
    build_penalty_identity_material,
    build_penalty_source_entry_fragments,
    penalty_source_entry_fingerprint,
    source_entry_content_sha256,
)


def _persist_artifacts(session, document: SourceDocument, text: str) -> None:
    data_dir = session.info["data_dir"]
    raw_path = data_dir / "raw" / f"{hashlib.sha256(text.encode()).hexdigest()}.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(text, encoding="utf-8")
    document.raw_file_path = str(raw_path)
    document.sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    ParsedArtifactService(Settings(data_dir=data_dir)).persist(
        session,
        document,
        ParsedDocument(
            title=document.source_title,
            plain_text=text,
            pages=[ParsedPage(page_number=1, text=text)],
        ),
        parser_name="TrustedRetrievalTestParser",
    )
    document.metadata_json = {
        **document.metadata_json,
        "automatic_validation": {"valid": True, "issues": []},
    }


def _add_trust_audit(session, document: SourceDocument) -> None:
    source = DataSource(
        name="官方来源",
        base_url="https://example.test",
        publisher="测试机构",
        source_type=document.data_type,
        enabled=True,
        crawl_policy={"allowed_domains": ["example.test"]},
    )
    session.add(source)
    session.flush()
    document.source_id = source.id
    occurrence = DocumentOccurrence(
        document_id=document.id,
        source_id=source.id,
        source_url=document.source_url,
        final_url=document.final_url,
        publisher=document.publisher,
        response_metadata={},
    )
    batch = ReviewBatch(
        batch_name="trusted-retrieval-test",
        data_type=document.data_type,
        record_count=1,
        export_path="review.jsonl",
        export_sha256="a" * 64,
        schema_version="2.0",
        status="completed",
    )
    session.add_all([occurrence, batch])
    session.flush()
    decision = ReviewDecision(
        batch_id=batch.id,
        record_type=document.data_type,
        record_id=document.id,
        decision=document.final_review_status,
        field_reviews_json={},
        corrections_json={},
        evidence_quality="A",
        review_comment="trusted retrieval test",
        reviewer="test-reviewer",
        reviewed_payload_hash="b" * 64,
        schema_version="2.0",
    )
    session.add(decision)
    session.flush()
    session.add(
        AuthenticityDecisionLog(
            document_id=document.id,
            previous_authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
            new_authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
            reviewer="test-reviewer",
            review_decision_id=decision.id,
            source_id=source.id,
            verified_occurrence_id=occurrence.id,
            reason="verified official test source",
        )
    )


def _trusted_product(session) -> tuple[SourceDocument, ProductDocument]:
    text = (
        "演示安心寿险 某保险公司 寿险 保险责任为身故保障 "
        "等待期三十日 等待期六十日 责任免除为故意行为"
    )
    document = SourceDocument(
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url="https://example.test/product",
        final_url="https://example.test/product.json",
        source_title="演示安心寿险",
        publisher="某保险公司",
        raw_file_path="pending",
        raw_text=text,
        sha256="0" * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.APPROVED.value,
        knowledge_index_status=KnowledgeIndexStatus.INDEXED.value,
    )
    session.add(document)
    session.flush()
    _persist_artifacts(session, document, text)
    values = {
        "product_name": "演示安心寿险",
        "company_name": "某保险公司",
        "product_type": "寿险",
        "insurance_responsibility": "保险责任为身故保障",
        "waiting_period": "等待期三十日",
        "exclusions": "责任免除为故意行为",
    }
    evidence = {}
    for field_name, value in values.items():
        start = text.index(value)
        evidence[field_name] = [
            {
                "quote": value,
                "page_number": 1,
                "start_offset": start,
                "end_offset": start + len(value),
                "mode": "verbatim",
            }
        ]
    product = ProductDocument(
        document_id=document.id,
        product_name=values["product_name"],
        company_name=values["company_name"],
        product_type=values["product_type"],
        insurance_responsibility=values["insurance_responsibility"],
        waiting_period=values["waiting_period"],
        exclusions=values["exclusions"],
        source_quote=values["product_name"],
        field_evidence_json=evidence,
        final_review_status=ReviewStatus.APPROVED.value,
    )
    session.add(product)
    _add_trust_audit(session, document)
    session.commit()
    return document, product


def _trusted_regulation(session) -> tuple[SourceDocument, Regulation]:
    text = (
        "保险销售行为管理办法 金监规〔2023〕2号 国家金融监督管理总局\n\n第一条 规范保险销售行为。"
    )
    document = SourceDocument(
        data_type=DataType.REGULATION.value,
        source_url="https://example.test/regulation",
        final_url="https://example.test/regulation.json",
        source_title="保险销售行为管理办法",
        publisher="国家金融监督管理总局",
        raw_file_path="pending",
        raw_text=text,
        sha256="0" * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.APPROVED_WITH_REVISION.value,
        knowledge_index_status=KnowledgeIndexStatus.INDEXED.value,
    )
    session.add(document)
    session.flush()
    _persist_artifacts(session, document, text)
    values = {
        "title": "保险销售行为管理办法",
        "document_number": "金监规〔2023〕2号",
        "issuing_authority": "国家金融监督管理总局",
        "article_number": "第一条",
        "article_text": "规范保险销售行为。",
    }
    evidence = {}
    for field_name, value in values.items():
        start = text.index(value)
        evidence[field_name] = [
            {
                "quote": value,
                "page_number": 1,
                "start_offset": start,
                "end_offset": start + len(value),
                "mode": "verbatim",
            }
        ]
    regulation = Regulation(
        document_id=document.id,
        title=values["title"],
        document_number=values["document_number"],
        issuing_authority=values["issuing_authority"],
        validity_status="unknown",
        article_number=values["article_number"],
        article_text=values["article_text"],
        source_quote=values["article_text"],
        field_evidence_json=evidence,
        final_review_status=ReviewStatus.APPROVED_WITH_REVISION.value,
    )
    session.add(regulation)
    _add_trust_audit(session, document)
    session.commit()
    return document, regulation


def _trusted_penalty(session) -> tuple[SourceDocument, Penalty]:
    text = (
        "某保险公司\n某金融监管局\n乌金罚决字〔2025〕9号\n2025年10月15日\n"
        "销售误导\n罚款10万元\n“优惠”“中奖”"
    )
    document = SourceDocument(
        data_type=DataType.PENALTY.value,
        source_url="https://example.test/penalty",
        final_url="https://example.test/penalty.json",
        source_title="处罚公示",
        publisher="某金融监管局",
        raw_file_path="pending",
        raw_text=text,
        sha256="0" * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.APPROVED.value,
        knowledge_index_status=KnowledgeIndexStatus.INDEXED.value,
    )
    session.add(document)
    session.flush()
    _persist_artifacts(session, document, text)
    fields = {
        "punished_entity": "某保险公司",
        "document_number": "乌金罚决字〔2025〕9号",
        "illegal_facts": "销售误导",
        "penalty_result": "罚款10万元",
    }
    evidence = {}
    for field_name, value in fields.items():
        start = text.index(value)
        evidence[field_name] = [
            {
                "quote": value,
                "page_number": 1,
                "start_offset": start,
                "end_offset": start + len(value),
                "mode": "verbatim",
            }
        ]
    wording = "“优惠”“中奖”"
    wording_start = text.index(wording)
    evidence["original_sales_wording"] = [
        {
            "quote": wording,
            "page_number": 1,
            "start_offset": wording_start,
            "end_offset": wording_start + len(wording),
            "mode": "verbatim",
        }
    ]
    authority = "某金融监管局"
    authority_start = text.index(authority)
    evidence["authority"] = [
        {
            "quote": authority,
            "page_number": 1,
            "start_offset": authority_start,
            "end_offset": authority_start + len(authority),
            "mode": "verbatim",
        }
    ]
    date_quote = "2025年10月15日"
    date_start = text.index(date_quote)
    evidence["decision_date"] = [
        {
            "quote": date_quote,
            "page_number": 1,
            "start_offset": date_start,
            "end_offset": date_start + len(date_quote),
            "mode": "normalized",
            "transformation_note": "date_normalized",
        }
    ]
    material = build_penalty_identity_material(fields, evidence)
    content_sha = source_entry_content_sha256(material)
    penalty = Penalty(
        document_id=document.id,
        source_entry_index=1,
        source_entry_fingerprint=penalty_source_entry_fingerprint(
            raw_artifact_sha256=document.sha256,
            source_entry_content_sha256=content_sha,
        ),
        punished_entity=fields["punished_entity"],
        authority=authority,
        document_number=fields["document_number"],
        decision_date=date(2025, 10, 15),
        illegal_facts=fields["illegal_facts"],
        penalty_result=fields["penalty_result"],
        original_sales_wording_disclosed=True,
        original_sales_wording=wording,
        source_quote=fields["illegal_facts"],
        field_evidence_json=evidence,
        final_review_status=ReviewStatus.APPROVED.value,
    )
    session.add(penalty)
    session.flush()
    document.metadata_json = {
        **document.metadata_json,
        "structured_draft_provenance": [
            {
                "structured_record_id": penalty.id,
                "record_type": "penalty",
                "source_entry_locator": {"table_index": 1, "logical_row": 1},
                "source_entry_fragments": build_penalty_source_entry_fragments(fields, evidence),
                "source_entry_content_sha256": content_sha,
            }
        ],
    }
    _add_trust_audit(session, document)
    session.commit()
    return document, penalty


def _service(session) -> KnowledgeIndexService:
    return KnowledgeIndexService(Settings(data_dir=session.info["data_dir"]))


def test_chinese_and_structured_tokens_are_stable() -> None:
    value = "ＡＢＣ 优惠 中奖 乌金罚决字〔2025〕9号 罚款10万元 2025-10-15"

    assert trusted_lexical_normalize(value).startswith("abc")
    tokens = han_bigram_tokens(value)
    assert tokens == han_bigram_tokens(value)
    assert {"优惠", "中奖", "abc", "乌金罚决字〔2025〕9号", "10万元", "2025-10-15"} <= set(tokens)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "final_review_status",
            ReviewStatus.PENDING_REVIEW.value,
            "knowledge_document_not_index_eligible",
        ),
        (
            "authenticity_type",
            AuthenticityType.PENDING_VERIFICATION.value,
            "knowledge_document_not_index_eligible",
        ),
        (
            "knowledge_index_status",
            KnowledgeIndexStatus.NOT_INDEXED.value,
            "knowledge_document_not_index_eligible",
        ),
        ("data_type", DataType.REGULATORY_CASE.value, "knowledge_document_not_index_eligible"),
    ],
)
def test_materialization_fails_closed_for_ineligible_documents(
    session, field: str, value: str, code: str
) -> None:
    document, _ = _trusted_product(session)
    setattr(document, field, value)
    session.commit()

    with pytest.raises(TrustGateError, match=code):
        _service(session).rebuild_document_chunks(session, document.id)

    assert session.query(KnowledgeChunk).count() == 0


def test_product_rebuild_is_idempotent_and_searchable(session) -> None:
    document, _ = _trusted_product(session)

    first = _service(session).rebuild_document_chunks(session, document.id)
    identities = [chunk.chunk_identity_sha256 for chunk in session.query(KnowledgeChunk)]
    second = _service(session).rebuild_document_chunks(session, document.id)
    results = TrustedKnowledgeSearchService().search(
        session, SearchRequest(query="保险责任", record_types=("product_document",))
    )

    assert first.created == 3
    assert second.created == 0
    assert second.active_chunks == 3
    assert identities == [chunk.chunk_identity_sha256 for chunk in session.query(KnowledgeChunk)]
    assert results
    assert results[0].record_type == "product_document"
    assert (
        results[0].snippet
        in session.query(KnowledgeChunk)
        .filter_by(chunk_identity_sha256=results[0].chunk_identity_sha256)
        .one()
        .text
    )
    assert results[0].source_locator
    assert results[0].evidence_references


def test_business_change_retires_only_replaced_chunk(session) -> None:
    document, product = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    value = "等待期六十日"
    product.waiting_period = value
    start = (document.raw_text or "").index(value)
    product.field_evidence_json = {
        **product.field_evidence_json,
        "waiting_period": [
            {
                "quote": value,
                "page_number": 1,
                "start_offset": start,
                "end_offset": start + len(value),
                "mode": "verbatim",
            }
        ],
    }
    session.commit()

    result = _service(session).rebuild_document_chunks(session, document.id)

    assert result.created == 3
    assert result.retired == 3
    assert session.query(KnowledgeChunk).filter_by(is_active=True).count() == 3
    assert session.query(KnowledgeChunk).filter_by(is_active=False).count() == 3


def test_failed_rebuild_leaves_previous_active_chunks(session, monkeypatch) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr("app.services.knowledge.materialization.build_record_chunks", fail)
    with pytest.raises(TrustGateError, match="knowledge_chunk_build_failed"):
        _service(session).rebuild_document_chunks(session, document.id)

    assert session.query(KnowledgeChunk).filter_by(is_active=True).count() == 3
    assert session.query(KnowledgeChunk).filter_by(is_active=False).count() == 0


def test_penalty_identity_wording_and_filters_are_preserved(session) -> None:
    document, penalty = _trusted_penalty(session)

    result = _service(session).rebuild_document_chunks(session, document.id)
    wording = TrustedKnowledgeSearchService().search(
        session, SearchRequest(query="优惠 中奖", record_types=("penalty",))
    )
    entity = TrustedKnowledgeSearchService().search(
        session, SearchRequest(query="某保险公司", authority="某金融监管局")
    )

    assert result.created == 1
    assert wording and "“优惠”“中奖”" in wording[0].snippet
    assert entity and entity[0].structured_record_id == penalty.id
    assert entity[0].source_locator["source_entry_index"] == 1
    assert entity[0].source_locator["source_entry_fingerprint"] == penalty.source_entry_fingerprint


def test_legacy_penalty_is_rejected(session) -> None:
    document, _ = _trusted_penalty(session)
    provenance = document.metadata_json["structured_draft_provenance"][0]
    provenance["identity_version"] = "legacy_source_quote_v1"
    provenance["source_identity_status"] = "reimport_required"
    document.metadata_json = {**document.metadata_json, "structured_draft_provenance": [provenance]}
    session.commit()

    with pytest.raises(TrustGateError, match="penalty_source_identity_reimport_required"):
        _service(session).rebuild_document_chunks(session, document.id)

    assert session.query(KnowledgeChunk).count() == 0


def test_search_filters_empty_query_retirement_and_verify(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)

    filtered = TrustedKnowledgeSearchService().search(
        session,
        SearchRequest(
            query="",
            record_types=("product_document",),
            authority="某保险公司",
            evidence_quality=("A",),
            limit=10,
        ),
    )
    assert len(filtered) == 3
    assert _service(session).verify_chunks(session).valid

    document.knowledge_index_status = KnowledgeIndexStatus.NOT_INDEXED.value
    session.commit()
    assert not TrustedKnowledgeSearchService().search(session, SearchRequest(query="保险"))
    assert not _service(session).verify_chunks(session).valid
    assert _service(session).retire_document_chunks(session, document.id) == 3


def test_invalid_search_limits_and_record_type_fail_closed() -> None:
    with pytest.raises(TrustGateError, match="knowledge_search_limit_invalid"):
        SearchRequest(limit=101)
    with pytest.raises(TrustGateError, match="knowledge_search_record_type_invalid"):
        SearchRequest(record_types=("regulatory_case",))


def test_api_and_cli_use_same_stable_search_order(session, monkeypatch) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    app.dependency_overrides[get_db] = lambda: session
    try:
        api_response = TestClient(app).get("/api/v1/knowledge/search", params={"query": "保险"})
    finally:
        app.dependency_overrides.clear()

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(cli_module, "SessionLocal", SessionContext)
    cli_result = CliRunner().invoke(cli_app, ["knowledge", "search", "保险"])

    assert api_response.status_code == 200
    assert cli_result.exit_code == 0
    api_ids = [row["chunk_identity_sha256"] for row in api_response.json()["results"]]
    cli_ids = [row["chunk_identity_sha256"] for row in json.loads(cli_result.stdout)]
    assert api_ids == cli_ids


def test_regulation_builder_preserves_structured_fields_and_evidence(session) -> None:
    document, regulation = _trusted_regulation(session)

    summary = _service(session).rebuild_document_chunks(session, document.id)
    chunks = session.query(KnowledgeChunk).order_by(KnowledgeChunk.chunk_ordinal).all()

    assert summary.created == 3
    assert [chunk.chunk_kind for chunk in chunks] == [
        "basic_information",
        "article_text",
        "scope_and_status",
    ]
    assert chunks[0].structured_record_id == regulation.id
    assert "金监规〔2023〕2号" in chunks[0].text
    assert any(
        item["field_name"] == "document_number" for item in chunks[0].evidence_reference_json
    )


def test_exact_document_number_illegal_fact_and_result_are_searchable(session) -> None:
    document, penalty = _trusted_penalty(session)
    _service(session).rebuild_document_chunks(session, document.id)
    service = TrustedKnowledgeSearchService()

    number = service.search(session, SearchRequest(query="乌金罚决字〔2025〕9号"))
    facts = service.search(session, SearchRequest(query="销售误导"))
    result = service.search(session, SearchRequest(query="罚款10万元"))

    assert number[0].structured_record_id == penalty.id
    assert facts[0].structured_record_id == penalty.id
    assert result[0].structured_record_id == penalty.id


def test_pilot_authority_and_date_filters_are_combined(session) -> None:
    document, _ = _trusted_penalty(session)
    _service(session).rebuild_document_chunks(session, document.id)
    chunk = session.query(KnowledgeChunk).one()
    chunk.pilot_id = "PEN-010"
    session.commit()

    matches = TrustedKnowledgeSearchService().search(
        session,
        SearchRequest(
            query="",
            pilot_ids=("PEN-010",),
            authority="金融监管",
            date_from=date(2025, 1, 1),
            date_to=date(2025, 12, 31),
            limit=1,
        ),
    )
    excluded = TrustedKnowledgeSearchService().search(
        session, SearchRequest(query="", date_to=date(2024, 12, 31))
    )

    assert len(matches) == 1
    assert matches[0].relevant_date == date(2025, 10, 15)
    assert excluded == []


def test_penalty_identity_inconsistency_has_specific_error(session) -> None:
    document, penalty = _trusted_penalty(session)
    penalty.source_entry_fingerprint = "f" * 64
    session.commit()

    with pytest.raises(TrustGateError, match="knowledge_penalty_identity_invalid"):
        _service(session).rebuild_document_chunks(session, document.id)

    assert session.query(KnowledgeChunk).count() == 0


def test_full_rebuild_is_idempotent_and_records_run_ledger(session) -> None:
    _trusted_product(session)
    _trusted_regulation(session)

    first = _service(session).rebuild_all_trusted_chunks(session)
    first_identities = sorted(
        value for (value,) in session.query(KnowledgeChunk.chunk_identity_sha256).all()
    )
    second = _service(session).rebuild_all_trusted_chunks(session)
    second_identities = sorted(
        value for (value,) in session.query(KnowledgeChunk.chunk_identity_sha256).all()
    )

    assert (first.documents, first.records, first.created) == (2, 2, 6)
    assert (second.documents, second.records, second.created) == (2, 2, 0)
    assert first_identities == second_identities
    assert session.query(KnowledgeIndexRun).filter_by(status="completed").count() == 2


def test_verify_detects_content_hash_tampering(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    chunk = session.query(KnowledgeChunk).first()
    chunk.text = f"{chunk.text}被篡改"
    session.commit()

    report = _service(session).verify_chunks(session)

    assert not report.valid
    assert report.hash_mismatches == 1


def test_verify_detects_missing_source_locator(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    chunk = session.query(KnowledgeChunk).first()
    chunk.source_locator_json = {}
    session.commit()

    report = _service(session).verify_chunks(session)

    assert not report.valid
    assert report.source_locator_missing == 1


def test_search_tie_order_is_repeatable(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    service = TrustedKnowledgeSearchService()

    first = service.search(session, SearchRequest(query=""))
    second = service.search(session, SearchRequest(query=""))

    assert [item.chunk_identity_sha256 for item in first] == [
        item.chunk_identity_sha256 for item in second
    ]
    assert [item.rank for item in first] == list(range(1, len(first) + 1))


def test_source_url_is_taken_from_verified_occurrence(session) -> None:
    document, _ = _trusted_product(session)
    document.source_url = "https://untrusted-change.test/product"
    session.commit()

    _service(session).rebuild_document_chunks(session, document.id)
    chunk = session.query(KnowledgeChunk).first()

    assert chunk.source_url == "https://example.test/product"
    assert chunk.source_locator_json["final_url"] == "https://example.test/product.json"


def test_null_penalty_fields_do_not_create_placeholder_claims(session) -> None:
    document, penalty = _trusted_penalty(session)
    assert penalty.legal_basis is None
    _service(session).rebuild_document_chunks(session, document.id)
    text = session.query(KnowledgeChunk).one().text

    assert "法律依据" not in text
    assert "未知" not in text
    assert "暂无" not in text


def test_retired_chunks_are_not_returned_even_when_document_stays_eligible(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    assert _service(session).retire_document_chunks(session, document.id) == 3

    assert TrustedKnowledgeSearchService().search(session, SearchRequest(query="保险")) == []


def test_search_request_rejects_query_offset_date_and_quality_boundaries() -> None:
    with pytest.raises(TrustGateError, match="knowledge_search_query_too_long"):
        SearchRequest(query="x" * 501)
    with pytest.raises(TrustGateError, match="knowledge_search_offset_invalid"):
        SearchRequest(offset=10_001)
    with pytest.raises(TrustGateError, match="knowledge_search_date_range_invalid"):
        SearchRequest(date_from=date(2025, 2, 1), date_to=date(2025, 1, 1))
    with pytest.raises(TrustGateError, match="knowledge_search_evidence_quality_invalid"):
        SearchRequest(evidence_quality=("Z",))


def test_chunk_hashes_remain_stable_after_retire_and_reactivate(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    before = sorted(
        (chunk.chunk_content_sha256, chunk.chunk_identity_sha256)
        for chunk in session.query(KnowledgeChunk)
    )
    _service(session).retire_document_chunks(session, document.id)

    result = _service(session).rebuild_document_chunks(session, document.id)
    after = sorted(
        (chunk.chunk_content_sha256, chunk.chunk_identity_sha256)
        for chunk in session.query(KnowledgeChunk)
    )

    assert result.reactivated == 3
    assert result.created == 0
    assert before == after


def test_pagination_is_bounded_and_non_overlapping(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    service = TrustedKnowledgeSearchService()

    first = service.search(session, SearchRequest(query="", limit=2))
    second = service.search(session, SearchRequest(query="", limit=2, offset=2))

    assert len(first) == 2
    assert len(second) == 1
    assert {item.chunk_identity_sha256 for item in first}.isdisjoint(
        {item.chunk_identity_sha256 for item in second}
    )


def test_document_review_status_change_immediately_hides_chunks(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    document.final_review_status = ReviewStatus.REJECTED.value
    session.commit()

    assert TrustedKnowledgeSearchService().search(session, SearchRequest(query="保险")) == []
    assert _service(session).verify_chunks(session).ineligible_active_chunks == 3


def test_api_rejects_out_of_range_limit_without_querying_database(session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).get(
            "/api/v1/knowledge/search", params={"query": "保险", "limit": 101}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_cli_verify_returns_nonzero_for_corrupt_index(session, monkeypatch) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    session.query(KnowledgeChunk).first().chunk_content_sha256 = "0" * 64
    session.commit()

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(cli_module, "SessionLocal", SessionContext)
    result = CliRunner().invoke(cli_app, ["knowledge", "verify"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["hash_mismatches"] == 1


def test_search_result_carries_review_and_authenticity_state(session) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)

    result = TrustedKnowledgeSearchService().search(
        session, SearchRequest(query="责任免除", limit=1)
    )[0]

    assert result.authenticity_status == AuthenticityType.VERIFIED_PUBLIC.value
    assert result.review_status == ReviewStatus.APPROVED.value
    assert result.chunk_content_sha256
    assert result.chunk_identity_sha256


def test_full_rebuild_failure_records_failed_run_and_keeps_prior_chunks(
    session, monkeypatch
) -> None:
    document, _ = _trusted_product(session)
    _service(session).rebuild_document_chunks(session, document.id)
    active_before = session.query(KnowledgeChunk).filter_by(is_active=True).count()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr("app.services.knowledge.materialization.build_record_chunks", fail)
    with pytest.raises(TrustGateError, match="knowledge_chunk_build_failed"):
        _service(session).rebuild_all_trusted_chunks(session)

    assert session.query(KnowledgeChunk).filter_by(is_active=True).count() == active_before
    assert session.query(KnowledgeIndexRun).filter_by(status="failed").count() == 1
