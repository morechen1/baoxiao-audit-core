from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import AuthenticityType, DatasetSplit, SampleCategory
from app.schemas.structured import FieldEvidenceItem


class StrictReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructuredRecordCorrection(StrictReviewModel):
    structured_record_id: int | None = Field(default=None, gt=0)
    portable_record_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    fields: dict[str, Any] = Field(min_length=1)
    field_evidence: dict[str, list[FieldEvidenceItem]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_record_locator(self) -> StructuredRecordCorrection:
        if self.structured_record_id is None and self.portable_record_key is None:
            raise ValueError("structured record locator is required")
        return self


class StructuredRecordCorrections(StrictReviewModel):
    records: list[StructuredRecordCorrection] = Field(min_length=1)

    @field_validator("records")
    @classmethod
    def record_locators_must_be_unique(
        cls, records: list[StructuredRecordCorrection]
    ) -> list[StructuredRecordCorrection]:
        locators = [
            (
                f"portable:{record.portable_record_key}"
                if record.portable_record_key is not None
                else f"database:{record.structured_record_id}"
            )
            for record in records
        ]
        if len(locators) != len(set(locators)):
            raise ValueError("structured record locators must be unique")
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
