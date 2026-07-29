import hashlib
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.exceptions import StructuredRecordError
from app.models import (
    Penalty,
    PilotCollectionItem,
    PilotCollectionRun,
    PilotSourceRegistration,
    Regulation,
    SourceDocument,
)
from app.models.enums import (
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    ReviewStatus,
)
from app.schemas.structured import StructuredDraftEnvelope
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.penalty_entries import (
    exact_source_entry_text_sha256,
    penalty_source_entry_fingerprint,
)
from app.services.review import ReviewService
from app.services.structured_records import StructuredRecordService
from app.services.validation import ValidationService


def parsed_document(session, data_type: str, *, raw_text: str) -> SourceDocument:
    document = SourceDocument(
        data_type=data_type,
        source_url="https://example.test/document",
        raw_file_path="/tmp/document.txt",
        raw_text=raw_text,
        sha256="e" * 64,
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PARSED.value,
    )
    session.add(document)
    session.flush()
    raw_dir = session.info["data_dir"] / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    content = f"{raw_text}\nartifact-id={document.id}".encode()
    digest = hashlib.sha256(content).hexdigest()
    raw_path = raw_dir / f"{digest}.txt"
    raw_path.write_bytes(content)
    document.raw_file_path = str(raw_path)
    document.sha256 = digest
    ParsedArtifactService(Settings(data_dir=session.info["data_dir"])).persist(
        session,
        document,
        ParsedDocument(
            title="测试文档",
            plain_text=raw_text,
            pages=[ParsedPage(page_number=1, text=raw_text)],
        ),
        parser_name="TestParser",
    )
    session.commit()
    return document


def structured_service(session) -> StructuredRecordService:
    return StructuredRecordService(Settings(data_dir=session.info["data_dir"]))


def penalty_entry_identity(
    document: SourceDocument,
    *,
    index: int = 1,
    exact_text: str = "演示违法事实",
) -> tuple[dict[str, object], dict[str, object]]:
    locator = {"table_index": 1, "row_index": index}
    text_sha256 = exact_source_entry_text_sha256(exact_text)
    fingerprint = penalty_source_entry_fingerprint(
        raw_artifact_sha256=document.sha256,
        source_entry_index=index,
        stable_source_locator=locator,
        exact_source_entry_text_sha256=text_sha256,
    )
    return (
        {
            "source_entry_locator": locator,
            "source_entry_text": exact_text,
            "source_entry_text_sha256": text_sha256,
        },
        {
            "source_entry_index": index,
            "source_entry_fingerprint": fingerprint,
        },
    )


def attach_pilot_item(
    session,
    document: SourceDocument,
    pilot_id: str,
) -> PilotCollectionItem:
    now = datetime.now(UTC)
    source_key = f"test-{uuid4().hex[:12]}"
    registration = PilotSourceRegistration(
        source_key=source_key,
        version=1,
        entry_sha256="a" * 64,
        name="测试公开来源",
        publisher="测试发布机构",
        base_url="https://example.test",
        source_type=document.data_type,
        allowed_domains_json=["example.test"],
        allow_subdomains=False,
        rate_limit_seconds=1.0,
        max_documents=20,
        confirmed_by="tester",
        approved_at=now,
        approval_reference="test-approval",
        robots_review_status="allowed",
        robots_checked_at=date.today(),
        robots_checked_by="tester",
        robots_notes="test",
        terms_review_status="public_access_allowed",
        terms_checked_at=date.today(),
        terms_checked_by="tester",
        terms_notes="test",
        notes="test",
    )
    run = PilotCollectionRun(
        run_uuid=str(uuid4()),
        selected_manifest="tests.jsonl",
        manifest_set_sha256="b" * 64,
        source_registry_set_sha256="c" * 64,
        status="completed",
        requested_by="tester",
        planned_count=1,
        success_count=1,
        failure_count=0,
        skipped_count=0,
    )
    session.add_all([registration, run])
    session.flush()
    item = PilotCollectionItem(
        run_id=run.id,
        source_registration_id=registration.id,
        pilot_id=pilot_id,
        source_key=source_key,
        source_type=document.data_type,
        source_url=document.source_url or "https://example.test/document",
        expected_title="测试文档",
        evaluation_usage_json=[],
        confirmed_by="tester",
        approval_reference="test-approval",
        approved_at=now,
        manifest_entry_sha256="d" * 64,
        source_registry_entry_sha256=registration.entry_sha256,
        status="collected",
        document_id=document.id,
        document_created=True,
        attempt_count=1,
        collected_at=now,
    )
    session.add(item)
    session.commit()
    return item


def test_missing_structured_record_fails_validation(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")

    result = ValidationService(Settings(data_dir=session.info["data_dir"])).validate_document(
        session, document
    )

    assert result.valid is False
    assert "missing_structured_record" in {issue.code for issue in result.issues}
    assert document.final_review_status == ReviewStatus.AUTO_VALIDATION_FAILED.value


def test_import_structured_penalty_draft(session, tmp_path: Path) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    attach_pilot_item(session, document, "PEN-001")
    envelope_identity, field_identity = penalty_entry_identity(document)
    path = tmp_path / "drafts.jsonl"
    path.write_text(
        json.dumps(
            {
                "pilot_id": "PEN-001",
                "document_id": document.id,
                "record_type": "penalty",
                "draft_generation_method": "manual_rules_from_official_text",
                "draft_generation_version": "first-batch-v1",
                **envelope_identity,
                "fields": {
                    **field_identity,
                    "illegal_facts": "演示违法事实",
                    "original_sales_wording_disclosed": False,
                    "original_sales_wording": None,
                    "source_quote": "演示违法事实",
                },
                "field_evidence": {
                    "illegal_facts": [
                        {
                            "quote": "演示违法事实",
                            "page_number": 1,
                            "start_offset": 0,
                            "end_offset": 6,
                            "mode": "verbatim",
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    imported, errors = structured_service(session).import_jsonl(session, path)

    assert imported == 1
    assert errors == []
    record = session.query(Penalty).filter_by(document_id=document.id).one()
    assert document.metadata_json["structured_draft_provenance"] == [
        {
            "structured_record_id": record.id,
            "record_type": DataType.PENALTY.value,
            "pilot_id": "PEN-001",
            "draft_generation_method": "manual_rules_from_official_text",
            "draft_generation_version": "first-batch-v1",
            **envelope_identity,
        }
    ]


def test_pilot_id_for_another_document_is_rejected_atomically(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    other = parsed_document(session, DataType.PENALTY.value, raw_text="其他违法事实")
    attach_pilot_item(session, document, "PEN-001")
    attach_pilot_item(session, other, "PEN-002")
    original_metadata = dict(document.metadata_json)
    envelope_identity, field_identity = penalty_entry_identity(document)
    envelope = StructuredDraftEnvelope(
        pilot_id="PEN-002",
        document_id=document.id,
        record_type=DataType.PENALTY,
        draft_generation_method="manual_rules_from_official_text",
        draft_generation_version="first-batch-v1",
        **envelope_identity,
        fields={
            **field_identity,
            "illegal_facts": "演示违法事实",
            "original_sales_wording_disclosed": False,
            "source_quote": "演示违法事实",
        },
        field_evidence={
            "illegal_facts": [
                {
                    "quote": "演示违法事实",
                    "page_number": 1,
                    "start_offset": 0,
                    "end_offset": 6,
                    "mode": "verbatim",
                }
            ]
        },
    )

    with pytest.raises(StructuredRecordError, match="pilot_id_document_mismatch"):
        structured_service(session).import_draft(session, envelope)

    assert session.query(Penalty).filter_by(document_id=document.id).count() == 0
    assert document.final_review_status == ReviewStatus.PARSED.value
    assert document.metadata_json == original_metadata


def test_pilot_document_requires_generation_provenance(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    attach_pilot_item(session, document, "PEN-001")
    envelope_identity, field_identity = penalty_entry_identity(document)
    envelope = StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        **envelope_identity,
        fields={
            **field_identity,
            "illegal_facts": "演示违法事实",
            "original_sales_wording_disclosed": False,
            "source_quote": "演示违法事实",
        },
        field_evidence={
            "illegal_facts": [
                {
                    "quote": "演示违法事实",
                    "page_number": 1,
                    "start_offset": 0,
                    "end_offset": 6,
                    "mode": "verbatim",
                }
            ]
        },
    )

    with pytest.raises(StructuredRecordError, match="pilot_id_document_mismatch"):
        structured_service(session).import_draft(session, envelope)

    assert session.query(Penalty).filter_by(document_id=document.id).count() == 0
    assert document.final_review_status == ReviewStatus.PARSED.value


def test_draft_provenance_is_exported_in_portable_review_bundle(session, tmp_path: Path) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    attach_pilot_item(session, document, "PEN-001")
    envelope_identity, field_identity = penalty_entry_identity(document)
    envelope = StructuredDraftEnvelope(
        pilot_id="PEN-001",
        document_id=document.id,
        record_type=DataType.PENALTY,
        draft_generation_method="manual_rules_from_official_text",
        draft_generation_version="first-batch-v1",
        **envelope_identity,
        fields={
            **field_identity,
            "illegal_facts": "演示违法事实",
            "original_sales_wording_disclosed": False,
            "source_quote": "演示违法事实",
        },
        field_evidence={
            "illegal_facts": [
                {
                    "quote": "演示违法事实",
                    "page_number": 1,
                    "start_offset": 0,
                    "end_offset": 6,
                    "mode": "verbatim",
                }
            ]
        },
    )
    structured_service(session).import_draft(session, envelope)
    ValidationService(Settings(data_dir=session.info["data_dir"])).validate_document(
        session, document
    )

    _, bundle_path = ReviewService(Settings(data_dir=session.info["data_dir"])).export_bundle(
        session, DataType.PENALTY.value
    )

    with zipfile.ZipFile(bundle_path) as bundle:
        review = json.loads(bundle.read("review.jsonl"))
    assert review["pilot_id"] == "PEN-001"
    assert review["pilot_ids"] == ["PEN-001"]
    assert review["parsed_fields"]["records"][0]["draft_provenance"] == {
        "pilot_id": "PEN-001",
        "draft_generation_method": "manual_rules_from_official_text",
        "draft_generation_version": "first-batch-v1",
        **envelope_identity,
    }


def test_multiple_regulations_preserve_independent_provenance(session) -> None:
    raw_text = "演示规则\n第一条 内容甲\n第二条 内容乙"
    document = parsed_document(session, DataType.REGULATION.value, raw_text=raw_text)
    attach_pilot_item(session, document, "REG-001")
    service = structured_service(session)
    records = []
    envelopes = []
    for article_number, article_text in (
        ("第一条", "内容甲"),
        ("第二条", "内容乙"),
    ):
        envelope = StructuredDraftEnvelope(
            pilot_id="REG-001",
            document_id=document.id,
            record_type=DataType.REGULATION,
            draft_generation_method="manual_rules_from_official_text",
            draft_generation_version="first-batch-v1",
            fields={
                "title": "演示规则",
                "article_number": article_number,
                "article_text": article_text,
                "source_quote": article_text,
            },
            field_evidence={
                "title": [
                    {
                        "quote": "演示规则",
                        "page_number": 1,
                        "start_offset": 0,
                        "end_offset": 4,
                        "mode": "verbatim",
                    }
                ],
                "article_number": [
                    {
                        "quote": article_number,
                        "page_number": 1,
                        "start_offset": raw_text.index(article_number),
                        "end_offset": raw_text.index(article_number) + len(article_number),
                        "mode": "verbatim",
                    }
                ],
                "article_text": [
                    {
                        "quote": article_text,
                        "page_number": 1,
                        "start_offset": raw_text.index(article_text),
                        "end_offset": raw_text.index(article_text) + len(article_text),
                        "mode": "verbatim",
                    }
                ],
            },
        )
        envelopes.append(envelope)
        records.append(service.import_draft(session, envelope))

    provenance = document.metadata_json["structured_draft_provenance"]
    assert len(provenance) == 2
    assert {item["structured_record_id"] for item in provenance} == {
        record.id for record in records
    }
    assert {item["pilot_id"] for item in provenance} == {"REG-001"}
    with pytest.raises(StructuredRecordError, match="structured_draft_duplicate"):
        service.import_draft(session, envelopes[0])
    assert len(document.metadata_json["structured_draft_provenance"]) == 2


def test_structured_draft_generation_provenance_must_be_complete() -> None:
    with pytest.raises(ValueError, match="draft generation provenance must be complete"):
        StructuredDraftEnvelope(
            pilot_id="PEN-001",
            document_id=1,
            record_type=DataType.PENALTY,
            fields={},
        )


def test_record_type_must_match_document(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    envelope = StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PRODUCT_DOCUMENT,
        fields={"product_name": "演示", "source_quote": "演示违法事实"},
    )

    with pytest.raises(StructuredRecordError, match="does not match"):
        structured_service(session).import_draft(session, envelope)


def test_empty_structured_record_cannot_bypass_validation(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    envelope_identity, field_identity = penalty_entry_identity(document)
    envelope = StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        **envelope_identity,
        fields={
            **field_identity,
            "illegal_facts": "",
            "source_quote": "",
            "original_sales_wording_disclosed": False,
        },
        field_evidence={
            "illegal_facts": [
                {
                    "quote": "演示违法事实",
                    "page_number": 1,
                    "start_offset": 0,
                    "end_offset": 6,
                    "mode": "verbatim",
                }
            ]
        },
    )

    with pytest.raises(StructuredRecordError):
        structured_service(session).import_draft(session, envelope)


def test_penalty_source_entry_identity_is_unique(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    envelope_identity, field_identity = penalty_entry_identity(document)
    envelope = StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        **envelope_identity,
        fields={
            **field_identity,
            "illegal_facts": "演示违法事实",
            "source_quote": "演示违法事实",
            "original_sales_wording_disclosed": False,
        },
        field_evidence={
            "illegal_facts": [
                {
                    "quote": "演示违法事实",
                    "page_number": 1,
                    "start_offset": 0,
                    "end_offset": 6,
                    "mode": "verbatim",
                }
            ]
        },
    )
    service = structured_service(session)
    service.import_draft(session, envelope)

    with pytest.raises(StructuredRecordError, match="penalty_source_entry_duplicate"):
        service.import_draft(session, envelope)


def regulation_envelope(document: SourceDocument) -> StructuredDraftEnvelope:
    return StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.REGULATION,
        fields={
            "title": "演示规则",
            "article_text": "第一条 演示规则内容",
            "source_quote": "演示规则内容",
        },
        field_evidence={
            "title": [
                {
                    "quote": "演示规则",
                    "page_number": 1,
                    "start_offset": 4,
                    "end_offset": 8,
                    "mode": "verbatim",
                }
            ],
            "article_text": [
                {
                    "quote": "第一条 演示规则内容",
                    "page_number": 1,
                    "start_offset": 0,
                    "end_offset": 10,
                    "mode": "verbatim",
                }
            ],
        },
    )


def assert_structured_record_locked(session, status: str) -> None:
    document = parsed_document(
        session,
        DataType.REGULATION.value,
        raw_text="第一条 演示规则内容",
    )
    document.final_review_status = status
    session.commit()

    with pytest.raises(StructuredRecordError, match="structured_record_locked"):
        structured_service(session).import_draft(
            session,
            regulation_envelope(document),
        )
    assert session.query(Regulation).filter_by(document_id=document.id).count() == 0


def test_pending_review_document_cannot_import_structured_draft(session) -> None:
    assert_structured_record_locked(session, ReviewStatus.PENDING_REVIEW.value)


def test_approved_document_cannot_import_structured_draft(session) -> None:
    assert_structured_record_locked(session, ReviewStatus.APPROVED.value)


def test_approved_with_revision_document_cannot_import_structured_draft(session) -> None:
    assert_structured_record_locked(
        session,
        ReviewStatus.APPROVED_WITH_REVISION.value,
    )


def test_rejected_document_cannot_import_structured_draft(session) -> None:
    assert_structured_record_locked(session, ReviewStatus.REJECTED.value)


def test_indexed_regulation_cannot_add_article(session) -> None:
    document = parsed_document(
        session,
        DataType.REGULATION.value,
        raw_text="第一条 演示规则内容",
    )
    document.final_review_status = ReviewStatus.APPROVED.value
    document.knowledge_index_status = KnowledgeIndexStatus.INDEXED.value
    session.commit()

    with pytest.raises(StructuredRecordError, match="structured_record_locked"):
        structured_service(session).import_draft(
            session,
            regulation_envelope(document),
        )


def test_auto_validation_failed_draft_returns_document_to_parsed(session) -> None:
    document = parsed_document(
        session,
        DataType.REGULATION.value,
        raw_text="第一条 演示规则内容",
    )
    document.final_review_status = ReviewStatus.AUTO_VALIDATION_FAILED.value
    session.commit()

    record = structured_service(session).import_draft(
        session,
        regulation_envelope(document),
    )

    assert record.final_review_status == ReviewStatus.PARSED.value
    assert document.final_review_status == ReviewStatus.PARSED.value
