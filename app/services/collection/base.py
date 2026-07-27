from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import CollectionError, RawArtifactIntegrityError
from app.models import DataSource, DocumentOccurrence, SourceDocument
from app.models.enums import AuthenticityType, DocumentDataType, ReviewStatus
from app.repositories import DocumentRepository
from app.services.integrity import RawArtifactIntegrityService


@dataclass
class CollectionResult:
    content: bytes
    source_url: str | None
    final_url: str | None
    content_type: str
    http_status: int | None
    title: str | None = None
    publisher: str | None = None
    published_at: date | None = None
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
        if data_type not in {value.value for value in DocumentDataType}:
            raise CollectionError("unsupported_document_data_type")
        if authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value:
            raise ValueError(
                "verified_public can only be assigned by an audited human review decision"
            )
        digest = self.sha256(result.content)
        repository = DocumentRepository(session)
        duplicate = repository.by_hash(digest)
        if duplicate:
            if duplicate.data_type != data_type:
                raise CollectionError("duplicate_content_type_conflict")
            try:
                RawArtifactIntegrityService(self.settings).verify(duplicate)
            except RawArtifactIntegrityError as exc:
                raise CollectionError("stored_artifact_corrupt") from exc
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
        source = session.get(DataSource, source_id) if source_id is not None else None
        publisher = result.publisher or (source.publisher if source else None)
        metadata = dict(result.metadata)
        if source_id is None and (result.source_url or "").startswith("file://"):
            metadata["official_source_declared"] = False
        document = SourceDocument(
            source_id=source_id,
            data_type=data_type,
            source_url=result.source_url,
            final_url=result.final_url,
            source_title=result.title,
            publisher=publisher,
            published_at=result.published_at,
            content_type=result.content_type,
            raw_file_path=str(raw_path),
            sha256=digest,
            http_status=result.http_status,
            authenticity_type=authenticity_type,
            collection_status=ReviewStatus.COLLECTED.value,
            final_review_status=ReviewStatus.COLLECTED.value,
            metadata_json=metadata,
        )
        repository.add(document)
        self._record_occurrence(session, document, result, source_id=source_id)
        session.commit()
        return document, True

    def _record_occurrence(
        self,
        session: Session,
        document: SourceDocument,
        result: CollectionResult,
        *,
        source_id: int | None,
    ) -> None:
        source = session.get(DataSource, source_id) if source_id is not None else None
        publisher = result.publisher or (source.publisher if source else None)
        response_metadata = dict(result.metadata)
        if source_id is None and (result.source_url or "").startswith("file://"):
            response_metadata["official_source_declared"] = False
        if not document.publisher and publisher:
            document.publisher = publisher
        existing = session.scalar(
            select(DocumentOccurrence).where(
                DocumentOccurrence.document_id == document.id,
                DocumentOccurrence.source_url == result.source_url,
                DocumentOccurrence.final_url == result.final_url,
            )
        )
        if existing:
            if source_id is not None:
                existing.source_id = source_id
            if publisher:
                existing.publisher = publisher
            existing.http_status = result.http_status
            existing.response_metadata = {
                **existing.response_metadata,
                **response_metadata,
            }
            return
        session.add(
            DocumentOccurrence(
                document_id=document.id,
                source_id=source_id,
                source_url=result.source_url,
                final_url=result.final_url,
                publisher=publisher,
                http_status=result.http_status,
                response_metadata=response_metadata,
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
