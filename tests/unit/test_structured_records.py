import hashlib
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import StructuredRecordError
from app.models import Penalty, Regulation, SourceDocument
from app.models.enums import (
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    ReviewStatus,
)
from app.schemas.structured import StructuredDraftEnvelope
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
    session.commit()
    return document


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
    path = tmp_path / "drafts.jsonl"
    path.write_text(
        json.dumps(
            {
                "document_id": document.id,
                "record_type": "penalty",
                "fields": {
                    "illegal_facts": "演示违法事实",
                    "original_sales_wording_disclosed": False,
                    "original_sales_wording": None,
                    "source_quote": "演示违法事实",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    imported, errors = StructuredRecordService().import_jsonl(session, path)

    assert imported == 1
    assert errors == []
    assert session.query(Penalty).filter_by(document_id=document.id).count() == 1


def test_record_type_must_match_document(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    envelope = StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PRODUCT_DOCUMENT,
        fields={"product_name": "演示", "source_quote": "演示违法事实"},
    )

    with pytest.raises(StructuredRecordError, match="does not match"):
        StructuredRecordService().import_draft(session, envelope)


def test_empty_structured_record_cannot_bypass_validation(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    envelope = StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        fields={
            "illegal_facts": "",
            "source_quote": "",
            "original_sales_wording_disclosed": False,
        },
    )

    with pytest.raises(StructuredRecordError):
        StructuredRecordService().import_draft(session, envelope)


def test_penalty_primary_record_is_unique(session) -> None:
    document = parsed_document(session, DataType.PENALTY.value, raw_text="演示违法事实")
    envelope = StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        fields={
            "illegal_facts": "演示违法事实",
            "source_quote": "演示违法事实",
            "original_sales_wording_disclosed": False,
        },
    )
    service = StructuredRecordService()
    service.import_draft(session, envelope)

    with pytest.raises(StructuredRecordError, match="already has"):
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
        StructuredRecordService().import_draft(
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
        StructuredRecordService().import_draft(
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

    record = StructuredRecordService().import_draft(
        session,
        regulation_envelope(document),
    )

    assert record.final_review_status == ReviewStatus.PARSED.value
    assert document.final_review_status == ReviewStatus.PARSED.value
