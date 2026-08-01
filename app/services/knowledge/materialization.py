from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import TrustGateError
from app.models import (
    AuthenticityDecisionLog,
    DocumentOccurrence,
    KnowledgeChunk,
    KnowledgeIndexRun,
    Penalty,
    PilotCollectionItem,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import APPROVABLE_STATUSES, AuthenticityType, DataType, KnowledgeIndexStatus
from app.services.knowledge.chunks import (
    CHUNKER_VERSION,
    RANKING_VERSION,
    build_record_chunks,
    recompute_chunk_identity,
)
from app.services.knowledge.normalization import TOKENIZER_VERSION, trusted_lexical_normalize
from app.services.penalty_identity import PenaltySourceIdentityService
from app.services.review_payload import canonical_json_bytes
from app.services.state_machine import StateMachineService

TRUSTED_INDEX_VERSION = "trusted_knowledge_index_v1"
MATERIALIZABLE_TYPES = {
    DataType.REGULATION.value,
    DataType.PRODUCT_DOCUMENT.value,
    DataType.PENALTY.value,
}


@dataclass
class RebuildSummary:
    source_document_id: int
    records: int = 0
    active_chunks: int = 0
    created: int = 0
    reactivated: int = 0
    retired: int = 0


@dataclass
class RebuildAllSummary:
    run_id: str
    documents: int = 0
    records: int = 0
    chunks: int = 0
    created: int = 0
    reactivated: int = 0
    retired: int = 0
    errors: dict[int, str] = field(default_factory=dict)


@dataclass
class VerificationReport:
    valid: bool
    eligible_source_documents: int
    active_chunks: int
    retired_chunks: int
    chunks_by_record_type: dict[str, int]
    chunks_by_document: dict[int, int]
    hash_mismatches: int
    orphan_chunks: int
    ineligible_active_chunks: int
    regulatory_case_active_chunks: int
    penalty_identity_failures: int
    source_locator_missing: int
    duplicate_active_identities: int
    errors: list[str]


class TrustedKnowledgeMaterializationService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def rebuild_document_chunks(self, session: Session, document_id: int) -> RebuildSummary:
        document = session.get(SourceDocument, document_id)
        if document is None:
            raise TrustGateError("knowledge_document_not_index_eligible")
        self._ensure_eligible(session, document)
        records = StateMachineService.structured_records(session, document)
        pilot_ids = list(
            session.scalars(
                select(PilotCollectionItem.pilot_id)
                .where(PilotCollectionItem.document_id == document.id)
                .order_by(PilotCollectionItem.id)
            )
        )
        pilot_id = pilot_ids[0] if len(pilot_ids) == 1 else None
        verified_source = self._verified_source(session, document)
        quality = session.scalar(
            select(ReviewDecision.evidence_quality)
            .where(
                ReviewDecision.record_type == document.data_type,
                ReviewDecision.record_id == document.id,
            )
            .order_by(ReviewDecision.id.desc())
        )
        provenance_by_record = self._provenance_by_record(document)
        candidates = []
        try:
            for record in records:
                if isinstance(record, Penalty):
                    try:
                        PenaltySourceIdentityService(self.settings).validate(
                            session, document, record
                        )
                    except ValueError as exc:
                        code = (
                            "penalty_source_identity_reimport_required"
                            if str(exc) == "penalty_source_identity_reimport_required"
                            else "knowledge_penalty_identity_invalid"
                        )
                        raise TrustGateError(code) from exc
                candidates.extend(
                    build_record_chunks(
                        document,
                        record,
                        pilot_id=pilot_id,
                        provenance=provenance_by_record.get(record.id),
                        verified_source=verified_source,
                        evidence_quality=quality,
                    )
                )
        except TrustGateError:
            raise
        except Exception as exc:
            raise TrustGateError("knowledge_chunk_build_failed") from exc
        identities = [candidate.chunk_identity_sha256 for candidate in candidates]
        if not candidates:
            raise TrustGateError("knowledge_chunk_build_failed")
        if len(identities) != len(set(identities)):
            raise TrustGateError("knowledge_chunk_duplicate_identity")

        summary = RebuildSummary(source_document_id=document.id, records=len(records))
        now = datetime.now(UTC)
        try:
            with session.begin_nested():
                active = list(
                    session.scalars(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.source_document_id == document.id,
                            KnowledgeChunk.is_active.is_(True),
                        )
                    )
                )
                candidate_identities = set(identities)
                for chunk in active:
                    if chunk.chunk_identity_sha256 not in candidate_identities:
                        chunk.is_active = False
                        chunk.retired_at = now
                        summary.retired += 1
                for candidate in candidates:
                    existing = session.scalar(
                        select(KnowledgeChunk).where(
                            KnowledgeChunk.chunk_identity_sha256 == candidate.chunk_identity_sha256
                        )
                    )
                    if existing is None:
                        existing = KnowledgeChunk(
                            source_document_id=document.id,
                            record_type=candidate.record_type,
                            structured_record_id=candidate.structured_record_id,
                            pilot_id=candidate.pilot_id,
                            portable_record_key=candidate.portable_record_key,
                            chunk_kind=candidate.chunk_kind,
                            chunk_ordinal=candidate.chunk_ordinal,
                            title=candidate.title,
                            text=candidate.text,
                            normalized_text=candidate.normalized_text,
                            lexical_tokens=candidate.lexical_tokens,
                            authority=candidate.authority,
                            relevant_date=candidate.relevant_date,
                            source_url=candidate.source_url,
                            source_locator_json=candidate.source_locator,
                            evidence_reference_json=candidate.evidence_references,
                            evidence_quality=candidate.evidence_quality,
                            authenticity_status=document.authenticity_type,
                            review_status=document.final_review_status,
                            source_payload_hash=candidate.source_payload_hash,
                            chunk_content_sha256=candidate.chunk_content_sha256,
                            chunk_identity_sha256=candidate.chunk_identity_sha256,
                            tokenizer_version=TOKENIZER_VERSION,
                            chunker_version=CHUNKER_VERSION,
                            ranking_version=RANKING_VERSION,
                            is_active=True,
                        )
                        session.add(existing)
                        summary.created += 1
                    else:
                        if (
                            existing.source_document_id != document.id
                            or existing.chunk_content_sha256 != candidate.chunk_content_sha256
                            or existing.text != candidate.text
                        ):
                            raise TrustGateError("knowledge_chunk_hash_mismatch")
                        if not existing.is_active:
                            existing.is_active = True
                            existing.retired_at = None
                            summary.reactivated += 1
                session.flush()
        except TrustGateError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise TrustGateError("knowledge_chunk_build_failed") from exc
        session.commit()
        summary.active_chunks = len(candidates)
        return summary

    def rebuild_all_trusted_chunks(self, session: Session) -> RebuildAllSummary:
        run = KnowledgeIndexRun(
            run_id=uuid4().hex,
            status="running",
            index_version=TRUSTED_INDEX_VERSION,
            source_document_count=0,
            record_count=0,
            chunk_count=0,
        )
        session.add(run)
        session.commit()
        summary = RebuildAllSummary(run_id=run.run_id)
        document_ids = list(
            session.scalars(
                select(SourceDocument.id)
                .where(
                    SourceDocument.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value,
                    SourceDocument.data_type.in_(MATERIALIZABLE_TYPES),
                )
                .order_by(SourceDocument.id)
            )
        )
        try:
            for document_id in document_ids:
                try:
                    result = self.rebuild_document_chunks(session, document_id)
                except TrustGateError as exc:
                    summary.errors[document_id] = str(exc)
                    raise
                summary.documents += 1
                summary.records += result.records
                summary.chunks += result.active_chunks
                summary.created += result.created
                summary.reactivated += result.reactivated
                summary.retired += result.retired
            active_identities = list(
                session.scalars(
                    select(KnowledgeChunk.chunk_identity_sha256)
                    .where(KnowledgeChunk.is_active.is_(True))
                    .order_by(KnowledgeChunk.chunk_identity_sha256)
                )
            )
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            run.source_document_count = summary.documents
            run.record_count = summary.records
            run.chunk_count = summary.chunks
            run.payload_hash = hashlib.sha256(canonical_json_bytes(active_identities)).hexdigest()
            session.commit()
            return summary
        except TrustGateError as exc:
            stored_run = session.get(KnowledgeIndexRun, run.id)
            if stored_run is not None:
                stored_run.status = "failed"
                stored_run.completed_at = datetime.now(UTC)
                stored_run.error_code = str(exc)
                stored_run.source_document_count = summary.documents
                stored_run.record_count = summary.records
                stored_run.chunk_count = summary.chunks
                session.commit()
            raise

    def retire_document_chunks(self, session: Session, document_id: int) -> int:
        chunks = list(
            session.scalars(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.source_document_id == document_id,
                    KnowledgeChunk.is_active.is_(True),
                )
            )
        )
        now = datetime.now(UTC)
        for chunk in chunks:
            chunk.is_active = False
            chunk.retired_at = now
        session.commit()
        return len(chunks)

    def verify(self, session: Session) -> VerificationReport:
        chunks = list(session.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.id)))
        active = [chunk for chunk in chunks if chunk.is_active]
        errors: list[str] = []
        hash_mismatches = 0
        orphan_chunks = 0
        ineligible = 0
        regulatory_cases = 0
        penalty_failures = 0
        locator_missing = 0
        identity_counts: dict[str, int] = {}
        by_type: dict[str, int] = {}
        by_document: dict[int, int] = {}
        for chunk in active:
            identity_counts[chunk.chunk_identity_sha256] = (
                identity_counts.get(chunk.chunk_identity_sha256, 0) + 1
            )
            by_type[chunk.record_type] = by_type.get(chunk.record_type, 0) + 1
            by_document[chunk.source_document_id] = by_document.get(chunk.source_document_id, 0) + 1
            document = session.get(SourceDocument, chunk.source_document_id)
            if document is None:
                orphan_chunks += 1
                errors.append(f"orphan:{chunk.chunk_identity_sha256}")
                continue
            if chunk.record_type == DataType.REGULATORY_CASE.value:
                regulatory_cases += 1
                errors.append(f"regulatory_case:{chunk.chunk_identity_sha256}")
            if not self._eligible_state(document):
                ineligible += 1
                errors.append(f"ineligible:{chunk.chunk_identity_sha256}")
            content_hash = hashlib.sha256(
                trusted_lexical_normalize(chunk.text).encode("utf-8")
            ).hexdigest()
            identity_hash = recompute_chunk_identity(
                raw_artifact_sha256=document.sha256,
                record_type=chunk.record_type,
                portable_record_key_value=chunk.portable_record_key or "",
                chunk_kind=chunk.chunk_kind,
                chunk_ordinal=chunk.chunk_ordinal,
                chunk_content_sha256=content_hash,
                chunker_version=chunk.chunker_version,
                tokenizer_version=chunk.tokenizer_version,
            )
            if (
                content_hash != chunk.chunk_content_sha256
                or identity_hash != chunk.chunk_identity_sha256
            ):
                hash_mismatches += 1
                errors.append(f"hash:{chunk.chunk_identity_sha256}")
            if not chunk.source_locator_json or not chunk.source_url:
                locator_missing += 1
                errors.append(f"locator:{chunk.chunk_identity_sha256}")
            if chunk.record_type == DataType.PENALTY.value:
                record = session.get(Penalty, chunk.structured_record_id)
                try:
                    if record is None:
                        raise ValueError("missing")
                    PenaltySourceIdentityService(self.settings).validate(session, document, record)
                except ValueError:
                    penalty_failures += 1
                    errors.append(f"penalty_identity:{chunk.chunk_identity_sha256}")
        duplicates = sum(value > 1 for value in identity_counts.values())
        eligible_documents = (
            session.scalar(
                select(func.count(SourceDocument.id)).where(
                    SourceDocument.final_review_status.in_(APPROVABLE_STATUSES),
                    SourceDocument.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value,
                    SourceDocument.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value,
                    SourceDocument.data_type.in_(MATERIALIZABLE_TYPES),
                )
            )
            or 0
        )
        return VerificationReport(
            valid=not errors and duplicates == 0,
            eligible_source_documents=eligible_documents,
            active_chunks=len(active),
            retired_chunks=len(chunks) - len(active),
            chunks_by_record_type=dict(sorted(by_type.items())),
            chunks_by_document=dict(sorted(by_document.items())),
            hash_mismatches=hash_mismatches,
            orphan_chunks=orphan_chunks,
            ineligible_active_chunks=ineligible,
            regulatory_case_active_chunks=regulatory_cases,
            penalty_identity_failures=penalty_failures,
            source_locator_missing=locator_missing,
            duplicate_active_identities=duplicates,
            errors=sorted(errors),
        )

    def _ensure_eligible(self, session: Session, document: SourceDocument) -> None:
        if document.data_type not in MATERIALIZABLE_TYPES or not self._eligible_state(document):
            raise TrustGateError("knowledge_document_not_index_eligible")
        from app.services.knowledge.service import KnowledgeIndexService

        reasons = KnowledgeIndexService(self.settings).rejection_reasons(session, document)
        if "penalty_source_identity_reimport_required" in reasons:
            raise TrustGateError("penalty_source_identity_reimport_required")
        if "penalty_source_identity_consistency_failed" in reasons:
            raise TrustGateError("knowledge_penalty_identity_invalid")
        if reasons:
            raise TrustGateError("knowledge_document_validation_failed")

    @staticmethod
    def _eligible_state(document: SourceDocument) -> bool:
        return (
            document.final_review_status in APPROVABLE_STATUSES
            and document.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value
            and document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
            and document.data_type in MATERIALIZABLE_TYPES
        )

    @staticmethod
    def _provenance_by_record(document: SourceDocument) -> dict[int, dict[str, object]]:
        raw = document.metadata_json.get("structured_draft_provenance", [])
        return {
            int(item["structured_record_id"]): item
            for item in raw
            if isinstance(item, dict) and isinstance(item.get("structured_record_id"), int)
        }

    @staticmethod
    def _verified_source(session: Session, document: SourceDocument) -> dict[str, object]:
        decisions = list(
            session.scalars(
                select(AuthenticityDecisionLog)
                .where(AuthenticityDecisionLog.document_id == document.id)
                .order_by(AuthenticityDecisionLog.id)
            )
        )
        if len(decisions) != 1:
            raise TrustGateError("knowledge_document_not_index_eligible")
        occurrence = session.get(DocumentOccurrence, decisions[0].verified_occurrence_id)
        if occurrence is None or occurrence.document_id != document.id:
            raise TrustGateError("knowledge_document_not_index_eligible")
        return {
            "source_url": occurrence.source_url,
            "final_url": occurrence.final_url,
        }
