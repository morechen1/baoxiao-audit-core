import pytest
from sqlalchemy.exc import IntegrityError

from app.models import DocumentChunk, Penalty, SourceDocument
from app.models.enums import DataType, ReviewStatus


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
