from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import StructuredRecordError
from app.models import (
    Penalty,
    PilotCollectionItem,
    ProductDocument,
    Regulation,
    RegulatoryCase,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import (
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    RegulatoryCaseUsage,
    ReviewStatus,
)
from app.schemas.structured import (
    PenaltyDraft,
    ProductDocumentDraft,
    RegulationDraft,
    RegulatoryCaseDraft,
    StructuredDraftEnvelope,
)
from app.services.field_evidence import EVIDENCE_FIELDS, FieldEvidenceService
from app.services.parsed_artifacts import ParsedArtifactIntegrityService
from app.services.penalty_entries import (
    build_penalty_identity_material,
    build_penalty_source_entry_fragments,
    canonical_source_entry_fragments,
    penalty_source_entry_fingerprint,
    source_entry_content_sha256,
    validate_source_entry_locator,
)
from app.services.state_machine import StateMachineService

DRAFT_MODELS = {
    DataType.REGULATION.value: RegulationDraft,
    DataType.PENALTY.value: PenaltyDraft,
    DataType.PRODUCT_DOCUMENT.value: ProductDocumentDraft,
    DataType.REGULATORY_CASE.value: RegulatoryCaseDraft,
}

ENTITY_MODELS = {
    DataType.REGULATION.value: Regulation,
    DataType.PENALTY.value: Penalty,
    DataType.PRODUCT_DOCUMENT.value: ProductDocument,
    DataType.REGULATORY_CASE.value: RegulatoryCase,
}
PENALTY_DUPLICATE_SIMILARITY_THRESHOLD = 0.90


class StructuredRecordService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def import_jsonl(self, session: Session, path: Path) -> tuple[int, list[str]]:
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            raise StructuredRecordError("Structured drafts must be an existing JSONL file")
        pending: list[tuple[int, StructuredDraftEnvelope]] = []
        errors: list[str] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                pending.append((line_number, StructuredDraftEnvelope.model_validate_json(line)))
            except (
                json.JSONDecodeError,
                PydanticValidationError,
                StructuredRecordError,
            ) as exc:
                errors.append(f"line {line_number}: {exc}")
        if errors:
            session.rollback()
            return 0, errors
        try:
            for line_number, envelope in pending:
                try:
                    self.import_draft(session, envelope, commit=False)
                except Exception as exc:
                    session.rollback()
                    return 0, [f"line {line_number}: {exc}"]
            session.commit()
        except Exception:
            session.rollback()
            raise
        return len(pending), []

    def import_draft(
        self,
        session: Session,
        envelope: StructuredDraftEnvelope,
        *,
        commit: bool = True,
    ) -> Regulation | Penalty | ProductDocument | RegulatoryCase:
        document = session.get(SourceDocument, envelope.document_id)
        if not document:
            raise StructuredRecordError(f"Document {envelope.document_id} does not exist")
        if document.final_review_status not in {
            ReviewStatus.PARSED.value,
            ReviewStatus.AUTO_VALIDATION_FAILED.value,
        }:
            raise StructuredRecordError("structured_record_locked")
        if document.parse_status != "parsed":
            raise StructuredRecordError("Document must be parsed before importing a draft")
        if envelope.record_type.value != document.data_type:
            raise StructuredRecordError("record_type does not match document.data_type")
        draft_model: Any = DRAFT_MODELS.get(document.data_type)
        entity_model: Any = ENTITY_MODELS.get(document.data_type)
        if not draft_model or not entity_model:
            raise StructuredRecordError("evaluation_sample does not use structured drafts")
        try:
            draft = draft_model.model_validate(envelope.fields)
        except PydanticValidationError as exc:
            raise StructuredRecordError(str(exc)) from exc
        if (
            document.data_type == DataType.REGULATORY_CASE.value
            and draft.case_usage != RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE
        ):
            raise StructuredRecordError("case_usage_requires_human_review")
        if document.data_type == DataType.PENALTY.value:
            parsed_payload = ParsedArtifactIntegrityService(self.settings).verify(
                document,
                session=session,
            )
            submitted_fragments = self._validate_penalty_fragments(document, envelope)
            expected_doc_id = self._parsed_nfra_doc_id(parsed_payload)
            try:
                validate_source_entry_locator(
                    envelope.source_entry_locator or {},
                    expected_nfra_doc_id=expected_doc_id,
                )
            except ValueError as exc:
                raise StructuredRecordError(str(exc)) from exc
        pilot_ids = self._validate_pilot_provenance(session, document, envelope)
        if document.data_type in {
            DataType.PRODUCT_DOCUMENT.value,
            DataType.REGULATORY_CASE.value,
        }:
            duplicate = session.scalar(
                select(entity_model).where(entity_model.document_id == document.id)
            )
            if duplicate:
                raise StructuredRecordError(
                    f"{document.data_type} already has a primary structured record"
                )
        values = draft.model_dump()
        if document.data_type == DataType.REGULATION.value:
            duplicates = session.scalars(
                select(Regulation).where(Regulation.document_id == document.id)
            )
            if any(
                all(getattr(existing, field_name) == value for field_name, value in values.items())
                for existing in duplicates
            ):
                raise StructuredRecordError("structured_draft_duplicate")
        existing_provenance = self._existing_provenance(
            session,
            document,
            pilot_ids,
        )
        evidence = envelope.field_evidence
        if evidence is None:
            if document.authenticity_type != AuthenticityType.DEMO_ONLY.value:
                raise StructuredRecordError("missing_field_evidence")
            validated_evidence = self._legacy_demo_evidence(document, values)
        else:
            try:
                validated_evidence = FieldEvidenceService(self.settings).validate(
                    session,
                    document,
                    values,
                    evidence,
                )
            except Exception as exc:
                raise StructuredRecordError(str(exc)) from exc
        if document.data_type == DataType.PENALTY.value:
            try:
                identity_material = build_penalty_identity_material(values, validated_evidence)
                expected_fragments = build_penalty_source_entry_fragments(
                    values,
                    validated_evidence,
                )
            except ValueError as exc:
                raise StructuredRecordError(str(exc)) from exc
            if submitted_fragments != expected_fragments:
                raise StructuredRecordError("penalty_source_entry_fragment_set_mismatch")
            actual_content_sha256 = source_entry_content_sha256(identity_material)
            if actual_content_sha256 != envelope.source_entry_content_sha256:
                raise StructuredRecordError("penalty_source_entry_content_sha256_mismatch")
            expected_fingerprint = penalty_source_entry_fingerprint(
                raw_artifact_sha256=document.sha256,
                source_entry_content_sha256=actual_content_sha256,
            )
            if draft.source_entry_fingerprint != expected_fingerprint:
                raise StructuredRecordError("penalty_source_entry_fingerprint_mismatch")
            duplicate_entry = session.scalar(
                select(Penalty.id).where(
                    Penalty.document_id == document.id,
                    (
                        (Penalty.source_entry_index == draft.source_entry_index)
                        | (Penalty.source_entry_fingerprint == draft.source_entry_fingerprint)
                    ),
                )
            )
            if duplicate_entry is not None:
                raise StructuredRecordError("penalty_source_entry_duplicate")
        record = entity_model(
            document_id=document.id,
            final_review_status=document.final_review_status,
            field_evidence_json=validated_evidence,
            **values,
        )
        try:
            session.add(record)
            session.flush()
            if isinstance(record, Penalty):
                self._mark_penalty_duplicate_candidates(session, document, record)
            if envelope.pilot_id is not None or isinstance(record, Penalty):
                entry_provenance: dict[str, Any] = {}
                if document.data_type == DataType.PENALTY.value:
                    entry_provenance = {
                        "source_entry_locator": envelope.source_entry_locator,
                        "source_entry_fragments": expected_fragments,
                        "source_entry_content_sha256": envelope.source_entry_content_sha256,
                    }
                metadata = dict(document.metadata_json)
                metadata["structured_draft_provenance"] = [
                    *existing_provenance,
                    {
                        "structured_record_id": record.id,
                        "record_type": document.data_type,
                        "pilot_id": envelope.pilot_id,
                        "draft_generation_method": envelope.draft_generation_method,
                        "draft_generation_version": envelope.draft_generation_version,
                        **entry_provenance,
                    },
                ]
                document.metadata_json = metadata
            if document.final_review_status == ReviewStatus.AUTO_VALIDATION_FAILED.value:
                metadata = dict(document.metadata_json)
                metadata.pop("automatic_validation", None)
                document.metadata_json = metadata
                StateMachineService.transition_document(
                    session,
                    document,
                    ReviewStatus.PARSED.value,
                    "structured draft imported after validation failure",
                )
            if commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        session.refresh(record)
        return cast(Regulation | Penalty | ProductDocument | RegulatoryCase, record)

    @staticmethod
    def _mark_penalty_duplicate_candidates(
        session: Session,
        document: SourceDocument,
        record: Penalty,
    ) -> None:
        possible_candidates = list(
            session.execute(
                select(Penalty, SourceDocument)
                .join(SourceDocument, SourceDocument.id == Penalty.document_id)
                .where(Penalty.document_id != document.id)
            )
        )
        signature_fields = (
            "punished_entity",
            "document_number",
            "illegal_facts",
            "penalty_result",
        )
        record_signature = tuple(getattr(record, name) for name in signature_fields)
        record_text = StructuredRecordService._penalty_similarity_text(record)
        candidates = [
            (candidate, candidate_document)
            for candidate, candidate_document in possible_candidates
            if (
                (bool(document.source_url) and candidate_document.source_url == document.source_url)
                or tuple(getattr(candidate, name) for name in signature_fields) == record_signature
                or (
                    bool(record_text)
                    and SequenceMatcher(
                        None,
                        record_text,
                        StructuredRecordService._penalty_similarity_text(candidate),
                        autojunk=False,
                    ).ratio()
                    >= PENALTY_DUPLICATE_SIMILARITY_THRESHOLD
                )
            )
        ]
        if candidates:
            record.duplicate_candidate = True
            for candidate, candidate_document in candidates:
                if not StructuredRecordService._duplicate_candidate_is_protected(
                    session, candidate_document
                ):
                    candidate.duplicate_candidate = True

    @staticmethod
    def _duplicate_candidate_is_protected(
        session: Session,
        document: SourceDocument,
    ) -> bool:
        protected_by_state = (
            document.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value
            or document.final_review_status
            in {
                ReviewStatus.APPROVED.value,
                ReviewStatus.APPROVED_WITH_REVISION.value,
                ReviewStatus.REJECTED.value,
                ReviewStatus.PENDING_SOURCE_VERIFICATION.value,
                ReviewStatus.REJECTED_HALLUCINATION.value,
                ReviewStatus.REJECTED_DUPLICATE.value,
                ReviewStatus.REJECTED_OUTDATED.value,
            }
            or document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
        )
        if protected_by_state:
            return True
        return (
            session.scalar(
                select(ReviewDecision.id)
                .where(
                    ReviewDecision.record_type == DataType.PENALTY.value,
                    ReviewDecision.record_id == document.id,
                )
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _parsed_nfra_doc_id(payload: dict[str, Any]) -> str | None:
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return None
        if metadata.get("source_format") != "nfra_public_json":
            return None
        nested = metadata.get("nfra")
        doc_id = nested.get("doc_id") if isinstance(nested, dict) else None
        if not isinstance(doc_id, str) or not doc_id:
            raise StructuredRecordError("penalty_source_entry_locator_invalid")
        return doc_id

    @staticmethod
    def _validate_penalty_fragments(
        document: SourceDocument,
        envelope: StructuredDraftEnvelope,
    ) -> list[dict[str, Any]]:
        raw_fragments = [item.model_dump() for item in (envelope.source_entry_fragments or [])]
        try:
            fragments = canonical_source_entry_fragments(raw_fragments)
        except ValueError as exc:
            raise StructuredRecordError(str(exc)) from exc
        raw_text = document.raw_text or ""
        if any(
            raw_text[item["start_offset"] : item["end_offset"]] != item["quote"]
            for item in fragments
        ):
            raise StructuredRecordError("penalty_source_entry_fragment_invalid")
        return fragments

    @staticmethod
    def _penalty_similarity_text(record: Penalty) -> str:
        values = (
            record.punished_entity,
            record.document_number,
            record.illegal_facts,
            record.penalty_result,
        )
        return "|".join(
            re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")) for value in values
        )

    @staticmethod
    def _validate_pilot_provenance(
        session: Session,
        document: SourceDocument,
        envelope: StructuredDraftEnvelope,
    ) -> set[str]:
        pilot_items = list(
            session.scalars(
                select(PilotCollectionItem).where(PilotCollectionItem.document_id == document.id)
            )
        )
        if pilot_items:
            if envelope.pilot_id is None or not any(
                item.pilot_id == envelope.pilot_id for item in pilot_items
            ):
                raise StructuredRecordError("pilot_id_document_mismatch")
            return {item.pilot_id for item in pilot_items}
        if envelope.pilot_id is not None:
            raise StructuredRecordError("pilot_id_document_mismatch")
        return set()

    @staticmethod
    def _existing_provenance(
        session: Session,
        document: SourceDocument,
        pilot_ids: set[str],
    ) -> list[dict[str, Any]]:
        raw_provenance = document.metadata_json.get("structured_draft_provenance", [])
        existing_record_ids = {
            record.id for record in StateMachineService.structured_records(session, document)
        }
        if not isinstance(raw_provenance, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("structured_record_id"), int)
            and item["structured_record_id"] in existing_record_ids
            and item.get("record_type") == document.data_type
            and (
                (
                    bool(pilot_ids)
                    and item.get("pilot_id") in pilot_ids
                    and isinstance(item.get("draft_generation_method"), str)
                    and bool(item["draft_generation_method"].strip())
                    and isinstance(item.get("draft_generation_version"), str)
                    and bool(item["draft_generation_version"].strip())
                )
                or (
                    not pilot_ids
                    and item.get("pilot_id") is None
                    and item.get("draft_generation_method") is None
                    and item.get("draft_generation_version") is None
                )
            )
            for item in raw_provenance
        ):
            raise StructuredRecordError("structured_draft_provenance_conflict")
        record_ids = [item["structured_record_id"] for item in raw_provenance]
        if len(record_ids) != len(set(record_ids)):
            raise StructuredRecordError("structured_draft_provenance_conflict")
        return [dict(item) for item in raw_provenance]

    @staticmethod
    def _legacy_demo_evidence(
        document: SourceDocument,
        values: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        quote = str(values.get("source_quote") or "")
        start = (document.raw_text or "").find(quote)
        if not quote or start < 0:
            raise StructuredRecordError("missing_field_evidence")
        evidence: dict[str, list[dict[str, Any]]] = {}
        for field_name in EVIDENCE_FIELDS.get(document.data_type, frozenset()):
            value = values.get(field_name)
            if value is None or not str(value).strip():
                continue
            evidence[field_name] = [
                {
                    "quote": quote,
                    "page_number": 1,
                    "start_offset": start,
                    "end_offset": start + len(quote),
                    "mode": "summary",
                    "transformation_note": "legacy_demo_compatibility",
                }
            ]
        return evidence
