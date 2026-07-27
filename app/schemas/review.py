from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AuthenticityType, DatasetSplit, SampleCategory
from app.schemas.structured import FieldEvidenceItem


class StrictReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructuredRecordCorrection(StrictReviewModel):
    structured_record_id: int = Field(gt=0)
    fields: dict[str, Any] = Field(min_length=1)
    field_evidence: dict[str, list[FieldEvidenceItem]] = Field(default_factory=dict)


class StructuredRecordCorrections(StrictReviewModel):
    records: list[StructuredRecordCorrection] = Field(min_length=1)

    @field_validator("records")
    @classmethod
    def record_ids_must_be_unique(
        cls, records: list[StructuredRecordCorrection]
    ) -> list[StructuredRecordCorrection]:
        ids = [record.structured_record_id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("structured_record_id values must be unique")
        return records


class AuthenticityDecisionInput(StrictReviewModel):
    new_type: Literal[AuthenticityType.VERIFIED_PUBLIC]
    verified_occurrence_id: int = Field(gt=0)
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


class EvaluationSampleRevision(StrictReviewModel):
    sample_text: str = Field(min_length=1)
    sample_category: SampleCategory
    risk_labels: list[str]
    expected_evidence: dict[str, Any]
    construction_basis: str = Field(min_length=1)
    authenticity_type: Literal[AuthenticityType.CONSTRUCTED_FOR_EVALUATION]
    split: DatasetSplit

    @field_validator("sample_text", "construction_basis")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("risk_labels")
    @classmethod
    def risk_labels_must_be_strings(cls, value: list[str]) -> list[str]:
        if any(not label.strip() for label in value):
            raise ValueError("risk_labels must contain non-empty strings")
        return value
