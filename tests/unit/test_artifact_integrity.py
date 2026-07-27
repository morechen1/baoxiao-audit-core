import hashlib
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import RawArtifactIntegrityError
from app.models import SourceDocument
from app.models.enums import AuthenticityType, DataType, ReviewStatus
from app.services.integrity import RawArtifactIntegrityService
from app.services.parsing import ParsingService
from app.services.validation import ValidationService


def stored_document(
    session,
    tmp_path: Path,
    *,
    content: bytes = b"immutable source",
) -> tuple[SourceDocument, Settings]:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    raw_dir = settings.data_dir / "raw"
    raw_dir.mkdir(parents=True)
    digest = hashlib.sha256(content).hexdigest()
    path = raw_dir / f"{digest}.txt"
    path.write_bytes(content)
    document = SourceDocument(
        data_type=DataType.REGULATION.value,
        source_url="https://official.example/rule.txt",
        final_url="https://official.example/rule.txt",
        raw_file_path=str(path),
        sha256=digest,
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        final_review_status=ReviewStatus.COLLECTED.value,
    )
    session.add(document)
    session.commit()
    return document, settings


def test_integrity_service_rejects_missing_file(session, tmp_path: Path) -> None:
    document, settings = stored_document(session, tmp_path)
    Path(document.raw_file_path).unlink()

    with pytest.raises(RawArtifactIntegrityError, match="raw_file_missing"):
        RawArtifactIntegrityService(settings).verify(document)


def test_integrity_service_rejects_path_outside_managed_raw(session, tmp_path: Path) -> None:
    document, settings = stored_document(session, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"immutable source")
    document.raw_file_path = str(outside)
    session.commit()

    with pytest.raises(
        RawArtifactIntegrityError,
        match="raw_file_path_outside_storage",
    ):
        RawArtifactIntegrityService(settings).verify(document)


def test_integrity_service_rejects_hash_mismatch(session, tmp_path: Path) -> None:
    document, settings = stored_document(session, tmp_path)
    Path(document.raw_file_path).write_bytes(b"changed")

    with pytest.raises(RawArtifactIntegrityError, match="raw_file_hash_mismatch"):
        RawArtifactIntegrityService(settings).verify(document)


def test_parsing_verifies_raw_artifact_before_reading(session, tmp_path: Path) -> None:
    document, settings = stored_document(session, tmp_path)
    Path(document.raw_file_path).write_bytes(b"changed")

    with pytest.raises(RawArtifactIntegrityError, match="raw_file_hash_mismatch"):
        ParsingService(settings).parse_document(session, document)
    assert document.parse_status == "pending"


def test_validation_verifies_raw_artifact_before_state_change(session, tmp_path: Path) -> None:
    document, settings = stored_document(session, tmp_path)
    document.parse_status = "parsed"
    document.final_review_status = ReviewStatus.PARSED.value
    document.raw_text = "immutable source"
    session.commit()
    Path(document.raw_file_path).write_bytes(b"changed")

    with pytest.raises(RawArtifactIntegrityError, match="raw_file_hash_mismatch"):
        ValidationService(settings).validate_document(session, document)
    assert document.final_review_status == ReviewStatus.PARSED.value
