from __future__ import annotations

from enum import StrEnum
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictStr, model_validator


class PilotSourceType(StrEnum):
    REGULATION = "regulation"
    PENALTY = "penalty"
    REGULATORY_CASE = "regulatory_case"
    PRODUCT_DOCUMENT = "product_document"


class PilotManifestStatus(StrEnum):
    DRAFT = "draft"
    APPROVED_FOR_COLLECTION = "approved_for_collection"
    COLLECTED = "collected"
    PARSED = "parsed"
    STRUCTURED = "structured"
    PENDING_REVIEW = "pending_review"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class StrictPilotModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CrawlPolicy(StrictPilotModel):
    rate_limit_seconds: float = Field(default=2, ge=0)
    max_documents: int = Field(default=10, gt=0)
    allow_subdomains: bool = True


class SourceRegistryEntry(StrictPilotModel):
    source_key: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    name: StrictStr = Field(min_length=1)
    publisher: StrictStr = Field(min_length=1)
    base_url: HttpUrl
    source_type: PilotSourceType
    allowed_domains: list[StrictStr] = Field(min_length=1)
    enabled: bool = False
    confirmed_by: StrictStr | None = None
    crawl_policy: CrawlPolicy = Field(default_factory=CrawlPolicy)
    notes: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def validate_confirmation_and_domains(self) -> SourceRegistryEntry:
        if self.enabled and not (self.confirmed_by and self.confirmed_by.strip()):
            raise ValueError("enabled source requires confirmed_by")
        for domain in self.allowed_domains:
            parsed = urlparse(domain if "://" in domain else f"//{domain}")
            if not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("allowed_domains must contain hostnames only")
        return self


class SourceRegistryFile(StrictPilotModel):
    sources: list[SourceRegistryEntry] = Field(default_factory=list)


class PilotManifestEntry(StrictPilotModel):
    pilot_id: StrictStr = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,63}$")
    source_key: StrictStr = Field(min_length=1)
    source_type: PilotSourceType
    source_url: HttpUrl
    expected_title: StrictStr | None = None
    collection_method: Literal["url"] = "url"
    evaluation_usage: list[StrictStr] = Field(default_factory=list)
    confirmed_by: StrictStr | None = None
    status: PilotManifestStatus = PilotManifestStatus.DRAFT
    case_usage: Literal["external_test_candidate"] | None = None

    @model_validator(mode="after")
    def validate_approval_and_case_usage(self) -> PilotManifestEntry:
        if self.status == PilotManifestStatus.APPROVED_FOR_COLLECTION and not (
            self.confirmed_by and self.confirmed_by.strip()
        ):
            raise ValueError("approved_for_collection requires confirmed_by")
        if self.source_type == PilotSourceType.REGULATORY_CASE:
            if self.case_usage != "external_test_candidate":
                raise ValueError("regulatory_case requires case_usage=external_test_candidate")
        elif self.case_usage is not None:
            raise ValueError("case_usage is only valid for regulatory_case")
        return self
