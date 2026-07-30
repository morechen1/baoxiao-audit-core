from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Penalty, SourceDocument
from app.services.parsed_artifacts import ParsedArtifactIntegrityService
from app.services.penalty_entries import (
    PENALTY_IDENTITY_FIELDS,
    build_penalty_identity_material,
    build_penalty_source_entry_fragments,
    penalty_source_entry_fingerprint,
    source_entry_content_sha256,
    validate_source_entry_locator,
)

LEGACY_PENALTY_IDENTITY_VERSION = "legacy_source_quote_v1"
LEGACY_PENALTY_IDENTITY_STATUS = "reimport_required"
LEGACY_PENALTY_REIMPORT_ERROR = "penalty_source_identity_reimport_required"


@dataclass(frozen=True)
class PenaltySourceIdentity:
    source_entry_fragments: list[dict[str, Any]]
    source_entry_content_sha256: str
    source_entry_fingerprint: str


class PenaltySourceIdentityService:
    """Validate one penalty record against its immutable import provenance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate(
        self,
        session: Session,
        document: SourceDocument,
        record: Penalty,
        *,
        candidate_fields: dict[str, Any] | None = None,
        candidate_evidence: dict[str, list[dict[str, Any]]] | None = None,
    ) -> PenaltySourceIdentity:
        provenance = self._provenance(document, record.id)
        candidate_supplied = candidate_fields is not None or candidate_evidence is not None
        fields = {field_name: getattr(record, field_name) for field_name in PENALTY_IDENTITY_FIELDS}
        if candidate_fields is not None:
            fields.update(
                {
                    field_name: candidate_fields[field_name]
                    for field_name in PENALTY_IDENTITY_FIELDS
                    if field_name in candidate_fields
                }
            )
        evidence = (
            candidate_evidence if candidate_evidence is not None else record.field_evidence_json
        )
        try:
            material = build_penalty_identity_material(fields, evidence)
            fragments = build_penalty_source_entry_fragments(fields, evidence)
            content_sha256 = source_entry_content_sha256(material)
        except (KeyError, TypeError, ValueError) as exc:
            code = (
                "penalty_source_identity_rebind_required"
                if candidate_supplied
                else "penalty_source_identity_consistency_failed"
            )
            raise ValueError(code) from exc
        if (
            provenance.get("source_entry_fragments") != fragments
            or provenance.get("source_entry_content_sha256") != content_sha256
        ):
            code = (
                "penalty_source_identity_rebind_required"
                if candidate_supplied
                else "penalty_source_identity_consistency_failed"
            )
            raise ValueError(code)
        fingerprint = penalty_source_entry_fingerprint(
            raw_artifact_sha256=document.sha256,
            source_entry_content_sha256=content_sha256,
        )
        if record.source_entry_fingerprint != fingerprint:
            raise ValueError("penalty_source_identity_consistency_failed")
        self._validate_locator(session, document, provenance)
        self._validate_database_identity(session, document, record)
        return PenaltySourceIdentity(
            source_entry_fragments=fragments,
            source_entry_content_sha256=content_sha256,
            source_entry_fingerprint=fingerprint,
        )

    @staticmethod
    def _provenance(document: SourceDocument, record_id: int) -> dict[str, Any]:
        metadata = document.metadata_json
        if not isinstance(metadata, dict):
            raise ValueError("penalty_source_identity_provenance_missing")
        raw = metadata.get("structured_draft_provenance")
        if not isinstance(raw, list):
            raise ValueError("penalty_source_identity_provenance_missing")
        matches = [
            item
            for item in raw
            if isinstance(item, dict) and item.get("structured_record_id") == record_id
        ]
        if not matches:
            raise ValueError("penalty_source_identity_provenance_missing")
        if len(matches) != 1:
            raise ValueError("penalty_source_identity_provenance_ambiguous")
        provenance = matches[0]
        if (
            provenance.get("identity_version") == LEGACY_PENALTY_IDENTITY_VERSION
            or provenance.get("source_identity_status") == LEGACY_PENALTY_IDENTITY_STATUS
        ):
            raise ValueError(LEGACY_PENALTY_REIMPORT_ERROR)
        if (
            provenance.get("record_type") != "penalty"
            or not isinstance(provenance.get("source_entry_locator"), dict)
            or not isinstance(provenance.get("source_entry_fragments"), list)
            or not isinstance(provenance.get("source_entry_content_sha256"), str)
        ):
            raise ValueError("penalty_source_identity_provenance_missing")
        return provenance

    def _validate_locator(
        self,
        session: Session,
        document: SourceDocument,
        provenance: dict[str, Any],
    ) -> None:
        artifact = ParsedArtifactIntegrityService(self.settings).verify(
            document,
            session=session,
        )
        metadata = artifact.get("metadata")
        expected_doc_id: str | None = None
        if isinstance(metadata, dict) and metadata.get("source_format") == "nfra_public_json":
            nfra = metadata.get("nfra")
            expected_doc_id = nfra.get("doc_id") if isinstance(nfra, dict) else None
            if not isinstance(expected_doc_id, str) or not expected_doc_id:
                raise ValueError("penalty_source_identity_consistency_failed")
        try:
            validate_source_entry_locator(
                provenance["source_entry_locator"],
                expected_nfra_doc_id=expected_doc_id,
            )
        except ValueError as exc:
            raise ValueError("penalty_source_identity_consistency_failed") from exc

    @staticmethod
    def _validate_database_identity(
        session: Session,
        document: SourceDocument,
        record: Penalty,
    ) -> None:
        if (
            not isinstance(record.source_entry_index, int)
            or isinstance(record.source_entry_index, bool)
            or record.source_entry_index <= 0
        ):
            raise ValueError("penalty_source_identity_consistency_failed")
        duplicate = session.scalar(
            select(Penalty.id).where(
                Penalty.document_id == document.id,
                Penalty.id != record.id,
                or_(
                    Penalty.source_entry_index == record.source_entry_index,
                    Penalty.source_entry_fingerprint == record.source_entry_fingerprint,
                ),
            )
        )
        if duplicate is not None:
            raise ValueError("penalty_source_identity_consistency_failed")
