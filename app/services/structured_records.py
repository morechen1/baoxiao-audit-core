from __future__ import annotations

import json
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
    SourceDocument,
)
from app.models.enums import (
    AuthenticityType,
    DataType,
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


class StructuredRecordService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def import_jsonl(self, session: Session, path: Path) -> tuple[int, list[str]]:
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            raise StructuredRecordError("Structured drafts must be an existing JSONL file")
        imported = 0
        errors: list[str] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                envelope = StructuredDraftEnvelope.model_validate_json(line)
                self.import_draft(session, envelope)
                imported += 1
            except (
                json.JSONDecodeError,
                PydanticValidationError,
                StructuredRecordError,
            ) as exc:
                session.rollback()
                errors.append(f"line {line_number}: {exc}")
        return imported, errors

    def import_draft(
        self, session: Session, envelope: StructuredDraftEnvelope
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
        pilot_ids = self._validate_pilot_provenance(session, document, envelope)
        if document.data_type in {
            DataType.PENALTY.value,
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
        record = entity_model(
            document_id=document.id,
            final_review_status=document.final_review_status,
            field_evidence_json=validated_evidence,
            **values,
        )
        try:
            session.add(record)
            session.flush()
            if envelope.pilot_id is not None:
                metadata = dict(document.metadata_json)
                metadata["structured_draft_provenance"] = [
                    *existing_provenance,
                    {
                        "structured_record_id": record.id,
                        "record_type": document.data_type,
                        "pilot_id": envelope.pilot_id,
                        "draft_generation_method": envelope.draft_generation_method,
                        "draft_generation_version": envelope.draft_generation_version,
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
            session.commit()
        except Exception:
            session.rollback()
            raise
        session.refresh(record)
        return cast(Regulation | Penalty | ProductDocument | RegulatoryCase, record)

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
            and item.get("pilot_id") in pilot_ids
            and isinstance(item.get("draft_generation_method"), str)
            and bool(item["draft_generation_method"].strip())
            and isinstance(item.get("draft_generation_version"), str)
            and bool(item["draft_generation_version"].strip())
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
