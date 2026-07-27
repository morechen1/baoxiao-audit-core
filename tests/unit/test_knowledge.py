from app.models import ProductDocument, SourceDocument
from app.models.enums import (
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    ReviewStatus,
)
from app.services.knowledge import KnowledgeIndexService


def make_document(
    status: str,
    *,
    authenticity: str = AuthenticityType.VERIFIED_PUBLIC.value,
) -> tuple[SourceDocument, ProductDocument]:
    raw_text = "演示产品名称，等待期三十日。"
    document = SourceDocument(
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url="https://example.test/product",
        raw_file_path="/tmp/product.txt",
        raw_text=raw_text,
        sha256="a" * 64,
        authenticity_type=authenticity,
        parse_status="parsed",
        final_review_status=status,
        metadata_json={"automatic_validation": {"valid": True, "issues": []}},
    )
    product = ProductDocument(
        document_id=0,
        product_name="演示产品",
        source_quote="演示产品名称",
        final_review_status=status,
    )
    return document, product


def persist_pair(session, status: str, *, authenticity: str):
    document, product = make_document(status, authenticity=authenticity)
    session.add(document)
    session.flush()
    product.document_id = document.id
    session.add(product)
    session.commit()
    return document, product


def test_unreviewed_data_cannot_be_indexed(session) -> None:
    document, _ = persist_pair(
        session,
        ReviewStatus.PENDING_REVIEW.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )

    summary = KnowledgeIndexService().index_approved(session)

    assert summary.indexed == 0
    assert "review_status_not_approved" in summary.rejected[document.id]


def test_indexing_keeps_review_status_and_sets_index_fields(session) -> None:
    document, _ = persist_pair(
        session,
        ReviewStatus.APPROVED.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )

    summary = KnowledgeIndexService().index_approved(session)

    assert summary.indexed == 1
    assert document.final_review_status == ReviewStatus.APPROVED.value
    assert document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
    assert document.indexed_at is not None


def test_only_verified_public_can_be_indexed(session) -> None:
    document, _ = persist_pair(
        session,
        ReviewStatus.APPROVED.value,
        authenticity=AuthenticityType.DEMO_ONLY.value,
    )

    summary = KnowledgeIndexService().index_approved(session)

    assert summary.indexed == 0
    assert summary.rejected[document.id] == ["authenticity_not_verified_public"]


def test_constructed_document_cannot_be_indexed(session) -> None:
    document, _ = persist_pair(
        session,
        ReviewStatus.APPROVED.value,
        authenticity=AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value,
    )

    summary = KnowledgeIndexService().index_approved(session)

    assert summary.indexed == 0
    assert "authenticity_not_verified_public" in summary.rejected[document.id]


def test_structured_status_mismatch_blocks_indexing(session) -> None:
    document, product = persist_pair(
        session,
        ReviewStatus.APPROVED.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )
    product.final_review_status = ReviewStatus.PENDING_REVIEW.value
    session.commit()

    summary = KnowledgeIndexService().index_approved(session)

    assert summary.indexed == 0
    assert "structured_status_mismatch" in summary.rejected[document.id]
