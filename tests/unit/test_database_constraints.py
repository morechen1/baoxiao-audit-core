import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    DataSource,
    DocumentChunk,
    EvaluationSample,
    Penalty,
    ReviewBatch,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import AuthenticityType, DataType, ReviewStatus


def test_sqlite_foreign_keys_are_enforced(session) -> None:
    session.add(
        DocumentChunk(
            document_id=999999,
            page_number=1,
            chunk_index=0,
            text="orphan",
            start_offset=0,
            end_offset=6,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_penalty_wording_check_constraint(session) -> None:
    document = SourceDocument(
        data_type=DataType.PENALTY.value,
        source_url="https://example.test/penalty",
        raw_file_path="/tmp/penalty.txt",
        sha256="f" * 64,
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(document)
    session.flush()
    session.add(
        Penalty(
            document_id=document.id,
            source_entry_index=1,
            source_entry_fingerprint="2" * 64,
            illegal_facts="演示",
            source_quote="演示",
            original_sales_wording_disclosed=False,
            original_sales_wording="非法补写",
            final_review_status=ReviewStatus.PENDING_REVIEW.value,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_invalid_review_status_is_rejected_by_database(session) -> None:
    session.add(
        SourceDocument(
            data_type=DataType.REGULATION.value,
            source_url="https://example.test/rule",
            raw_file_path="/tmp/rule.txt",
            sha256="1" * 64,
            final_review_status="invalid_status",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_category", "invalid"),
        ("split", "invalid"),
        ("authenticity_type", AuthenticityType.VERIFIED_PUBLIC.value),
    ],
)
def test_invalid_evaluation_domains_are_rejected_by_database(
    session, field: str, value: str
) -> None:
    values = {
        "sample_text": "评测样本",
        "sample_category": "risky",
        "risk_labels": ["风险"],
        "expected_evidence": {},
        "construction_basis": "人工构造",
        "authenticity_type": AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value,
        "split": "train",
        "final_review_status": ReviewStatus.PENDING_REVIEW.value,
    }
    values[field] = value
    session.add(EvaluationSample(**values))

    with pytest.raises(IntegrityError):
        session.commit()


def test_review_decision_rejects_non_human_status(session) -> None:
    batch = ReviewBatch(
        batch_name="batch",
        data_type=DataType.REGULATION.value,
        record_count=1,
        export_path="/tmp/batch.jsonl",
        export_sha256="a" * 64,
        schema_version="2.0",
        status="exported",
    )
    session.add(batch)
    session.flush()
    session.add(
        ReviewDecision(
            batch_id=batch.id,
            record_type=DataType.REGULATION.value,
            record_id=1,
            decision=ReviewStatus.PENDING_REVIEW.value,
            field_reviews_json={},
            corrections_json={},
            evidence_quality="A",
            reviewer="reviewer",
            reviewed_payload_hash="b" * 64,
            schema_version="2.0",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_data_source_rejects_evaluation_sample_type(session) -> None:
    session.add(
        DataSource(
            name="非法评测来源",
            base_url="https://example.test",
            source_type=DataType.EVALUATION_SAMPLE.value,
            enabled=True,
            crawl_policy={},
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_source_document_rejects_evaluation_sample_type(session) -> None:
    session.add(
        SourceDocument(
            data_type=DataType.EVALUATION_SAMPLE.value,
            source_url="https://example.test/evaluation",
            raw_file_path="/tmp/evaluation.txt",
            sha256="8" * 64,
            final_review_status=ReviewStatus.COLLECTED.value,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()
