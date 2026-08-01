from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
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
    ChunkCandidate,
    build_document_chunks,
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
    missing_expected_chunks: int
    extra_active_chunks: int
    canonical_mismatches: int
    structured_record_orphans: int
    duplicate_document_level_chunks: int
    eligibility_drift_documents: int
    errors: list[str]


class TrustedKnowledgeMaterializationService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build_expected_document_chunks(
        self, session: Session, document: SourceDocument
    ) -> tuple[list[Any], list[ChunkCandidate]]:
        """Apply every trust gate once and build the sole canonical candidate set."""
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
        candidates: list[ChunkCandidate] = []
        try:
            candidates.extend(
                build_document_chunks(
                    document,
                    records,
                    pilot_id=pilot_id,
                    verified_source=verified_source,
                    evidence_quality=quality,
                )
            )
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
        return records, candidates

    def rebuild_document_chunks(self, session: Session, document_id: int) -> RebuildSummary:
        document = session.get(SourceDocument, document_id)
        if document is None:
            raise TrustGateError("knowledge_document_not_index_eligible")
        records, candidates = self.build_expected_document_chunks(session, document)
        identities = [candidate.chunk_identity_sha256 for candidate in candidates]
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
                            authority_filter_text=candidate.authority_filter_text,
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
                        self._apply_canonical_values(existing, candidate, document)
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
        documents = list(session.scalars(select(SourceDocument).order_by(SourceDocument.id)))
        try:
            for document in documents:
                has_active = (
                    session.scalar(
                        select(KnowledgeChunk.id).where(
                            KnowledgeChunk.source_document_id == document.id,
                            KnowledgeChunk.is_active.is_(True),
                        )
                    )
                    is not None
                )
                claims_materializable = (
                    document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
                    and document.data_type in MATERIALIZABLE_TYPES
                )
                if not has_active and not claims_materializable:
                    continue
                if not self._eligible_state(document):
                    summary.retired += self._retire_active(session, document.id)
                    session.commit()
                    continue
                try:
                    self._ensure_eligible(session, document)
                except TrustGateError as exc:
                    summary.retired += self._retire_active(session, document.id)
                    session.commit()
                    summary.errors[document.id] = str(exc)
                    raise
                try:
                    result = self.rebuild_document_chunks(session, document.id)
                except TrustGateError as exc:
                    if str(exc) in {
                        "knowledge_regulation_document_fields_inconsistent",
                        "knowledge_chunk_duplicate_identity",
                    }:
                        summary.retired += self._retire_active(session, document.id)
                        session.commit()
                    summary.errors[document.id] = str(exc)
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
            run.chunk_count = len(active_identities)
            run.payload_hash = hashlib.sha256(canonical_json_bytes(active_identities)).hexdigest()
            session.commit()
            summary.chunks = len(active_identities)
            return summary
        except TrustGateError as exc:
            stored_run = session.get(KnowledgeIndexRun, run.id)
            if stored_run is not None:
                stored_run.status = "failed"
                stored_run.completed_at = datetime.now(UTC)
                stored_run.error_code = str(exc)
                stored_run.source_document_count = summary.documents
                stored_run.record_count = summary.records
                stored_run.chunk_count = (
                    session.query(KnowledgeChunk).filter_by(is_active=True).count()
                )
                session.commit()
            raise

    def retire_document_chunks(self, session: Session, document_id: int) -> int:
        retired = self._retire_active(session, document_id)
        session.commit()
        return retired

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
        missing_expected = 0
        extra_active = 0
        canonical_mismatches = 0
        structured_orphans = 0
        eligibility_drift = 0
        identity_counts: dict[str, int] = defaultdict(int)
        by_type: dict[str, int] = defaultdict(int)
        by_document: dict[int, int] = defaultdict(int)
        active_by_document: dict[int, list[KnowledgeChunk]] = defaultdict(list)

        for chunk in active:
            identity_counts[chunk.chunk_identity_sha256] += 1
            by_type[chunk.record_type] += 1
            by_document[chunk.source_document_id] += 1
            active_by_document[chunk.source_document_id].append(chunk)
            document = session.get(SourceDocument, chunk.source_document_id)
            if document is None:
                orphan_chunks += 1
                errors.append(f"knowledge_chunk_source_document_missing:{chunk.id}")
                continue
            if chunk.record_type == DataType.REGULATORY_CASE.value:
                regulatory_cases += 1
                errors.append(f"knowledge_regulatory_case_active:{chunk.id}")
            content_hash = hashlib.sha256(
                trusted_lexical_normalize(chunk.text).encode("utf-8")
            ).hexdigest()
            identity_hash = recompute_chunk_identity(
                raw_artifact_sha256=document.sha256,
                record_type=chunk.record_type,
                portable_record_key_value=(chunk.portable_record_key or chunk.source_payload_hash),
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
                errors.append(f"knowledge_chunk_hash_mismatch:{chunk.id}")
            if not chunk.source_locator_json or not chunk.source_url:
                locator_missing += 1
                errors.append(f"knowledge_chunk_source_locator_missing:{chunk.id}")

        state_candidates = list(
            session.scalars(
                select(SourceDocument).where(
                    SourceDocument.final_review_status.in_(APPROVABLE_STATUSES),
                    SourceDocument.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value,
                    SourceDocument.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value,
                    SourceDocument.data_type.in_(MATERIALIZABLE_TYPES),
                )
            )
        )
        documents = {document.id: document for document in state_candidates}
        for document_id in active_by_document:
            document = session.get(SourceDocument, document_id)
            if document is not None:
                documents[document_id] = document

        eligible_documents = 0
        for document_id, document in sorted(documents.items()):
            document_active = active_by_document.get(document_id, [])
            try:
                records, expected = self.build_expected_document_chunks(session, document)
            except TrustGateError as exc:
                ineligible += len(document_active)
                eligibility_drift += 1
                errors.append(f"knowledge_document_eligibility_drift:{document_id}:{exc}")
                if str(exc) in {
                    "knowledge_penalty_identity_invalid",
                    "penalty_source_identity_reimport_required",
                }:
                    penalty_failures += len(
                        [chunk for chunk in document_active if chunk.record_type == "penalty"]
                    )
                continue
            eligible_documents += 1
            record_ids = {record.id for record in records}
            for chunk in document_active:
                if chunk.structured_record_id is None:
                    if chunk.chunk_kind != "basic_information":
                        structured_orphans += 1
                        errors.append(f"knowledge_chunk_structured_record_missing:{chunk.id}")
                elif chunk.structured_record_id not in record_ids:
                    structured_orphans += 1
                    errors.append(f"knowledge_chunk_structured_record_missing:{chunk.id}")

            expected_by_slot = {_candidate_slot(candidate): candidate for candidate in expected}
            active_by_slot: dict[tuple[int | None, str, int], list[KnowledgeChunk]] = defaultdict(
                list
            )
            for chunk in document_active:
                active_by_slot[_chunk_slot(chunk)].append(chunk)
            consumed: set[int] = set()
            for slot, candidate in expected_by_slot.items():
                matches = active_by_slot.get(slot, [])
                if not matches:
                    missing_expected += 1
                    errors.append(
                        f"knowledge_chunk_missing_expected:{document_id}:{slot[0]}:{slot[1]}:{slot[2]}"
                    )
                    continue
                chosen = next(
                    (
                        chunk
                        for chunk in matches
                        if chunk.chunk_identity_sha256 == candidate.chunk_identity_sha256
                    ),
                    matches[0],
                )
                consumed.add(chosen.id)
                if not self._canonical_match(chosen, candidate, document):
                    canonical_mismatches += 1
                    errors.append(f"knowledge_chunk_canonical_mismatch:{chosen.id}")
                for duplicate in matches:
                    if duplicate.id != chosen.id:
                        consumed.add(duplicate.id)
                        extra_active += 1
                        errors.append(f"knowledge_chunk_extra_active:{duplicate.id}")
            for chunk in document_active:
                if chunk.id not in consumed:
                    extra_active += 1
                    errors.append(f"knowledge_chunk_extra_active:{chunk.id}")

        duplicate_document_level = self._duplicate_document_level_chunks(active)
        if duplicate_document_level:
            errors.extend(
                f"knowledge_chunk_document_level_duplicate:{chunk_id}"
                for chunk_id in sorted(duplicate_document_level)
            )
        duplicates = sum(value - 1 for value in identity_counts.values() if value > 1)
        if duplicates:
            errors.append("knowledge_chunk_duplicate_identity")
        return VerificationReport(
            valid=not errors,
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
            missing_expected_chunks=missing_expected,
            extra_active_chunks=extra_active,
            canonical_mismatches=canonical_mismatches,
            structured_record_orphans=structured_orphans,
            duplicate_document_level_chunks=len(duplicate_document_level),
            eligibility_drift_documents=eligibility_drift,
            errors=sorted(set(errors)),
        )

    def _ensure_eligible(self, session: Session, document: SourceDocument) -> None:
        if document.data_type not in MATERIALIZABLE_TYPES or not self._eligible_state(document):
            raise TrustGateError("knowledge_document_not_index_eligible")
        from app.services.knowledge.service import KnowledgeIndexService

        reasons = KnowledgeIndexService(self.settings).rejection_reasons(session, document)
        review_decisions = list(
            session.scalars(
                select(ReviewDecision).where(
                    ReviewDecision.record_type == document.data_type,
                    ReviewDecision.record_id == document.id,
                )
            )
        )
        if (
            len(review_decisions) != 1
            or review_decisions[0].decision != document.final_review_status
        ):
            reasons.append("review_decision_missing_or_inconsistent")
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
        return {"source_url": occurrence.source_url, "final_url": occurrence.final_url}

    @staticmethod
    def _retire_active(session: Session, document_id: int) -> int:
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
        session.flush()
        return len(chunks)

    @staticmethod
    def _apply_canonical_values(
        chunk: KnowledgeChunk, candidate: ChunkCandidate, document: SourceDocument
    ) -> None:
        chunk.record_type = candidate.record_type
        chunk.structured_record_id = candidate.structured_record_id
        chunk.pilot_id = candidate.pilot_id
        chunk.portable_record_key = candidate.portable_record_key
        chunk.chunk_kind = candidate.chunk_kind
        chunk.chunk_ordinal = candidate.chunk_ordinal
        chunk.title = candidate.title
        chunk.text = candidate.text
        chunk.normalized_text = candidate.normalized_text
        chunk.lexical_tokens = candidate.lexical_tokens
        chunk.authority = candidate.authority
        chunk.authority_filter_text = candidate.authority_filter_text
        chunk.relevant_date = candidate.relevant_date
        chunk.source_url = candidate.source_url
        chunk.source_locator_json = candidate.source_locator
        chunk.evidence_reference_json = candidate.evidence_references
        chunk.evidence_quality = candidate.evidence_quality
        chunk.authenticity_status = document.authenticity_type
        chunk.review_status = document.final_review_status
        chunk.source_payload_hash = candidate.source_payload_hash
        chunk.chunk_content_sha256 = candidate.chunk_content_sha256
        chunk.tokenizer_version = TOKENIZER_VERSION
        chunk.chunker_version = CHUNKER_VERSION
        chunk.ranking_version = RANKING_VERSION

    @staticmethod
    def _canonical_match(
        chunk: KnowledgeChunk, candidate: ChunkCandidate, document: SourceDocument
    ) -> bool:
        return (
            chunk.chunk_identity_sha256 == candidate.chunk_identity_sha256
            and chunk.source_payload_hash == candidate.source_payload_hash
            and chunk.structured_record_id == candidate.structured_record_id
            and chunk.portable_record_key == candidate.portable_record_key
            and chunk.record_type == candidate.record_type
            and chunk.chunk_kind == candidate.chunk_kind
            and chunk.chunk_ordinal == candidate.chunk_ordinal
            and chunk.title == candidate.title
            and chunk.text == candidate.text
            and chunk.normalized_text == candidate.normalized_text
            and chunk.lexical_tokens == candidate.lexical_tokens
            and chunk.authority == candidate.authority
            and chunk.authority_filter_text == candidate.authority_filter_text
            and chunk.relevant_date == candidate.relevant_date
            and chunk.source_url == candidate.source_url
            and chunk.source_locator_json == candidate.source_locator
            and chunk.evidence_reference_json == candidate.evidence_references
            and chunk.evidence_quality == candidate.evidence_quality
            and chunk.authenticity_status == document.authenticity_type
            and chunk.review_status == document.final_review_status
            and chunk.tokenizer_version == TOKENIZER_VERSION
            and chunk.chunker_version == CHUNKER_VERSION
            and chunk.ranking_version == RANKING_VERSION
            and chunk.chunk_content_sha256 == candidate.chunk_content_sha256
        )

    @staticmethod
    def _duplicate_document_level_chunks(active: list[KnowledgeChunk]) -> set[int]:
        duplicate_ids: set[int] = set()
        by_kind: dict[tuple[int, str], list[KnowledgeChunk]] = defaultdict(list)
        by_content: dict[tuple[int, str, str], list[KnowledgeChunk]] = defaultdict(list)
        for chunk in active:
            if chunk.chunk_kind == "basic_information":
                by_kind[(chunk.source_document_id, chunk.chunk_kind)].append(chunk)
            if chunk.structured_record_id is None:
                by_content[
                    (chunk.source_document_id, chunk.chunk_kind, chunk.chunk_content_sha256)
                ].append(chunk)
        for group in [*by_kind.values(), *by_content.values()]:
            duplicate_ids.update(chunk.id for chunk in group[1:])
        return duplicate_ids


def _candidate_slot(candidate: ChunkCandidate) -> tuple[int | None, str, int]:
    return candidate.structured_record_id, candidate.chunk_kind, candidate.chunk_ordinal


def _chunk_slot(chunk: KnowledgeChunk) -> tuple[int | None, str, int]:
    return chunk.structured_record_id, chunk.chunk_kind, chunk.chunk_ordinal
