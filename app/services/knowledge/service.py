from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import SourceDocument
from app.models.enums import (
    APPROVABLE_STATUSES,
    AuthenticityType,
    KnowledgeIndexStatus,
)
from app.repositories import DocumentRepository
from app.services.state_machine import StateMachineService


@dataclass
class IndexSummary:
    indexed: int = 0
    rejected: dict[int, list[str]] = field(default_factory=dict)


class KnowledgeIndexService:
    """Applies a strict whitelist trust gate and records index state only."""

    def index_approved(self, session: Session) -> IndexSummary:
        summary = IndexSummary()
        for document in DocumentRepository(session).list():
            if document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value:
                continue
            reasons = self.rejection_reasons(session, document)
            if reasons:
                summary.rejected[document.id] = reasons
                continue
            document.knowledge_index_status = KnowledgeIndexStatus.INDEXED.value
            document.indexed_at = datetime.now(UTC)
            summary.indexed += 1
        session.commit()
        return summary

    @staticmethod
    def rejection_reasons(session: Session, document: SourceDocument) -> list[str]:
        reasons: list[str] = []
        if document.final_review_status not in APPROVABLE_STATUSES:
            reasons.append("review_status_not_approved")
        if document.authenticity_type != AuthenticityType.VERIFIED_PUBLIC.value:
            reasons.append("authenticity_not_verified_public")
        if document.parse_status != "parsed":
            reasons.append("document_not_parsed")
        validation = document.metadata_json.get("automatic_validation", {})
        if validation.get("valid") is not True:
            reasons.append("automatic_validation_not_valid")
        if not document.raw_text or not document.raw_text.strip():
            reasons.append("raw_text_missing")
        if not re.fullmatch(r"[0-9a-f]{64}", document.sha256 or ""):
            reasons.append("sha256_invalid")
        records = StateMachineService.structured_records(session, document)
        if not records:
            reasons.append("missing_structured_record")
        for record in records:
            if record.final_review_status != document.final_review_status:
                reasons.append("structured_status_mismatch")
            quote = getattr(record, "source_quote", None)
            if not quote or quote not in (document.raw_text or ""):
                reasons.append("source_quote_not_found")
        return sorted(set(reasons))
