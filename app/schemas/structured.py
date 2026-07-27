from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from app.models.enums import DataType


class StrictDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
