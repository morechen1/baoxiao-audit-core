import pytest

from app.core.exceptions import InvalidStateTransition
from app.models import ProductDocument, SourceDocument, StatusHistory
from app.models.enums import DataType, ReviewStatus
from app.services.state_machine import StateMachineService


def document_in_status(status: str) -> SourceDocument:
    return SourceDocument(
        data_type=DataType.REGULATION.value,
        source_url="https://example.test/rule",
        raw_file_path="/tmp/rule.txt",
        sha256="d" * 64,
        final_review_status=status,
    )


@pytest.mark.parametrize(
    "status",
    [
        ReviewStatus.COLLECTED.value,
        ReviewStatus.PARSED.value,
        ReviewStatus.AUTO_VALIDATION_FAILED.value,
        ReviewStatus.REJECTED.value,
        ReviewStatus.APPROVED.value,
        ReviewStatus.APPROVED_WITH_REVISION.value,
    ],
)
def test_non_pending_record_cannot_be_directly_approved(session, status: str) -> None:
    document = document_in_status(status)
    session.add(document)
    session.commit()

    with pytest.raises(InvalidStateTransition):
        StateMachineService.transition_document(
            session, document, ReviewStatus.APPROVED.value, "illegal approval"
        )

    session.rollback()
    assert document.final_review_status == status


def test_allowed_foundation_transitions(session) -> None:
    document = document_in_status(ReviewStatus.COLLECTED.value)
    session.add(document)
    session.commit()

    StateMachineService.transition_document(session, document, ReviewStatus.PARSED.value, "parsed")
    StateMachineService.transition_document(
        session, document, ReviewStatus.PENDING_REVIEW.value, "validated"
    )
    StateMachineService.transition_document(
        session, document, ReviewStatus.APPROVED.value, "human review"
    )

    assert document.final_review_status == ReviewStatus.APPROVED.value


def test_status_consistency_repair_defaults_to_dry_run(session) -> None:
    document = SourceDocument(
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url="https://example.test/product",
        raw_file_path="/tmp/product.txt",
        sha256="2" * 64,
        final_review_status=ReviewStatus.APPROVED.value,
    )
    session.add(document)
    session.flush()
    product = ProductDocument(
        document_id=document.id,
        product_name="演示",
        source_quote="演示",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(product)
    session.commit()

    reported = StateMachineService.repair_consistency(session)

    assert len(reported) == 1
    assert product.final_review_status == ReviewStatus.PENDING_REVIEW.value
    StateMachineService.repair_consistency(session, apply=True)
    assert product.final_review_status == ReviewStatus.PENDING_REVIEW.value


@pytest.mark.parametrize(
    "status",
    [
        ReviewStatus.PENDING_SOURCE_VERIFICATION.value,
        ReviewStatus.REQUIRES_EXPERT_REVIEW.value,
    ],
)
def test_eligible_document_can_be_explicitly_resubmitted(session, status: str) -> None:
    document = document_in_status(status)
    session.add(document)
    session.commit()

    StateMachineService.resubmit_document(
        session,
        document,
        "已补充可核验官方来源",
    )

    assert document.final_review_status == ReviewStatus.PARSED.value
    history = session.query(StatusHistory).one()
    assert history.from_status == status
    assert history.to_status == ReviewStatus.PARSED.value
    assert "已补充可核验官方来源" in history.reason


@pytest.mark.parametrize(
    "status",
    [
        ReviewStatus.REJECTED_HALLUCINATION.value,
        ReviewStatus.REJECTED_DUPLICATE.value,
        ReviewStatus.REJECTED_OUTDATED.value,
    ],
)
def test_rejected_document_cannot_be_resubmitted(session, status: str) -> None:
    document = document_in_status(status)
    session.add(document)
    session.commit()

    with pytest.raises(InvalidStateTransition, match="record_not_resubmittable"):
        StateMachineService.resubmit_document(session, document, "试图重开")

    assert document.final_review_status == status
    assert session.query(StatusHistory).count() == 0
