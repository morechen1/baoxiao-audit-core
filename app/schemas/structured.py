from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from app.models.enums import DataType


class StrictDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldEvidenceItem(StrictDraft):
    document_id: int | None = Field(default=None, gt=0)
    quote: StrictStr = Field(min_length=1)
    page_number: int = Field(gt=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    mode: Literal["verbatim", "normalized", "summary", "document_metadata"]
    transformation_note: StrictStr | None = None
    chunk_id: int | None = Field(default=None, gt=0)
    metadata_field: StrictStr | None = None

    @model_validator(mode="after")
    def validate_range_and_mode(self) -> "FieldEvidenceItem":
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if self.mode == "normalized" and not self.transformation_note:
            raise ValueError("normalized evidence requires transformation_note")
        if self.mode == "document_metadata" and not self.metadata_field:
            raise ValueError("document_metadata evidence requires metadata_field")
        return self


class RegulationDraft(StrictDraft):
    title: StrictStr = Field(min_length=1)
    document_number: StrictStr | None = None
    issuing_authority: StrictStr | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    validity_status: StrictStr | None = None
    article_number: StrictStr | None = None
    article_text: StrictStr = Field(min_length=1)
    source_quote: StrictStr = Field(min_length=1)


class PenaltyDraft(StrictDraft):
    punished_entity: StrictStr | None = None
    authority: StrictStr | None = None
    document_number: StrictStr | None = None
    decision_date: date | None = None
    illegal_facts: StrictStr = Field(min_length=1)
    legal_basis: StrictStr | None = None
    penalty_result: StrictStr | None = None
    original_sales_wording_disclosed: bool = Field(strict=True)
    original_sales_wording: StrictStr | None = None
    source_quote: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_original_wording(self) -> "PenaltyDraft":
        if self.original_sales_wording_disclosed and not self.original_sales_wording:
            raise ValueError("disclosed original wording must be provided")
        if not self.original_sales_wording_disclosed and self.original_sales_wording:
            raise ValueError("undisclosed original wording must be empty")
        return self


class ProductDocumentDraft(StrictDraft):
    company_name: StrictStr | None = None
    product_name: StrictStr = Field(min_length=1)
    product_type: StrictStr | None = None
    waiting_period: StrictStr | None = None
    cooling_off_period: StrictStr | None = None
    insurance_responsibility: StrictStr | None = None
    exclusions: StrictStr | None = None
    cash_value_description: StrictStr | None = None
    guaranteed_benefit: StrictStr | None = None
    non_guaranteed_benefit: StrictStr | None = None
    surrender_risk: StrictStr | None = None
    source_quote: StrictStr = Field(min_length=1)


class StructuredDraftEnvelope(StrictDraft):
    document_id: int = Field(gt=0)
    record_type: DataType
    fields: dict[str, Any]
    field_evidence: dict[str, list[FieldEvidenceItem]] | None = None


class StructuredDraftRevisionEnvelope(StrictDraft):
    document_id: int = Field(gt=0)
    record_type: DataType
    structured_record_id: int | None = Field(default=None, gt=0)
    action: Literal["update", "add", "delete"] = "update"
    fields: dict[str, Any] = Field(default_factory=dict)
    field_evidence: dict[str, list[FieldEvidenceItem]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_action(self) -> "StructuredDraftRevisionEnvelope":
        if self.action in {"update", "delete"} and self.structured_record_id is None:
            raise ValueError("structured_record_id is required")
        if self.action == "add" and self.structured_record_id is not None:
            raise ValueError("structured_record_id must be omitted for add")
        if self.action == "delete" and (self.fields or self.field_evidence):
            raise ValueError("delete cannot contain fields or field_evidence")
        if self.action != "delete" and not self.fields:
            raise ValueError("fields must not be empty")
        return self
