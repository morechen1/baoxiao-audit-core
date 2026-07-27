import pytest

from app.models import ProductDocument, SourceDocument
from app.models.enums import AuthenticityType, DataType, ReviewStatus
from app.services.review import ReviewService


def setup_product(session):
    document = SourceDocument(
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url="https://example.test/product",
        raw_file_path="/tmp/product.txt",
        raw_text="等待期三十日",
        sha256="b" * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(document)
    session.flush()
    product = ProductDocument(
        document_id=document.id,
        product_name="演示产品",
        waiting_period="待确认",
        source_quote="等待期三十日",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(product)
    session.commit()
    return document, product


def test_approved_with_revision_applies_structured_corrections(session) -> None:
    document, product = setup_product(session)
    ReviewService().apply_decision(
        session,
        {
            "record_id": document.id,
            "record_type": DataType.PRODUCT_DOCUMENT.value,
            "final_status": ReviewStatus.APPROVED_WITH_REVISION.value,
            "field_reviews": {"waiting_period": "corrected"},
            "corrections": {"waiting_period": "三十日"},
            "evidence_quality": "A",
            "review_comment": "已核对原文",
            "reviewer": "chatgpt_review",
        },
    )

    assert product.waiting_period == "三十日"
    assert document.corrected_fields_json["waiting_period"] == "三十日"
    assert document.raw_text == "等待期三十日"


def test_illegal_review_status_is_rejected(session) -> None:
    document, _ = setup_product(session)

    with pytest.raises(ValueError, match="Illegal final_status"):
        ReviewService().apply_decision(
            session,
            {
                "record_id": document.id,
                "record_type": DataType.PRODUCT_DOCUMENT.value,
                "final_status": "made_up_status",
                "evidence_quality": "A",
                "reviewer": "reviewer",
            },
        )
