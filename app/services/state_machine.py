from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidStateTransition
from app.models import (
    EvaluationSample,
    Penalty,
    ProductDocument,
    Regulation,
    SourceDocument,
    StatusHistory,
)
from app.models.enums import HUMAN_REVIEW_STATUS_VALUES, DataType, ReviewStatus

ALLOWED_DOCUMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    ReviewStatus.COLLECTED.value: frozenset({ReviewStatus.PARSED.value}),
    ReviewStatus.PARSED.value: frozenset(
        {
            ReviewStatus.PENDING_REVIEW.value,
            ReviewStatus.AUTO_VALIDATION_FAILED.value,
        }
    ),
    ReviewStatus.PENDING_REVIEW.value: frozenset(
        {
            ReviewStatus.APPROVED.value,
            ReviewStatus.APPROVED_WITH_REVISION.value,
            ReviewStatus.REJECTED.value,
            ReviewStatus.PENDING_SOURCE_VERIFICATION.value,
            ReviewStatus.REJECTED_HALLUCINATION.value,
            ReviewStatus.REJECTED_DUPLICATE.value,
            ReviewStatus.REJECTED_OUTDATED.value,
            ReviewStatus.REQUIRES_EXPERT_REVIEW.value,
        }
    ),
    ReviewStatus.AUTO_VALIDATION_FAILED.value: frozenset({ReviewStatus.PARSED.value}),
    ReviewStatus.PENDING_SOURCE_VERIFICATION.value: frozenset({ReviewStatus.PARSED.value}),
    ReviewStatus.REQUIRES_EXPERT_REVIEW.value: frozenset({ReviewStatus.PARSED.value}),
}


class StateMachineService:
    @classmethod
    def transition_document(
        cls,
        session: Session,
        document: SourceDocument,
        to_status: str,
        reason: str,
        *,
        sync_structured: bool = True,
    ) -> None:
        from_status = document.final_review_status
        allowed = ALLOWED_DOCUMENT_TRANSITIONS.get(from_status, frozenset())
        if to_status not in allowed:
            raise InvalidStateTransition(
                f"Illegal document transition: {from_status} -> {to_status}"
            )
        document.final_review_status = to_status
        if sync_structured:
            for record in cls.structured_records(session, document):
                record.final_review_status = to_status
        session.add(
            StatusHistory(
                record_type=document.data_type,
                record_id=document.id,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )

    @staticmethod
    def transition_evaluation_sample(
        session: Session,
        sample: EvaluationSample,
        to_status: str,
        reason: str,
    ) -> None:
        if sample.final_review_status != ReviewStatus.PENDING_REVIEW.value:
            raise InvalidStateTransition(
                f"Only pending_review evaluation samples may be reviewed; "
                f"current={sample.final_review_status}"
            )
        if to_status not in ALLOWED_DOCUMENT_TRANSITIONS[ReviewStatus.PENDING_REVIEW.value]:
            raise InvalidStateTransition(f"Illegal human decision status: {to_status}")
        from_status = sample.final_review_status
        sample.final_review_status = to_status
        session.add(
            StatusHistory(
                record_type=DataType.EVALUATION_SAMPLE.value,
                record_id=sample.id,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )

    @staticmethod
    def structured_records(session: Session, document: SourceDocument) -> list[Any]:
        model: Any = {
            DataType.REGULATION.value: Regulation,
            DataType.PENALTY.value: Penalty,
            DataType.PRODUCT_DOCUMENT.value: ProductDocument,
        }.get(document.data_type)
        if model is None:
            return []
        return list(session.scalars(select(model).where(model.document_id == document.id)))

    @classmethod
    def resubmit_document(
        cls,
        session: Session,
        document: SourceDocument,
        reason: str,
    ) -> None:
        if document.final_review_status not in {
            ReviewStatus.PENDING_SOURCE_VERIFICATION.value,
            ReviewStatus.REQUIRES_EXPERT_REVIEW.value,
        }:
            raise InvalidStateTransition("record_not_resubmittable")
        if not reason.strip():
            raise InvalidStateTransition("resubmission_reason_required")
        cls.transition_document(
            session,
            document,
            ReviewStatus.PARSED.value,
            f"resubmitted for review: {reason.strip()}",
        )
        session.commit()

    @classmethod
    def repair_consistency(cls, session: Session, *, apply: bool = False) -> list[dict[str, Any]]:
        repairs: list[dict[str, Any]] = []
        for document in session.scalars(select(SourceDocument).order_by(SourceDocument.id)):
            for record in cls.structured_records(session, document):
                if record.final_review_status == document.final_review_status:
                    continue
                repairs.append(
                    {
                        "document_id": document.id,
                        "record_type": document.data_type,
                        "record_id": record.id,
                        "from_status": record.final_review_status,
                        "to_status": document.final_review_status,
                    }
                )
                if apply and document.final_review_status not in HUMAN_REVIEW_STATUS_VALUES:
                    record.final_review_status = document.final_review_status
        if apply:
            session.commit()
        else:
            session.rollback()
        return repairs
