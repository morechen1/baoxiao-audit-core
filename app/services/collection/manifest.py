from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import CollectionError
from app.models import DataSource
from app.models.enums import AuthenticityType
from app.schemas.collection import LocalImportManifestEntry
from app.services.collection.file import FileCollector
from app.services.collection.security import SafeUrlPolicy


class LocalManifestCollector:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def import_jsonl(self, session: Session, manifest_path: Path) -> tuple[int, list[str]]:
        path = manifest_path.resolve()
        import_root = (self.settings.data_dir.resolve() / "import").resolve()
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            raise CollectionError("local_manifest_missing")
        if path.parent != import_root and import_root not in path.parents:
            raise CollectionError("local_manifest_outside_import_storage")
        imported = 0
        errors: list[str] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = LocalImportManifestEntry.model_validate_json(line)
                self.import_entry(session, entry, path.parent)
                imported += 1
            except (
                PydanticValidationError,
                CollectionError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                session.rollback()
                errors.append(f"line {line_number}: {exc}")
        return imported, errors

    def import_entry(
        self,
        session: Session,
        entry: LocalImportManifestEntry,
        manifest_dir: Path,
    ) -> int:
        source = session.get(DataSource, entry.source_id)
        if not source or not source.enabled:
            raise CollectionError("manifest_source_not_enabled")
        if source.source_type != entry.source_type.value:
            raise CollectionError("manifest_source_type_mismatch")
        allowed_domains = source.crawl_policy.get("allowed_domains", [])
        if not isinstance(allowed_domains, list) or any(
            not isinstance(value, str) for value in allowed_domains
        ):
            raise CollectionError("manifest_source_allowlist_invalid")
        for url in (str(entry.source_url), str(entry.final_url)):
            if not SafeUrlPolicy.host_allowed(url, source.base_url, allowed_domains):
                raise CollectionError("manifest_source_url_not_allowed")
        local_path = entry.path.resolve()
        if not local_path.is_file():
            local_path = (manifest_dir / entry.path).resolve()
        import_root = (self.settings.data_dir.resolve() / "import").resolve()
        if not local_path.is_file() or (
            local_path.parent != import_root and import_root not in local_path.parents
        ):
            raise CollectionError("manifest_local_file_not_allowed")
        collector = FileCollector(self.settings)
        local = collector.collect(local_path)
        result = replace(
            local,
            source_url=str(entry.source_url),
            final_url=str(entry.final_url),
            title=entry.source_title,
            publisher=entry.publisher,
            published_at=entry.published_at,
            metadata={
                "local_manifest_import": True,
                "original_filename": local_path.name,
                "declared_source_id": entry.source_id,
            },
        )
        document, _ = collector.persist(
            session,
            result,
            entry.source_type.value,
            source_id=entry.source_id,
            authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        )
        return document.id
