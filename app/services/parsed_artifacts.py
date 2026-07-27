from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ParsedArtifactIntegrityError
from app.models import ParsedArtifactVersion, SourceDocument
from app.services.integrity import RawArtifactIntegrityService

if TYPE_CHECKING:
    from app.services.parsing.base import ParsedDocument

PARSED_ARTIFACT_SCHEMA_VERSION = "1.0"
PARSER_VERSION = "1.0"


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class ParsedArtifactService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def persist(
        self,
        session: Session,
        document: SourceDocument,
        parsed: ParsedDocument,
        *,
        parser_name: str,
        parser_version: str = PARSER_VERSION,
    ) -> dict[str, Any]:
        RawArtifactIntegrityService(self.settings).verify(document)
        parsed_at = datetime.now(UTC)
        payload = {
            "schema_version": PARSED_ARTIFACT_SCHEMA_VERSION,
            "document_id": document.id,
            "raw_sha256": document.sha256,
            "parser_name": parser_name,
            "parser_version": parser_version,
            "parsed_at": parsed_at.isoformat(),
            "title": parsed.title,
            "plain_text": parsed.plain_text,
            "pages": self._pages_with_offsets(parsed),
            "warnings": parsed.warnings,
            "metadata": parsed.metadata,
        }
        content = canonical_json_bytes(payload)
        artifact_sha256 = hashlib.sha256(content).hexdigest()
        output_dir = self.settings.data_dir.resolve() / "parsed_artifacts"
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = output_dir / f"{document.sha256}-{artifact_sha256}.json"
        if artifact_path.exists() and artifact_path.read_bytes() != content:
            raise ParsedArtifactIntegrityError("parsed_artifact_hash_mismatch")
        if not artifact_path.exists():
            artifact_path.write_bytes(content)
        parsed_text_hash = text_sha256(parsed.plain_text)
        document.parsed_artifact_path = str(artifact_path)
        document.parsed_artifact_sha256 = artifact_sha256
        document.parsed_text_sha256 = parsed_text_hash
        document.parsed_from_raw_sha256 = document.sha256
        document.parser_name = parser_name
        document.parser_version = parser_version
        document.parsed_at = parsed_at
        session.add(
            ParsedArtifactVersion(
                document_id=document.id,
                artifact_path=str(artifact_path),
                artifact_sha256=artifact_sha256,
                text_sha256=parsed_text_hash,
                from_raw_sha256=document.sha256,
                parser_name=parser_name,
                parser_version=parser_version,
                parsed_at=parsed_at,
            )
        )
        return payload

    @staticmethod
    def _pages_with_offsets(parsed: ParsedDocument) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor = 0
        for page in parsed.pages:
            start = parsed.plain_text.find(page.text, cursor) if page.text else cursor
            if start < 0:
                raise ParsedArtifactIntegrityError("parsed_text_hash_mismatch")
            end = start + len(page.text)
            pages.append(
                {
                    "page_number": page.page_number,
                    "text": page.text,
                    "start_offset": start,
                    "end_offset": end,
                }
            )
            cursor = end
        return pages


class ParsedArtifactIntegrityService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def verify(
        self,
        document: SourceDocument,
        *,
        session: Session | None = None,
    ) -> dict[str, Any]:
        RawArtifactIntegrityService(self.settings).verify(document)
        if not document.parsed_artifact_path:
            raise ParsedArtifactIntegrityError("parsed_artifact_missing")
        path = Path(document.parsed_artifact_path)
        if not path.exists() or not path.is_file():
            raise ParsedArtifactIntegrityError("parsed_artifact_missing")
        resolved = path.resolve()
        managed_dir = (self.settings.data_dir.resolve() / "parsed_artifacts").resolve()
        if resolved.parent != managed_dir and managed_dir not in resolved.parents:
            raise ParsedArtifactIntegrityError("parsed_artifact_path_outside_storage")
        content = resolved.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != document.parsed_artifact_sha256:
            raise ParsedArtifactIntegrityError("parsed_artifact_hash_mismatch")
        try:
            payload = cast(dict[str, Any], json.loads(content))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParsedArtifactIntegrityError("parsed_artifact_hash_mismatch") from exc
        if (
            payload.get("document_id") != document.id
            or payload.get("raw_sha256") != document.sha256
            or document.parsed_from_raw_sha256 != document.sha256
        ):
            raise ParsedArtifactIntegrityError("parsed_raw_hash_mismatch")
        plain_text = payload.get("plain_text")
        if not isinstance(plain_text, str):
            raise ParsedArtifactIntegrityError("parsed_text_hash_mismatch")
        actual_text_hash = text_sha256(plain_text)
        if (
            actual_text_hash != document.parsed_text_sha256
            or payload.get("parser_name") != document.parser_name
            or payload.get("parser_version") != document.parser_version
        ):
            raise ParsedArtifactIntegrityError("parsed_text_hash_mismatch")
        if document.raw_text != plain_text:
            raise ParsedArtifactIntegrityError("stored_raw_text_mismatch")
        if session is not None:
            self._verify_chunks(document)
        return payload

    @staticmethod
    def _verify_chunks(document: SourceDocument) -> None:
        raw_text = document.raw_text or ""
        previous_end = 0
        for chunk in sorted(document.chunks, key=lambda value: value.chunk_index):
            if (
                chunk.document_id != document.id
                or chunk.start_offset < previous_end
                or chunk.end_offset < chunk.start_offset
                or raw_text[chunk.start_offset : chunk.end_offset] != chunk.text
            ):
                raise ParsedArtifactIntegrityError("document_chunk_mismatch")
            previous_end = chunk.end_offset
