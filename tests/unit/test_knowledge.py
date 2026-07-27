from app.models import SourceDocument
from app.models.enums import AuthenticityType, DataType, ReviewStatus
from app.services.knowledge import KnowledgeIndexService


def make_document(status: str, authenticity: str = AuthenticityType.VERIFIED_PUBLIC.value):
    return SourceDocument(
        data_type=DataType.REGULATION.value,
        source_url="https://example.test/rule",
        raw_file_path="/tmp/rule.txt",
        raw_text="public rule",
        sha256=status.encode().hex().ljust(64, "0")[:64],
        authenticity_type=authenticity,
        final_review_status=status,
    )


def test_unreviewed_data_cannot_be_indexed(session) -> None:
    document = make_document(ReviewStatus.PENDING_REVIEW.value)
    session.add(document)
    session.commit()

    count = KnowledgeIndexService().index_approved(session)

    assert count == 0
    assert document.knowledge_index_status == "not_indexed"


def test_approved_data_can_be_indexed(session) -> None:
    document = make_document(ReviewStatus.APPROVED.value)
    session.add(document)
    session.commit()

    count = KnowledgeIndexService().index_approved(session)

    assert count == 1
    assert document.knowledge_index_status == "indexed"
    assert document.indexed_at is not None


def test_demo_data_cannot_be_indexed_even_if_approved(session) -> None:
    document = make_document(
        ReviewStatus.APPROVED.value, authenticity=AuthenticityType.DEMO_ONLY.value
    )
    session.add(document)
    session.commit()

    assert KnowledgeIndexService().index_approved(session) == 0
