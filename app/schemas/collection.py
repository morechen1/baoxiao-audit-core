from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.enums import DocumentDataType


class LocalImportManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    source_id: int = Field(gt=0)
    source_type: DocumentDataType
    source_url: HttpUrl
    final_url: HttpUrl
    source_title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    published_at: date | None = None
