from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import FieldEvidenceError
from app.models import Penalty, ProductDocument, Regulation, RegulatoryCase, SourceDocument
from app.models.enums import DataType, ReviewStatus
from app.repositories import DocumentRepository
from app.services.field_evidence import EVIDENCE_FIELDS, FieldEvidenceService
from app.services.integrity import RawArtifactIntegrityService
from app.services.parsed_artifacts import ParsedArtifactIntegrityService
from app.services.validation.validators import (
    AuthenticityValidator,
    DateValidator,
    HashDuplicateValidator,
    OriginalWordingValidator,
    RegulatoryCaseValidator,
    RequiredFieldValidator,
    Sha256Validator,
    SourceQuoteValidator,
    SourceUrlValidator,
    ValidationContext,
    ValidationIssue,
    ValidationResult,
)


class ValidationService:
    validators = (
        RequiredFieldValidator(),
        SourceUrlValidator(),
        DateValidator(),
        HashDuplicateValidator(),
        SourceQuoteValidator(),
        Sha256Validator(),
        AuthenticityValidator(),
        OriginalWordingValidator(),
        RegulatoryCaseValidator(),
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate_document(
        self, session: Session, document: SourceDocument, record: object | None = None
    ) -> ValidationResult:
        if document.final_review_status != ReviewStatus.PARSED.value:
            raise ValueError(
                f"Only parsed documents may be validated; current={document.final_review_status}"
            )
        RawArtifactIntegrityService(self.settings).verify(document)
        ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
        result = self.evaluate_document(session, document, [record] if record is not None else None)
        repository = DocumentRepository(session)
        metadata = dict(document.metadata_json)
        metadata["automatic_validation"] = {
            "valid": result.valid,
            "issues": [issue.__dict__ for issue in result.issues],
        }
        document.metadata_json = metadata
        codes = {issue.code for issue in result.issues}
        if "summary_evidence_requires_expert_review" in codes or (
            document.data_type == DataType.PENALTY.value and result.valid
        ):
            status = ReviewStatus.REQUIRES_EXPERT_REVIEW.value
        else:
            status = (
                ReviewStatus.PENDING_REVIEW.value
                if result.valid
                else ReviewStatus.AUTO_VALIDATION_FAILED.value
            )
        repository.transition(document, status, "deterministic validation")
        session.commit()
        return result

    def evaluate_document(
        self,
        session: Session,
        document: SourceDocument,
        records: list[object] | None = None,
    ) -> ValidationResult:
        RawArtifactIntegrityService(self.settings).verify(document)
        ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
        records = records if records is not None else self._structured_records(session, document)
        issues = self._document_issues(session, document)
        if document.data_type != DataType.EVALUATION_SAMPLE.value and not records:
            issues.append(
                ValidationIssue(
                    "StructuredRecordValidator",
                    "missing_structured_record",
                    "A parsed document requires at least one structured record",
                )
            )
        for record in records:
            required = {
                DataType.REGULATION.value: ("title", "article_text", "source_quote"),
                DataType.PENALTY.value: (
                    "illegal_facts",
                    "source_quote",
                    "original_sales_wording_disclosed",
                ),
                DataType.PRODUCT_DOCUMENT.value: ("product_name", "source_quote"),
                DataType.REGULATORY_CASE.value: (
                    "case_title",
                    "case_category",
                    "scenario_text",
                    "marketing_wording_disclosed",
                    "case_usage",
                    "source_quote",
                ),
            }.get(document.data_type, ())
            context = ValidationContext(
                document=document,
                record=record,
                required_fields=required,
            )
            issues.extend(
                issue
                for validator in self.validators
                for issue in validator.validate(context, session)
            )
            fields = {
                field_name: getattr(record, field_name)
                for field_name in EVIDENCE_FIELDS.get(document.data_type, frozenset())
            }
            try:
                FieldEvidenceService(self.settings).validate(
                    session,
                    document,
                    fields,
                    getattr(record, "field_evidence_json", {}),
                )
            except FieldEvidenceError as exc:
                issues.append(
                    ValidationIssue(
                        "FieldEvidenceValidator",
                        str(exc),
                        "Structured fields require valid immutable field evidence",
                    )
                )
        return ValidationResult(valid=not issues, issues=issues)

    def _document_issues(self, session: Session, document: SourceDocument) -> list[ValidationIssue]:
        context = ValidationContext(
            document=document,
            required_fields=("raw_text", "sha256", "raw_file_path"),
        )
        document_validators = (
            RequiredFieldValidator(),
            SourceUrlValidator(),
            HashDuplicateValidator(),
            AuthenticityValidator(),
            Sha256Validator(),
        )
        return [
            issue
            for validator in document_validators
            for issue in validator.validate(context, session)
        ]

    def validate_pending(self, session: Session) -> tuple[int, int]:
        documents = [
            item
            for item in DocumentRepository(session).list()
            if item.final_review_status == ReviewStatus.PARSED.value
        ]
        passed = 0
        for document in documents:
            result = self.validate_document(session, document)
            passed += int(result.valid)
        return passed, len(documents) - passed

    @staticmethod
    def _structured_records(session: Session, document: SourceDocument) -> list[object]:
        model: Any = {
            DataType.REGULATION.value: Regulation,
            DataType.PENALTY.value: Penalty,
            DataType.PRODUCT_DOCUMENT.value: ProductDocument,
            DataType.REGULATORY_CASE.value: RegulatoryCase,
        }.get(document.data_type)
        return (
            list(session.scalars(select(model).where(model.document_id == document.id)))
            if model
            else []
        )
