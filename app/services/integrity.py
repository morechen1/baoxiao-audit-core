from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.exceptions import RawArtifactIntegrityError
from app.models import SourceDocument
from app.models.enums import AuthenticityType


class RawArtifactIntegrityService:
    chunk_size = 1024 * 1024

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def verify(self, document: SourceDocument) -> str:
        path = Path(document.raw_file_path)
        if not path.exists() or not path.is_file():
            raise RawArtifactIntegrityError("raw_file_missing")
        resolved = path.resolve()
        if self._requires_managed_storage(document):
            raw_dir = (self.settings.data_dir.resolve() / "raw").resolve()
            if resolved.parent != raw_dir and raw_dir not in resolved.parents:
                raise RawArtifactIntegrityError("raw_file_path_outside_storage")
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(self.chunk_size), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != document.sha256:
            raise RawArtifactIntegrityError("raw_file_hash_mismatch")
        return actual

    @staticmethod
    def _requires_managed_storage(document: SourceDocument) -> bool:
        return document.authenticity_type not in {
            AuthenticityType.DEMO_ONLY.value,
            AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value,
        }
