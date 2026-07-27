from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Penalty, ProductDocument, Regulation, SourceDocument
from app.models.enums import DataType, ReviewStatus
from app.repositories import DocumentRepository
from app.services.validation.validators import (
    AuthenticityValidator,
    DateValidator,
    HashDuplicateValidator,
    OriginalWordingValidator,
    RequiredFieldValidator,
    SourceQuoteValidator,
    SourceUrlValidator,
    ValidationContext,
    ValidationResult,
)


class ValidationService:
    validators = (
        RequiredFieldValidator(),
        SourceUrlValidator(),
        DateValidator(),
        HashDuplicateValidator(),
        SourceQuoteValidator(),
        AuthenticityValidator(),
        OriginalWordingValidator(),
    )

    def validate_document(
        self, session: Session, document: SourceDocument, record: object | None = None
    ) -> ValidationResult:
        required = ("raw_text", "sha256", "raw_file_path")
        context = ValidationContext(document=document, record=record, required_fields=required)
        issues = [
            issue for validator in self.validators for issue in validator.validate(context, session)
        ]
        repository = DocumentRepository(session)
        metadata = dict(document.metadata_json)
        metadata["automatic_validation"] = {
            "valid": not issues,
            "issues": [issue.__dict__ for issue in issues],
        }
        document.metadata_json = metadata
        status = (
            ReviewStatus.PENDING_REVIEW.value
            if not issues
            else ReviewStatus.AUTO_VALIDATION_FAILED.value
        )
        repository.transition(document, status, "deterministic validation")
        session.commit()
        return ValidationResult(valid=not issues, issues=issues)

    def validate_pending(self, session: Session) -> tuple[int, int]:
        documents = [
            item
            for item in DocumentRepository(session).list()
            if item.final_review_status == ReviewStatus.PARSED.value
        ]
        passed = 0
        for document in documents:
            record = self._structured_record(session, document)
            result = self.validate_document(session, document, record)
            passed += int(result.valid)
        return passed, len(documents) - passed

    @staticmethod
    def _structured_record(session: Session, document: SourceDocument) -> object | None:
        model: Any = {
            DataType.REGULATION.value: Regulation,
            DataType.PENALTY.value: Penalty,
            DataType.PRODUCT_DOCUMENT.value: ProductDocument,
        }.get(document.data_type)
        return (
            session.scalar(select(model).where(model.document_id == document.id)) if model else None
        )
