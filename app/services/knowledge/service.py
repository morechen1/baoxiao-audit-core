from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    FieldEvidenceError,
    ParsedArtifactIntegrityError,
    RawArtifactIntegrityError,
)
from app.models import Penalty, RegulatoryCase, SourceDocument
from app.models.enums import (
    APPROVABLE_STATUSES,
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    RegulatoryCaseUsage,
)
from app.repositories import DocumentRepository
from app.services.field_evidence import EVIDENCE_FIELDS, FieldEvidenceService
from app.services.integrity import RawArtifactIntegrityService
from app.services.parsed_artifacts import ParsedArtifactIntegrityService
from app.services.penalty_identity import PenaltySourceIdentityService
from app.services.state_machine import StateMachineService

if TYPE_CHECKING:
    from app.services.knowledge.materialization import (
        RebuildAllSummary,
        RebuildSummary,
        VerificationReport,
    )


@dataclass
class IndexSummary:
    indexed: int = 0
    rejected: dict[int, list[str]] = field(default_factory=dict)


class KnowledgeIndexService:
    """Applies a strict whitelist trust gate and records index state only."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def index_approved(self, session: Session) -> IndexSummary:
        summary = IndexSummary()
        for document in DocumentRepository(session).list():
            if document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value:
                continue
            reasons = self.rejection_reasons(session, document)
            if reasons:
                summary.rejected[document.id] = reasons
                continue
            if document.data_type == DataType.REGULATORY_CASE.value:
                record = next(
                    (
                        value
                        for value in StateMachineService.structured_records(session, document)
                        if isinstance(value, RegulatoryCase)
                    ),
                    None,
                )
                if record is not None:
                    metadata = dict(document.metadata_json)
                    metadata["knowledge_index_payload"] = self._regulatory_case_payload(
                        document, record
                    )
                    document.metadata_json = metadata
            document.knowledge_index_status = KnowledgeIndexStatus.INDEXED.value
            document.indexed_at = datetime.now(UTC)
            summary.indexed += 1
        session.commit()
        return summary

    def rebuild_document_chunks(self, session: Session, document_id: int) -> RebuildSummary:
        from app.services.knowledge.materialization import (
            TrustedKnowledgeMaterializationService,
        )

        return TrustedKnowledgeMaterializationService(self.settings).rebuild_document_chunks(
            session, document_id
        )

    def rebuild_all_trusted_chunks(self, session: Session) -> RebuildAllSummary:
        from app.services.knowledge.materialization import (
            TrustedKnowledgeMaterializationService,
        )

        return TrustedKnowledgeMaterializationService(self.settings).rebuild_all_trusted_chunks(
            session
        )

    def retire_document_chunks(self, session: Session, document_id: int) -> int:
        from app.services.knowledge.materialization import (
            TrustedKnowledgeMaterializationService,
        )

        return TrustedKnowledgeMaterializationService(self.settings).retire_document_chunks(
            session, document_id
        )

    def verify_chunks(self, session: Session) -> VerificationReport:
        from app.services.knowledge.materialization import (
            TrustedKnowledgeMaterializationService,
        )

        return TrustedKnowledgeMaterializationService(self.settings).verify(session)

    def rejection_reasons(self, session: Session, document: SourceDocument) -> list[str]:
        reasons: list[str] = []
        try:
            RawArtifactIntegrityService(self.settings).verify(document)
        except RawArtifactIntegrityError as exc:
            reasons.append(str(exc))
        try:
            ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
        except (ParsedArtifactIntegrityError, RawArtifactIntegrityError) as exc:
            reasons.append(str(exc))
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
            if isinstance(record, RegulatoryCase):
                if record.case_usage == RegulatoryCaseUsage.EXTERNAL_TEST_CANDIDATE.value:
                    reasons.append("external_test_candidate_not_indexable")
                elif record.case_usage == RegulatoryCaseUsage.SEALED_EXTERNAL_TEST.value:
                    reasons.append("sealed_external_test_not_indexable")
                elif record.case_usage != RegulatoryCaseUsage.RETRIEVAL_ONLY.value:
                    reasons.append("case_usage_not_indexable")
            if isinstance(record, Penalty):
                try:
                    PenaltySourceIdentityService(self.settings).validate(
                        session,
                        document,
                        record,
                    )
                except ValueError as exc:
                    reasons.append(
                        "penalty_source_identity_reimport_required"
                        if str(exc) == "penalty_source_identity_reimport_required"
                        else "penalty_source_identity_consistency_failed"
                    )
            if record.final_review_status != document.final_review_status:
                reasons.append("structured_status_mismatch")
            quote = getattr(record, "source_quote", None)
            if not quote or quote not in (document.raw_text or ""):
                reasons.append("source_quote_not_found")
            try:
                FieldEvidenceService(self.settings).validate(
                    session,
                    document,
                    {
                        field_name: getattr(record, field_name)
                        for field_name in EVIDENCE_FIELDS.get(document.data_type, frozenset())
                    },
                    getattr(record, "field_evidence_json", {}),
                )
            except (
                FieldEvidenceError,
                ParsedArtifactIntegrityError,
                RawArtifactIntegrityError,
            ) as exc:
                reasons.append(str(exc))
        return sorted(set(reasons))

    @staticmethod
    def _regulatory_case_payload(
        document: SourceDocument,
        record: RegulatoryCase,
    ) -> dict[str, object]:
        return {
            "case_title": record.case_title,
            "case_category": record.case_category,
            "scenario_text": record.scenario_text,
            "marketing_wording": record.marketing_wording,
            "case_facts": record.case_facts,
            "regulatory_analysis": record.regulatory_analysis,
            "consumer_advice": record.consumer_advice,
            "publisher": record.publisher,
            "published_at": record.published_at.isoformat() if record.published_at else None,
            "source_url": document.source_url,
            "document_id": document.id,
            "record_id": record.id,
        }
