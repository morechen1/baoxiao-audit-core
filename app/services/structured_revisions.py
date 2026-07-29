from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import FieldEvidenceError, StructuredRecordError
from app.models import SourceDocument, StructuredDraftRevision
from app.models.enums import DataType, ReviewStatus
from app.schemas.structured import StructuredDraftRevisionEnvelope
from app.services.field_evidence import EVIDENCE_FIELDS, FieldEvidenceService
from app.services.parsed_artifacts import ParsedArtifactIntegrityService
from app.services.state_machine import StateMachineService
from app.services.structured_records import DRAFT_MODELS, ENTITY_MODELS

PENALTY_SOURCE_IDENTITY_FIELDS = frozenset(
    {
        "source_entry_index",
        "source_entry_fingerprint",
        "source_entry_content_sha256",
        "source_entry_fragments",
        "source_entry_locator",
    }
)


class StructuredDraftRevisionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def import_jsonl(
        self,
        session: Session,
        path: Path,
        *,
        reason: str,
        actor: str,
    ) -> tuple[int, list[str]]:
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            raise StructuredRecordError("revision_file_invalid")
        revised = 0
        errors: list[str] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                envelope = StructuredDraftRevisionEnvelope.model_validate_json(line)
                self.revise(session, envelope, reason=reason, actor=actor)
                revised += 1
            except (
                json.JSONDecodeError,
                PydanticValidationError,
                StructuredRecordError,
            ) as exc:
                session.rollback()
                errors.append(f"line {line_number}: {exc}")
        return revised, errors

    def revise(
        self,
        session: Session,
        envelope: StructuredDraftRevisionEnvelope,
        *,
        reason: str,
        actor: str,
    ) -> Any:
        document = session.get(SourceDocument, envelope.document_id)
        if not document or document.data_type != envelope.record_type.value:
            raise StructuredRecordError("revision_record_mismatch")
        if document.final_review_status not in {
            ReviewStatus.PARSED.value,
            ReviewStatus.AUTO_VALIDATION_FAILED.value,
        }:
            raise StructuredRecordError("structured_record_locked")
        if not reason.strip() or not actor.strip():
            raise StructuredRecordError("revision_audit_fields_required")
        ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
        entity_model: Any = ENTITY_MODELS.get(document.data_type)
        draft_model: Any = DRAFT_MODELS.get(document.data_type)
        if entity_model is None or draft_model is None:
            raise StructuredRecordError("unsupported_revision_record_type")
        if envelope.action == "add":
            if document.data_type != DataType.REGULATION.value:
                raise StructuredRecordError("only_regulation_supports_add_delete")
            target: Any = entity_model(document_id=document.id)
            previous_fields: dict[str, Any] = {}
            previous_evidence: dict[str, Any] = {}
            candidate = dict(envelope.fields)
            evidence = envelope.field_evidence
        else:
            target = session.get(entity_model, envelope.structured_record_id)
            if not target or target.document_id != document.id:
                raise StructuredRecordError("revision_record_mismatch")
            previous_fields = {name: getattr(target, name) for name in draft_model.model_fields}
            previous_evidence = dict(target.field_evidence_json)
            if envelope.action == "delete":
                if document.data_type != DataType.REGULATION.value:
                    raise StructuredRecordError("only_regulation_supports_add_delete")
                self._audit(
                    session,
                    document,
                    target.id,
                    "delete",
                    previous_fields,
                    {},
                    previous_evidence,
                    {},
                    reason,
                    actor,
                )
                session.delete(target)
                self._reset_validation(session, document, reason)
                session.commit()
                return None
            candidate = {**previous_fields, **envelope.fields}
            evidence = {**previous_evidence, **envelope.field_evidence}
        changed_fields = set(envelope.fields)
        if (
            document.data_type == DataType.PENALTY.value
            and changed_fields & PENALTY_SOURCE_IDENTITY_FIELDS
        ):
            raise StructuredRecordError("penalty_source_identity_locked")
        if not changed_fields.issubset(draft_model.model_fields):
            raise StructuredRecordError("revision_field_not_allowed")
        if document.data_type == DataType.REGULATORY_CASE.value and "case_usage" in changed_fields:
            raise StructuredRecordError("case_usage_requires_human_review")
        changed_evidence_fields = {
            field
            for field in changed_fields & EVIDENCE_FIELDS[document.data_type]
            if candidate.get(field) is not None and str(candidate[field]).strip()
        }
        if set(envelope.field_evidence) != changed_evidence_fields:
            raise StructuredRecordError("revision_evidence_must_match_fields")
        for field in changed_fields & EVIDENCE_FIELDS[document.data_type]:
            if candidate.get(field) is None or not str(candidate[field]).strip():
                evidence.pop(field, None)
        try:
            validated_draft = draft_model.model_validate(candidate).model_dump()
            validated_evidence = FieldEvidenceService(self.settings).validate(
                session,
                document,
                validated_draft,
                evidence,
            )
        except (PydanticValidationError, FieldEvidenceError) as exc:
            raise StructuredRecordError(str(exc)) from exc
        if envelope.action == "add":
            target = entity_model(
                document_id=document.id,
                final_review_status=document.final_review_status,
                field_evidence_json=validated_evidence,
                **validated_draft,
            )
            session.add(target)
            session.flush()
        else:
            for key, value in validated_draft.items():
                setattr(target, key, value)
            target.field_evidence_json = validated_evidence
            session.flush()
        self._audit(
            session,
            document,
            target.id,
            envelope.action,
            previous_fields,
            validated_draft,
            previous_evidence,
            validated_evidence,
            reason,
            actor,
        )
        self._reset_validation(session, document, reason)
        session.commit()
        return target

    @staticmethod
    def _audit(
        session: Session,
        document: SourceDocument,
        record_id: int | None,
        action: str,
        previous_fields: dict[str, Any],
        new_fields: dict[str, Any],
        previous_evidence: dict[str, Any],
        new_evidence: dict[str, Any],
        reason: str,
        actor: str,
    ) -> None:
        session.add(
            StructuredDraftRevision(
                document_id=document.id,
                record_type=document.data_type,
                structured_record_id=record_id,
                action=action,
                previous_fields_json=StructuredDraftRevisionService._json_values(previous_fields),
                new_fields_json=StructuredDraftRevisionService._json_values(new_fields),
                previous_evidence_json=previous_evidence,
                new_evidence_json=new_evidence,
                reason=reason.strip(),
                actor=actor.strip(),
            )
        )

    @staticmethod
    def _json_values(values: dict[str, Any]) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            json.loads(json.dumps(values, ensure_ascii=False, default=str)),
        )

    @staticmethod
    def _reset_validation(
        session: Session,
        document: SourceDocument,
        reason: str,
    ) -> None:
        metadata = dict(document.metadata_json)
        metadata.pop("automatic_validation", None)
        metadata.pop("post_review_validation", None)
        document.metadata_json = metadata
        if document.final_review_status == ReviewStatus.AUTO_VALIDATION_FAILED.value:
            StateMachineService.transition_document(
                session,
                document,
                ReviewStatus.PARSED.value,
                f"structured draft revised: {reason.strip()}",
            )
