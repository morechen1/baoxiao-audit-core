import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import ReviewDecisionError
from app.models import (
    Penalty,
    ProductDocument,
    ReviewBatchItem,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import AuthenticityType, DataType, ReviewStatus
from app.services.review import ReviewService


def setup_product(session, *, suffix: str = "a"):
    document = SourceDocument(
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url=f"https://example.test/product/{suffix}",
        final_url=f"https://example.test/product/{suffix}",
        raw_file_path=f"/tmp/product-{suffix}.txt",
        raw_text="演示产品，等待期三十日。",
        sha256=suffix * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
        metadata_json={
            "automatic_validation": {"valid": True, "issues": []},
            "parsing": {"warnings": []},
        },
    )
    session.add(document)
    session.flush()
    product = ProductDocument(
        document_id=document.id,
        product_name="演示产品",
        waiting_period="待确认",
        source_quote="演示产品",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(product)
    session.commit()
    return document, product


def setup_penalty(session):
    document = SourceDocument(
        data_type=DataType.PENALTY.value,
        source_url="https://example.test/penalty",
        raw_file_path="/tmp/penalty.txt",
        raw_text="演示违法事实，未披露原始话术。",
        sha256="b" * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
        metadata_json={"automatic_validation": {"valid": True, "issues": []}},
    )
    session.add(document)
    session.flush()
    penalty = Penalty(
        document_id=document.id,
        illegal_facts="演示违法事实",
        original_sales_wording_disclosed=False,
        original_sales_wording=None,
        source_quote="演示违法事实",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(penalty)
    session.commit()
    return document, penalty


def export_one(session, tmp_path: Path, data_type: str):
    service = ReviewService(Settings(database_url="sqlite://", data_dir=tmp_path))
    batch = service.export_batch(session, data_type, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8").splitlines()[0])
    return service, batch, row


def decision_payload(row, *, status=ReviewStatus.APPROVED.value, corrections=None):
    return {
        "batch_id": row["batch_id"],
        "batch_item_id": row["batch_item_id"],
        "record_id": row["record_id"],
        "record_type": row["record_type"],
        "final_status": status,
        "field_reviews": {},
        "corrections": corrections or {},
        "evidence_quality": "A",
        "review_comment": "已核对",
        "reviewer": "reviewer",
    }


def test_review_batch_contains_real_fields_and_full_text(session, tmp_path: Path) -> None:
    document, _ = setup_product(session)

    _, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    assert row["batch_id"] == batch.id
    assert row["document_id"] == document.id
    assert row["parsed_fields"]["product_name"] == "演示产品"
    assert row["raw_text"] == document.raw_text
    assert row["raw_text_file"] == document.raw_file_path
    assert batch.export_sha256
    assert batch.schema_version == "2.0"


def test_record_not_in_batch_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session, suffix="a")
    other, _ = setup_product(session, suffix="c")
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    payload["record_id"] = other.id

    with pytest.raises(ReviewDecisionError, match="does not match"):
        service.apply_decision(session, payload, batch.id)


def test_duplicate_batch_item_decision_is_rejected(session, tmp_path: Path) -> None:
    document, product = setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    service.apply_decision(session, payload, batch.id)

    assert document.final_review_status == ReviewStatus.APPROVED.value
    assert product.final_review_status == ReviewStatus.APPROVED.value
    assert batch.status == "completed"
    assert batch.completed_at is not None

    with pytest.raises(ReviewDecisionError, match="already has a decision"):
        service.apply_decision(session, payload, batch.id)


def test_payload_hash_change_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    item = session.get(ReviewBatchItem, row["batch_item_id"])
    item.payload_hash = "0" * 64
    session.commit()

    with pytest.raises(ReviewDecisionError, match="payload changed"):
        service.apply_decision(session, decision_payload(row), batch.id)


@pytest.mark.parametrize("field", ["source_quote", "raw_text", "sha256"])
def test_protected_correction_fields_are_rejected(session, tmp_path: Path, field: str) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    with pytest.raises(ReviewDecisionError, match="Protected correction"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={field: "forbidden"},
            ),
            batch.id,
        )


def test_unknown_correction_field_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    with pytest.raises(ReviewDecisionError, match="Unknown correction"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={"made_up": "value"},
            ),
            batch.id,
        )


def test_illegal_penalty_wording_correction_rolls_back(session, tmp_path: Path) -> None:
    document, penalty = setup_penalty(session)
    service, batch, row = export_one(session, tmp_path, DataType.PENALTY.value)

    with pytest.raises(ReviewDecisionError):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={"original_sales_wording": "虚构话术"},
            ),
            batch.id,
        )

    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value
    assert penalty.original_sales_wording is None
    assert session.query(ReviewDecision).count() == 0


def test_failed_post_correction_validation_rolls_back(session, tmp_path: Path) -> None:
    document, product = setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    with pytest.raises(ReviewDecisionError, match="failed deterministic"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={"product_name": ""},
            ),
            batch.id,
        )

    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value
    assert product.product_name == "演示产品"
    assert session.query(ReviewDecision).count() == 0
