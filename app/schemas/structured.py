from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import DataType


class StrictDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegulationDraft(StrictDraft):
    title: str = Field(min_length=1)
    document_number: str | None = None
    issuing_authority: str | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    validity_status: str | None = None
    article_number: str | None = None
    article_text: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)


class PenaltyDraft(StrictDraft):
    punished_entity: str | None = None
    authority: str | None = None
    document_number: str | None = None
    decision_date: date | None = None
    illegal_facts: str = Field(min_length=1)
    legal_basis: str | None = None
    penalty_result: str | None = None
    original_sales_wording_disclosed: bool
    original_sales_wording: str | None = None
    source_quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_original_wording(self) -> "PenaltyDraft":
        if self.original_sales_wording_disclosed and not self.original_sales_wording:
            raise ValueError("disclosed original wording must be provided")
        if not self.original_sales_wording_disclosed and self.original_sales_wording:
            raise ValueError("undisclosed original wording must be empty")
        return self


class ProductDocumentDraft(StrictDraft):
    company_name: str | None = None
    product_name: str = Field(min_length=1)
    product_type: str | None = None
    waiting_period: str | None = None
    cooling_off_period: str | None = None
    insurance_responsibility: str | None = None
    exclusions: str | None = None
    cash_value_description: str | None = None
    guaranteed_benefit: str | None = None
    non_guaranteed_benefit: str | None = None
    surrender_risk: str | None = None
    source_quote: str = Field(min_length=1)


class StructuredDraftEnvelope(StrictDraft):
    document_id: int = Field(gt=0)
    record_type: DataType
    fields: dict[str, Any]
