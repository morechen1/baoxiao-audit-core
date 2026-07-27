from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import StructuredRecordError
from app.models import Penalty, ProductDocument, Regulation, SourceDocument
from app.models.enums import DataType, ReviewStatus
from app.schemas.structured import (
    PenaltyDraft,
    ProductDocumentDraft,
    RegulationDraft,
    StructuredDraftEnvelope,
)
from app.services.state_machine import StateMachineService

DRAFT_MODELS = {
    DataType.REGULATION.value: RegulationDraft,
    DataType.PENALTY.value: PenaltyDraft,
    DataType.PRODUCT_DOCUMENT.value: ProductDocumentDraft,
}

ENTITY_MODELS = {
    DataType.REGULATION.value: Regulation,
    DataType.PENALTY.value: Penalty,
    DataType.PRODUCT_DOCUMENT.value: ProductDocument,
}


class StructuredRecordService:
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
    ) -> Regulation | Penalty | ProductDocument:
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
        if document.data_type in {
            DataType.PENALTY.value,
            DataType.PRODUCT_DOCUMENT.value,
        }:
            duplicate = session.scalar(
                select(entity_model).where(entity_model.document_id == document.id)
            )
            if duplicate:
                raise StructuredRecordError(
                    f"{document.data_type} already has a primary structured record"
                )
        values = draft.model_dump()
        record = entity_model(
            document_id=document.id,
            final_review_status=document.final_review_status,
            **values,
        )
        session.add(record)
        session.flush()
        if document.final_review_status == ReviewStatus.AUTO_VALIDATION_FAILED.value:
            StateMachineService.transition_document(
                session,
                document,
                ReviewStatus.PARSED.value,
                "structured draft imported after validation failure",
            )
        session.commit()
        session.refresh(record)
        return cast(Regulation | Penalty | ProductDocument, record)
