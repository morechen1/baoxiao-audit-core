from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from app.api.routes.workflow import get_regulatory_case, list_regulatory_cases
from app.core.config import Settings
from app.core.exceptions import ReviewDecisionError, StructuredRecordError
from app.models import (
    AuthenticityDecisionLog,
    DataSource,
    DocumentOccurrence,
    Penalty,
    RegulatoryCase,
    SourceDocument,
    StructuredDraftRevision,
)
from app.models.enums import (
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    RegulatoryCaseCategory,
    RegulatoryCaseUsage,
    ReviewStatus,
)
from app.schemas.structured import (
    RegulatoryCaseDraft,
    StructuredDraftEnvelope,
    StructuredDraftRevisionEnvelope,
)
from app.services.knowledge import KnowledgeIndexService
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.review import ReviewService
from app.services.review.service import payload_hash
from app.services.structured_records import StructuredRecordService
from app.services.structured_revisions import StructuredDraftRevisionService
from app.services.validation import ValidationService

TITLE = "关于防范保险销售误导的风险提示"
PUBLISHER = "测试金融监管局"
PUBLISHED = date(2026, 1, 1)
SCENARIO = "消费者被告知该产品与银行存款相同。"
SECOND_SCENARIO = "消费者随后被要求继续缴纳保费。"
WORDING = "和银行存款一样安全"
FACTS = "消费者购买后发现产品存在退保损失。"
ANALYSIS = "该宣传容易使消费者混淆保险与存款。"
ADVICE = "消费者应认真阅读保险合同。"
RAW_TEXT = "\n".join(
    [
        TITLE,
        PUBLISHER,
        PUBLISHED.isoformat(),
        SCENARIO,
        SECOND_SCENARIO,
        WORDING,
        FACTS,
        ANALYSIS,
        ADVICE,
    ]
)


def _settings(session) -> Settings:
    return Settings(database_url="sqlite://", data_dir=session.info["data_dir"])


def _make_document(session) -> tuple[SourceDocument, DocumentOccurrence]:
    source = DataSource(
        name=PUBLISHER,
        publisher=PUBLISHER,
        base_url="https://official.test",
        source_type=DataType.REGULATORY_CASE.value,
        enabled=True,
        crawl_policy={"allowed_domains": ["official.test"]},
    )
    session.add(source)
    session.flush()
    raw_dir = session.info["data_dir"] / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = RAW_TEXT.encode()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    raw_path = raw_dir / f"{digest}.txt"
    raw_path.write_bytes(raw_bytes)
    document = SourceDocument(
        source_id=source.id,
        data_type=DataType.REGULATORY_CASE.value,
        source_url="https://official.test/cases/one",
        final_url="https://official.test/cases/one",
        source_title=TITLE,
        publisher=PUBLISHER,
        published_at=PUBLISHED,
        raw_file_path=str(raw_path),
        raw_text=RAW_TEXT,
        sha256=digest,
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PARSED.value,
    )
    session.add(document)
    session.flush()
    occurrence = DocumentOccurrence(
        document_id=document.id,
        source_id=source.id,
        source_url=document.source_url,
        final_url=document.final_url,
        publisher=PUBLISHER,
        http_status=200,
    )
    session.add(occurrence)
    ParsedArtifactService(_settings(session)).persist(
        session,
        document,
        ParsedDocument(
            title=TITLE,
            plain_text=RAW_TEXT,
            pages=[ParsedPage(page_number=1, text=RAW_TEXT)],
        ),
        parser_name="RegulatoryCaseTestParser",
    )
    session.commit()
    return document, occurrence


def _evidence(value: object) -> list[dict[str, object]]:
    rendered = value.isoformat() if isinstance(value, date) else str(value)
    start = RAW_TEXT.index(rendered)
    return [
        {
            "quote": rendered,
            "page_number": 1,
            "start_offset": start,
            "end_offset": start + len(rendered),
            "mode": "verbatim",
        }
    ]


def _envelope(
    document: SourceDocument,
    *,
    disclosed: bool = True,
    wording: str | None = WORDING,
    case_usage: str = RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE.value,
) -> StructuredDraftEnvelope:
    fields = {
        "case_title": TITLE,
        "publisher": PUBLISHER,
        "published_at": PUBLISHED.isoformat(),
        "case_category": RegulatoryCaseCategory.CONSUMER_RISK_ALERT.value,
        "scenario_text": SCENARIO,
        "marketing_wording_disclosed": disclosed,
        "marketing_wording": wording,
        "case_facts": FACTS,
        "regulatory_analysis": ANALYSIS,
        "consumer_advice": ADVICE,
        "case_usage": case_usage,
        "source_quote": TITLE,
    }
    evidence = {
        "case_title": _evidence(TITLE),
        "publisher": _evidence(PUBLISHER),
        "published_at": _evidence(PUBLISHED),
        "scenario_text": _evidence(SCENARIO),
        "case_facts": _evidence(FACTS),
        "regulatory_analysis": _evidence(ANALYSIS),
        "consumer_advice": _evidence(ADVICE),
    }
    if wording is not None:
        evidence["marketing_wording"] = _evidence(WORDING)
    return StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.REGULATORY_CASE,
        fields=fields,
        field_evidence=evidence,
    )


def _import_case(session) -> tuple[SourceDocument, RegulatoryCase, DocumentOccurrence]:
    document, occurrence = _make_document(session)
    record = StructuredRecordService(_settings(session)).import_draft(
        session,
        _envelope(document),
    )
    assert isinstance(record, RegulatoryCase)
    return document, record, occurrence


def _pending_case(session) -> tuple[SourceDocument, RegulatoryCase, DocumentOccurrence]:
    document, record, occurrence = _import_case(session)
    result = ValidationService(_settings(session)).validate_document(session, document)
    assert result.valid
    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value
    return document, record, occurrence


def _review_export(
    session,
) -> tuple[
    ReviewService, object, dict[str, object], SourceDocument, RegulatoryCase, DocumentOccurrence
]:
    document, record, occurrence = _pending_case(session)
    service = ReviewService(_settings(session))
    batch = service.export_batch(session, DataType.REGULATORY_CASE.value, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8").splitlines()[0])
    return service, batch, row, document, record, occurrence


def _decision(
    batch,
    row: dict[str, object],
    *,
    status: str,
    corrections: dict[str, object] | None = None,
    occurrence_id: int | None = None,
) -> dict[str, object]:
    return {
        "batch_id": batch.id,
        "batch_item_id": row["batch_item_id"],
        "reviewed_payload_hash": payload_hash(row),
        "schema_version": batch.schema_version,
        "record_id": row["record_id"],
        "record_type": DataType.REGULATORY_CASE.value,
        "final_status": status,
        "field_reviews": {},
        "corrections": corrections or {},
        "authenticity_decision": (
            {
                "new_type": AuthenticityType.VERIFIED_PUBLIC.value,
                "verified_occurrence_id": occurrence_id,
                "reason": "已核验公开来源及不可变原件",
            }
            if occurrence_id is not None
            else None
        ),
        "evidence_quality": "A",
        "review_comment": "监管案例审核",
        "reviewer": "case_reviewer",
    }


def _write_decision(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "regulatory-case-review.jsonl"
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _approve_directly(
    session,
    document: SourceDocument,
    record: RegulatoryCase,
    usage: RegulatoryCaseUsage,
) -> None:
    document.final_review_status = ReviewStatus.APPROVED.value
    document.authenticity_type = AuthenticityType.VERIFIED_PUBLIC.value
    document.metadata_json = {"automatic_validation": {"valid": True, "issues": []}}
    record.final_review_status = ReviewStatus.APPROVED.value
    record.case_usage = usage.value
    record.evidence_quality = "A"
    session.commit()


def test_regulatory_case_import_is_isolated_from_penalty(session) -> None:
    document, record, _ = _import_case(session)

    assert record.document_id == document.id
    assert record.case_usage == RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE.value
    assert session.query(Penalty).count() == 0


def test_case_title_without_evidence_is_rejected(session) -> None:
    document, _ = _make_document(session)
    envelope = _envelope(document)
    assert envelope.field_evidence is not None
    envelope.field_evidence.pop("case_title")

    with pytest.raises(StructuredRecordError, match="missing_field_evidence"):
        StructuredRecordService(_settings(session)).import_draft(session, envelope)


def test_fabricated_marketing_wording_is_rejected(session) -> None:
    document, _ = _make_document(session)
    envelope = _envelope(document, wording="虚构销售原话")

    with pytest.raises(StructuredRecordError, match="field_not_supported_by_evidence"):
        StructuredRecordService(_settings(session)).import_draft(session, envelope)


def test_undisclosed_marketing_wording_must_remain_empty(session) -> None:
    document, _ = _make_document(session)

    with pytest.raises(StructuredRecordError, match="undisclosed marketing wording"):
        StructuredRecordService(_settings(session)).import_draft(
            session,
            _envelope(document, disclosed=False, wording=WORDING),
        )


def test_disclosed_marketing_wording_with_evidence_validates(session) -> None:
    document, record, _ = _import_case(session)

    result = ValidationService(_settings(session)).validate_document(session, document)

    assert result.valid
    assert record.marketing_wording == WORDING
    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value


def test_normalized_marketing_wording_evidence_validates(session) -> None:
    document, _ = _make_document(session)
    envelope = _envelope(document)
    assert envelope.field_evidence is not None
    wording_evidence = envelope.field_evidence["marketing_wording"][0]
    wording_evidence.mode = "normalized"
    wording_evidence.transformation_note = "whitespace_normalized"

    record = StructuredRecordService(_settings(session)).import_draft(session, envelope)
    result = ValidationService(_settings(session)).validate_document(session, document)

    assert result.valid
    assert record.marketing_wording == WORDING


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [("case_category", "invented_category"), ("case_usage", "invented_usage")],
)
def test_regulatory_case_enums_reject_arbitrary_values(
    field_name: str,
    invalid_value: str,
) -> None:
    values = {
        "case_title": TITLE,
        "case_category": RegulatoryCaseCategory.CONSUMER_RISK_ALERT.value,
        "scenario_text": SCENARIO,
        "marketing_wording_disclosed": False,
        "marketing_wording": None,
        "case_usage": RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE.value,
        "source_quote": TITLE,
    }
    values[field_name] = invalid_value

    with pytest.raises(PydanticValidationError):
        RegulatoryCaseDraft.model_validate(values)


@pytest.mark.parametrize(
    "case_usage",
    [
        RegulatoryCaseUsage.RETRIEVAL_ONLY.value,
        RegulatoryCaseUsage.SEALED_EXTERNAL_TEST.value,
    ],
)
def test_nondefault_case_usage_requires_human_review(session, case_usage: str) -> None:
    document, _ = _make_document(session)

    with pytest.raises(StructuredRecordError, match="case_usage_requires_human_review"):
        StructuredRecordService(_settings(session)).import_draft(
            session,
            _envelope(document, case_usage=case_usage),
        )


def test_publisher_document_metadata_evidence_is_supported(session) -> None:
    document, _ = _make_document(session)
    envelope = _envelope(document)
    assert envelope.field_evidence is not None
    publisher_evidence = envelope.field_evidence["publisher"][0]
    publisher_evidence.mode = "document_metadata"
    publisher_evidence.metadata_field = "publisher"

    record = StructuredRecordService(_settings(session)).import_draft(session, envelope)

    assert isinstance(record, RegulatoryCase)
    assert record.field_evidence_json["publisher"][0]["metadata_field"] == "publisher"


def test_summary_evidence_routes_case_to_expert_review(session) -> None:
    document, record, _ = _import_case(session)
    record.field_evidence_json["scenario_text"][0]["mode"] = "summary"
    session.commit()

    result = ValidationService(_settings(session)).validate_document(session, document)

    assert not result.valid
    assert "summary_evidence_requires_expert_review" in {issue.code for issue in result.issues}
    assert document.final_review_status == ReviewStatus.REQUIRES_EXPERT_REVIEW.value


def test_external_test_candidate_cannot_be_indexed(session) -> None:
    document, record, _ = _import_case(session)
    _approve_directly(
        session,
        document,
        record,
        RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE,
    )

    summary = KnowledgeIndexService(_settings(session)).index_approved(session)

    assert summary.indexed == 0
    assert "external_test_candidate_not_indexable" in summary.rejected[document.id]


def test_sealed_external_test_cannot_be_indexed(session) -> None:
    document, record, _ = _import_case(session)
    _approve_directly(
        session,
        document,
        record,
        RegulatoryCaseUsage.SEALED_EXTERNAL_TEST,
    )

    summary = KnowledgeIndexService(_settings(session)).index_approved(session)

    assert summary.indexed == 0
    assert "sealed_external_test_not_indexable" in summary.rejected[document.id]


def test_human_review_can_make_verified_retrieval_case_indexable(session, tmp_path: Path) -> None:
    service, batch, row, document, record, occurrence = _review_export(session)
    corrections = {
        "records": [
            {
                "structured_record_id": record.id,
                "fields": {"case_usage": RegulatoryCaseUsage.RETRIEVAL_ONLY.value},
                "field_evidence": {},
            }
        ]
    }
    payload = _decision(
        batch,
        row,
        status=ReviewStatus.APPROVED_WITH_REVISION.value,
        corrections=corrections,
        occurrence_id=occurrence.id,
    )

    imported, errors = service.import_results(
        session,
        _write_decision(tmp_path, payload),
        batch.id,
    )
    summary = KnowledgeIndexService(_settings(session)).index_approved(session)

    assert (imported, errors) == (1, [])
    assert record.case_usage == RegulatoryCaseUsage.RETRIEVAL_ONLY.value
    assert record.evidence_quality == "A"
    assert document.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value
    assert session.query(AuthenticityDecisionLog).filter_by(document_id=document.id).count() == 1
    assert summary.indexed == 1
    assert document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
    assert document.metadata_json["knowledge_index_payload"]["record_id"] == record.id


def test_pre_review_fact_revision_requires_matching_evidence(session) -> None:
    document, record, _ = _import_case(session)
    envelope = StructuredDraftRevisionEnvelope(
        document_id=document.id,
        record_type=DataType.REGULATORY_CASE,
        structured_record_id=record.id,
        fields={"scenario_text": SECOND_SCENARIO},
        field_evidence={},
    )

    with pytest.raises(StructuredRecordError, match="revision_evidence_must_match_fields"):
        StructuredDraftRevisionService(_settings(session)).revise(
            session,
            envelope,
            reason="修正场景",
            actor="case_editor",
        )


def test_auto_validation_failed_case_can_be_revised_with_audit(session) -> None:
    document, record, _ = _import_case(session)
    document.final_review_status = ReviewStatus.AUTO_VALIDATION_FAILED.value
    record.final_review_status = ReviewStatus.AUTO_VALIDATION_FAILED.value
    session.commit()
    envelope = StructuredDraftRevisionEnvelope(
        document_id=document.id,
        record_type=DataType.REGULATORY_CASE,
        structured_record_id=record.id,
        fields={"scenario_text": SECOND_SCENARIO},
        field_evidence={"scenario_text": _evidence(SECOND_SCENARIO)},
    )

    revised = StructuredDraftRevisionService(_settings(session)).revise(
        session,
        envelope,
        reason="使用原文中更准确的场景",
        actor="case_editor",
    )

    assert revised.scenario_text == SECOND_SCENARIO
    assert document.final_review_status == ReviewStatus.PARSED.value
    assert (
        session.query(StructuredDraftRevision)
        .filter_by(record_type=DataType.REGULATORY_CASE.value)
        .count()
        == 1
    )


def test_pending_review_case_cannot_use_pre_review_revision(session) -> None:
    document, record, _ = _pending_case(session)
    envelope = StructuredDraftRevisionEnvelope(
        document_id=document.id,
        record_type=DataType.REGULATORY_CASE,
        structured_record_id=record.id,
        fields={"scenario_text": SECOND_SCENARIO},
        field_evidence={"scenario_text": _evidence(SECOND_SCENARIO)},
    )

    with pytest.raises(StructuredRecordError, match="structured_record_locked"):
        StructuredDraftRevisionService(_settings(session)).revise(
            session,
            envelope,
            reason="不应允许",
            actor="case_editor",
        )


def test_human_fact_correction_without_evidence_is_rejected(session, tmp_path: Path) -> None:
    document, record, _ = _pending_case(session)
    document.authenticity_type = AuthenticityType.VERIFIED_PUBLIC.value
    session.commit()
    service = ReviewService(_settings(session))
    batch = service.export_batch(session, DataType.REGULATORY_CASE.value, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8").splitlines()[0])
    payload = _decision(
        batch,
        row,
        status=ReviewStatus.APPROVED_WITH_REVISION.value,
        corrections={
            "records": [
                {
                    "structured_record_id": record.id,
                    "fields": {"scenario_text": SECOND_SCENARIO},
                    "field_evidence": {},
                }
            ]
        },
    )

    imported, errors = service.import_results(
        session,
        _write_decision(tmp_path, payload),
        batch.id,
    )

    assert imported == 0
    assert any("correction_evidence_required" in error for error in errors)
    assert record.scenario_text == SCENARIO


def test_sealed_external_test_cannot_be_reopened_by_review(session, tmp_path: Path) -> None:
    _, record, occurrence = _pending_case(session)
    record.case_usage = RegulatoryCaseUsage.SEALED_EXTERNAL_TEST.value
    session.commit()
    service = ReviewService(_settings(session))
    batch = service.export_batch(session, DataType.REGULATORY_CASE.value, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8").splitlines()[0])
    payload = _decision(
        batch,
        row,
        status=ReviewStatus.APPROVED_WITH_REVISION.value,
        corrections={
            "records": [
                {
                    "structured_record_id": record.id,
                    "fields": {"case_usage": RegulatoryCaseUsage.RETRIEVAL_ONLY.value},
                    "field_evidence": {},
                }
            ]
        },
        occurrence_id=occurrence.id,
    )

    imported, errors = service.import_results(
        session,
        _write_decision(tmp_path, payload),
        batch.id,
    )

    assert imported == 0
    assert any("sealed_external_test_cannot_be_reopened" in error for error in errors)
    assert record.case_usage == RegulatoryCaseUsage.SEALED_EXTERNAL_TEST.value


def test_portable_bundle_contains_case_fields_evidence_and_artifacts(session) -> None:
    document, record, _ = _pending_case(session)
    service = ReviewService(_settings(session))

    batch, bundle_path = service.export_bundle(session, DataType.REGULATORY_CASE.value)

    with zipfile.ZipFile(bundle_path) as bundle:
        names = set(bundle.namelist())
        row = json.loads(bundle.read("review.jsonl").decode().splitlines()[0])
        parsed_record = row["parsed_fields"]["records"][0]
        assert {"manifest.json", "review.jsonl", "review-results-template.jsonl"} <= names
        assert any(name.startswith("sources/") for name in names)
        assert any(name.startswith("parsed/") for name in names)
        assert parsed_record["structured_record_id"] == record.id
        assert parsed_record["case_title"] == TITLE
        assert parsed_record["field_evidence"]["scenario_text"]
        assert row["source_occurrences"]
        assert batch.bundle_sha256
        assert any(name.startswith("sources/") and document.sha256 in name for name in names)


def test_tampered_regulatory_case_bundle_is_rejected_on_import(session, tmp_path: Path) -> None:
    _, _, _ = _pending_case(session)
    service = ReviewService(_settings(session))
    batch, bundle_path = service.export_bundle(session, DataType.REGULATORY_CASE.value)
    result_path = service.create_result_template(session, batch.id)
    with zipfile.ZipFile(bundle_path, "a") as bundle:
        bundle.writestr("tampered.txt", "tampered")

    with pytest.raises(ReviewDecisionError, match="review_package_tampered"):
        service.import_results(session, result_path, batch.id)


def test_regulatory_case_api_list_filters_and_returns_evidence(session) -> None:
    document, record, _ = _import_case(session)

    rows = list_regulatory_cases(
        case_category=RegulatoryCaseCategory.CONSUMER_RISK_ALERT,
        case_usage=RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE,
        review_status=ReviewStatus.PARSED.value,
        authenticity_type=AuthenticityType.PENDING_VERIFICATION,
        session=session,
    )
    single = get_regulatory_case(record.id, session)

    assert len(rows) == 1
    assert rows[0]["document_id"] == document.id
    assert single["field_evidence"]["case_title"]
    assert single["can_index"] is False


def test_database_rejects_wording_when_disclosure_is_false(session) -> None:
    document, _, _ = _import_case(session)
    record = session.query(RegulatoryCase).filter_by(document_id=document.id).one()
    record.marketing_wording_disclosed = False
    record.marketing_wording = WORDING

    with pytest.raises(IntegrityError):
        session.commit()
