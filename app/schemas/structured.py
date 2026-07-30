from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from app.models.enums import (
    DataType,
    RegulatoryCaseCategory,
    RegulatoryCaseUsage,
)


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
    validity_status: Literal["unknown"] | None = "unknown"
    article_number: StrictStr | None = None
    article_text: StrictStr = Field(min_length=1)
    source_quote: StrictStr = Field(min_length=1)


class PenaltyDraft(StrictDraft):
    source_entry_index: int = Field(gt=0)
    source_entry_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
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


class SourceEntryFragment(StrictDraft):
    quote: StrictStr = Field(min_length=1)
    start_offset: StrictInt = Field(ge=0)
    end_offset: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "SourceEntryFragment":
        if self.end_offset <= self.start_offset:
            raise ValueError("penalty_source_entry_fragment_invalid")
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


class RegulatoryCaseDraft(StrictDraft):
    case_title: StrictStr = Field(min_length=1)
    publisher: StrictStr | None = Field(default=None, min_length=1)
    published_at: date | None = None
    case_category: RegulatoryCaseCategory
    scenario_text: StrictStr = Field(min_length=1)
    marketing_wording_disclosed: bool = Field(strict=True)
    marketing_wording: StrictStr | None = Field(default=None, min_length=1)
    case_facts: StrictStr | None = Field(default=None, min_length=1)
    regulatory_analysis: StrictStr | None = Field(default=None, min_length=1)
    consumer_advice: StrictStr | None = Field(default=None, min_length=1)
    case_usage: RegulatoryCaseUsage = RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE
    source_quote: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_marketing_wording(self) -> "RegulatoryCaseDraft":
        if self.marketing_wording_disclosed and not self.marketing_wording:
            raise ValueError("disclosed marketing wording must be provided")
        if not self.marketing_wording_disclosed and self.marketing_wording:
            raise ValueError("undisclosed marketing wording must be empty")
        return self


class RegulatoryCaseRevision(RegulatoryCaseDraft):
    """Full candidate model used for atomic human-review corrections."""


class StructuredDraftEnvelope(StrictDraft):
    pilot_id: StrictStr | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_-]{2,63}$")
    document_id: int = Field(gt=0)
    record_type: DataType
    fields: dict[str, Any]
    field_evidence: dict[str, list[FieldEvidenceItem]] | None = None
    draft_generation_method: StrictStr | None = Field(default=None, min_length=1)
    draft_generation_version: StrictStr | None = Field(default=None, min_length=1)
    source_entry_locator: dict[str, Any] | None = None
    source_entry_fragments: list[SourceEntryFragment] | None = Field(
        default=None,
        min_length=1,
    )
    source_entry_content_sha256: StrictStr | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_generation_provenance(self) -> "StructuredDraftEnvelope":
        provenance = (
            self.pilot_id,
            self.draft_generation_method,
            self.draft_generation_version,
        )
        if any(value is not None for value in provenance) and not all(
            value is not None for value in provenance
        ):
            raise ValueError("draft generation provenance must be complete")
        entry_identity = (
            self.source_entry_locator,
            self.source_entry_fragments,
            self.source_entry_content_sha256,
        )
        if self.record_type == DataType.PENALTY:
            if not all(value is not None for value in entry_identity):
                raise ValueError("penalty source entry identity must be complete")
        elif any(value is not None for value in entry_identity):
            raise ValueError("source entry identity is only valid for penalty drafts")
        return self


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
