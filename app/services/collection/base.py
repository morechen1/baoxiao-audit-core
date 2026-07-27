from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import DocumentOccurrence, SourceDocument
from app.models.enums import AuthenticityType, ReviewStatus
from app.repositories import DocumentRepository


@dataclass
class CollectionResult:
    content: bytes
    source_url: str | None
    final_url: str | None
    content_type: str
    http_status: int | None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseCollector(ABC):
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @abstractmethod
    def collect(self, target: str | Path) -> CollectionResult:
        """Collect one target without persisting it."""

    @staticmethod
    def sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def persist(
        self,
        session: Session,
        result: CollectionResult,
        data_type: str,
        *,
        source_id: int | None = None,
        authenticity_type: str = AuthenticityType.PENDING_VERIFICATION.value,
    ) -> tuple[SourceDocument, bool]:
        digest = self.sha256(result.content)
        repository = DocumentRepository(session)
        duplicate = repository.by_hash(digest)
        if duplicate:
            self._record_occurrence(session, duplicate, result, source_id=source_id)
            session.commit()
            return duplicate, False

        raw_dir = self.settings.data_dir.resolve() / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        suffix = _suffix_for_content_type(result.content_type, result.final_url)
        raw_path = (raw_dir / f"{digest}{suffix}").resolve()
        if raw_dir not in raw_path.parents:
            raise ValueError("Unsafe storage path")
        raw_path.write_bytes(result.content)
        document = SourceDocument(
            source_id=source_id,
            data_type=data_type,
            source_url=result.source_url,
            final_url=result.final_url,
            source_title=result.title,
            content_type=result.content_type,
            raw_file_path=str(raw_path),
            sha256=digest,
            http_status=result.http_status,
            authenticity_type=authenticity_type,
            collection_status=ReviewStatus.COLLECTED.value,
            final_review_status=ReviewStatus.COLLECTED.value,
            metadata_json=result.metadata,
        )
        repository.add(document)
        self._record_occurrence(session, document, result, source_id=source_id)
        session.commit()
        return document, True

    @staticmethod
    def _record_occurrence(
        session: Session,
        document: SourceDocument,
        result: CollectionResult,
        *,
        source_id: int | None,
    ) -> None:
        existing = session.scalar(
            select(DocumentOccurrence).where(
                DocumentOccurrence.document_id == document.id,
                DocumentOccurrence.source_url == result.source_url,
                DocumentOccurrence.final_url == result.final_url,
            )
        )
        if existing:
            existing.source_id = source_id
            existing.http_status = result.http_status
            existing.response_metadata = result.metadata
            return
        session.add(
            DocumentOccurrence(
                document_id=document.id,
                source_id=source_id,
                source_url=result.source_url,
                final_url=result.final_url,
                publisher=document.publisher,
                http_status=result.http_status,
                response_metadata=result.metadata,
            )
        )


def _suffix_for_content_type(content_type: str, url: str | None) -> str:
    mapping = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "text/html": ".html",
        "text/plain": ".txt",
    }
    normalized = content_type.split(";", 1)[0].lower()
    if normalized in mapping:
        return mapping[normalized]
    suffix = Path(url or "").suffix.lower()
    return suffix if suffix in {".pdf", ".docx", ".html", ".htm", ".txt"} else ".bin"
