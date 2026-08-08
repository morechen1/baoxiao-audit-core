from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

from app.models.enums import DataType, DocumentDataType


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: HttpUrl | None = None
    publisher: str | None = Field(default=None, max_length=255)
    source_type: DocumentDataType
    enabled: bool = True
    crawl_policy: dict[str, Any] = Field(default_factory=dict)
    rate_limit_seconds: float = Field(default=1.0, ge=0, le=3600)


class CollectionUrlRequest(BaseModel):
    url: HttpUrl
    source_type: DocumentDataType
    source_id: int = Field(gt=0)


class CollectionLocalRequest(BaseModel):
    path: Path
    source_type: DocumentDataType
    source_id: int | None = None


class ReviewBatchRequest(BaseModel):
    data_type: DataType
    format: Literal["jsonl", "xlsx"] = "jsonl"


class ReviewImportRequest(BaseModel):
    path: Path
    batch_id: int = Field(gt=0)


class StructuredImportRequest(BaseModel):
    path: Path


class ScreeningCreateRequest(BaseModel):
    title: str
    material_type: str
    raw_text: str
    source_label: str = "user_submission"
    external_reference: str | None = None


class ExplanationCreateRequest(BaseModel):
    audience: Literal["institution", "consumer"]
    provider: Literal["deterministic_fixture", "openai_compatible", "external"]
    retry_of_id: int | None = Field(default=None, gt=0)
