from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

from app.models.enums import AuthenticityType, DataType


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: HttpUrl | None = None
    publisher: str | None = Field(default=None, max_length=255)
    source_type: DataType
    enabled: bool = True
    crawl_policy: dict[str, Any] = Field(default_factory=dict)
    rate_limit_seconds: float = Field(default=1.0, ge=0, le=3600)


class CollectionUrlRequest(BaseModel):
    url: HttpUrl
    source_type: DataType
    source_id: int = Field(gt=0)


class CollectionLocalRequest(BaseModel):
    path: Path
    source_type: DataType
    source_id: int | None = None
    authenticity_type: AuthenticityType = AuthenticityType.PENDING_VERIFICATION


class ReviewBatchRequest(BaseModel):
    data_type: DataType
    format: Literal["jsonl", "xlsx"] = "jsonl"


class ReviewImportRequest(BaseModel):
    path: Path
    batch_id: int = Field(gt=0)


class StructuredImportRequest(BaseModel):
    path: Path
