from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ScreeningError, TrustGateError
from app.models import FindingEvidenceLink, KnowledgeChunk, RiskFinding
from app.models.enums import DataType, FindingEvidenceStatus, FindingSupportType
from app.services.knowledge import (
    KnowledgeIndexService,
    SearchRequest,
    SearchResult,
    TrustedKnowledgeSearchService,
)
from app.services.knowledge.chunks import RANKING_VERSION
from app.services.screening.rules import MarketingRiskRule, SupportType

MAX_EVIDENCE_PER_FINDING = 5
MAX_EVIDENCE_PER_DOCUMENT = 2
SUPPORT_EVALUATION_VERSION = "deterministic_evidence_support_v1"


@dataclass(frozen=True)
class TrustedIndexSnapshot:
    payload_hash: str
    active_chunks: int
    chunks_by_record_type: dict[str, int]


@dataclass(frozen=True)
class EvidenceSupportDecision:
    support_type: str
    result: SearchResult
    passed: bool
    matched_support_patterns: tuple[str, ...]
    matched_evidence_fields: tuple[str, ...]
    support_reason: str
    context_scope: str


@dataclass(frozen=True)
class EvidenceAssemblyResult:
    links: tuple[FindingEvidenceLink, ...]
    candidates_total: int
    candidates_passed: int
    candidates_rejected: int


class DeterministicEvidenceSupportEvaluator:
    version = SUPPORT_EVALUATION_VERSION

    @staticmethod
    def evaluate(
        rule: MarketingRiskRule,
        support_type: str,
        result: SearchResult,
    ) -> EvidenceSupportDecision:
        matcher = rule.evidence_matchers[cast(SupportType, support_type)]
        context_scope = (
            "illustrative_not_material_specific"
            if support_type == FindingSupportType.PRODUCT_TERM_CONTEXT.value
            else "not_applicable"
        )
        if result.chunk_kind not in matcher.allowed_chunk_kinds:
            return EvidenceSupportDecision(
                support_type,
                result,
                False,
                (),
                (),
                "chunk_kind_not_allowed",
                context_scope,
            )
        references = [
            item
            for item in result.evidence_references
            if str(item.get("field_name") or "") in matcher.required_evidence_fields
        ]
        fields = tuple(sorted({str(item["field_name"]) for item in references}))
        if not fields:
            return EvidenceSupportDecision(
                support_type,
                result,
                False,
                (),
                (),
                "required_evidence_field_missing",
                context_scope,
            )
        evidence_text = "\n".join(str(item.get("quote") or "") for item in references)
        matched = tuple(
            pattern
            for pattern in matcher.required_any_patterns
            if re.search(pattern, evidence_text)
        )
        if not matched:
            return EvidenceSupportDecision(
                support_type,
                result,
                False,
                (),
                fields,
                "semantic_pattern_not_matched",
                context_scope,
            )
        return EvidenceSupportDecision(
            support_type,
            result,
            True,
            matched,
            fields,
            "semantic_evidence_match",
            context_scope,
        )


class FindingEvidenceAssembler:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def verify_trusted_index(self, session: Session) -> TrustedIndexSnapshot:
        report = KnowledgeIndexService(self.settings).verify_chunks(session)
        if not report.valid:
            raise ScreeningError("screening_trusted_index_invalid")
        identities = list(
            session.scalars(
                select(KnowledgeChunk.chunk_identity_sha256)
                .where(KnowledgeChunk.is_active.is_(True))
                .order_by(KnowledgeChunk.chunk_identity_sha256)
            )
        )
        payload = json.dumps(identities, ensure_ascii=False, separators=(",", ":")).encode()
        return TrustedIndexSnapshot(
            payload_hash=hashlib.sha256(payload).hexdigest(),
            active_chunks=report.active_chunks,
            chunks_by_record_type=report.chunks_by_record_type,
        )

    def assemble(
        self,
        session: Session,
        finding: RiskFinding,
        rule: MarketingRiskRule,
    ) -> EvidenceAssemblyResult:
        try:
            candidates = self._search_candidates(session, rule)
            decisions = [
                DeterministicEvidenceSupportEvaluator.evaluate(rule, *candidate)
                for candidate in candidates
            ]
            passed = [decision for decision in decisions if decision.passed]
            selected = self._select(passed, rule.evidence_requirements)
            links = [self._to_link(session, finding, item) for item in selected]
        except (TrustGateError, ScreeningError):
            raise
        except Exception as exc:
            raise ScreeningError("screening_evidence_retrieval_failed") from exc
        support_types = {link.support_type for link in links if link.support_evaluation_passed}
        if set(rule.evidence_requirements).issubset(support_types):
            finding.evidence_status = FindingEvidenceStatus.SUPPORTED.value
        elif links:
            finding.evidence_status = FindingEvidenceStatus.PARTIALLY_SUPPORTED.value
        else:
            finding.evidence_status = FindingEvidenceStatus.EVIDENCE_INSUFFICIENT.value
        return EvidenceAssemblyResult(
            links=tuple(links),
            candidates_total=len(decisions),
            candidates_passed=len(passed),
            candidates_rejected=len(decisions) - len(passed),
        )

    @staticmethod
    def _search_candidates(
        session: Session, rule: MarketingRiskRule
    ) -> list[tuple[str, SearchResult]]:
        service = TrustedKnowledgeSearchService()
        query = " ".join(rule.retrieval_queries)[:500]
        candidates: list[tuple[str, SearchResult]] = []
        type_to_support = {
            DataType.REGULATION.value: FindingSupportType.NORMATIVE_BASIS.value,
            DataType.PENALTY.value: FindingSupportType.ENFORCEMENT_EXAMPLE.value,
            DataType.PRODUCT_DOCUMENT.value: FindingSupportType.PRODUCT_TERM_CONTEXT.value,
        }
        for record_type in rule.preferred_record_types:
            support_type = type_to_support[record_type]
            if support_type not in rule.evidence_requirements:
                continue
            results = service.search(
                session,
                SearchRequest(query=query, record_types=(record_type,), limit=10),
            )
            candidates.extend((support_type, result) for result in results)
        return candidates

    @staticmethod
    def _select(
        candidates: list[EvidenceSupportDecision],
        required_support_types: tuple[str, ...],
    ) -> list[EvidenceSupportDecision]:
        priorities = {
            FindingSupportType.NORMATIVE_BASIS.value: 0,
            FindingSupportType.ENFORCEMENT_EXAMPLE.value: 1,
            FindingSupportType.PRODUCT_TERM_CONTEXT.value: 2,
        }
        ordered = sorted(
            candidates,
            key=lambda item: (
                priorities[item.support_type],
                -item.result.score,
                item.result.evidence_quality or "Z",
                item.result.pilot_id or "",
                item.result.chunk_identity_sha256,
            ),
        )
        selected: list[EvidenceSupportDecision] = []
        seen_chunks: set[str] = set()
        document_counts: defaultdict[int, int] = defaultdict(int)
        for required in sorted(required_support_types, key=priorities.__getitem__):
            for candidate in (item for item in ordered if item.support_type == required):
                count_before = len(selected)
                FindingEvidenceAssembler._append_if_eligible(
                    selected, seen_chunks, document_counts, candidate
                )
                if len(selected) > count_before:
                    break
        for candidate in ordered:
            FindingEvidenceAssembler._append_if_eligible(
                selected, seen_chunks, document_counts, candidate
            )
            if len(selected) == MAX_EVIDENCE_PER_FINDING:
                break
        return selected

    @staticmethod
    def _append_if_eligible(
        selected: list[EvidenceSupportDecision],
        seen_chunks: set[str],
        document_counts: defaultdict[int, int],
        candidate: EvidenceSupportDecision,
    ) -> None:
        if len(selected) >= MAX_EVIDENCE_PER_FINDING:
            return
        result = candidate.result
        if not result.source_locator or not result.evidence_references:
            return
        identity = result.chunk_identity_sha256
        document_id = result.source_document_id
        if identity in seen_chunks or document_counts[document_id] >= MAX_EVIDENCE_PER_DOCUMENT:
            return
        selected.append(candidate)
        seen_chunks.add(identity)
        document_counts[document_id] += 1

    @staticmethod
    def _to_link(
        session: Session,
        finding: RiskFinding,
        decision: EvidenceSupportDecision,
    ) -> FindingEvidenceLink:
        result = decision.result
        chunk = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.chunk_identity_sha256 == result.chunk_identity_sha256
            )
        )
        if chunk is None or not chunk.is_active:
            raise ScreeningError("screening_evidence_retrieval_failed")
        return FindingEvidenceLink(
            finding_id=finding.id,
            knowledge_chunk_id=chunk.id,
            support_type=decision.support_type,
            retrieval_rank=result.rank,
            retrieval_score=result.score,
            chunk_identity_sha256=result.chunk_identity_sha256,
            chunk_content_sha256=result.chunk_content_sha256,
            source_document_snapshot_json={
                "source_document_id": result.source_document_id,
                "record_type": result.record_type,
                "pilot_id": result.pilot_id,
                "title": result.title,
                "source_url": result.source_url,
                "authenticity_status": result.authenticity_status,
                "review_status": result.review_status,
            },
            source_locator_snapshot_json=result.source_locator,
            evidence_references_snapshot_json=result.evidence_references,
            support_evaluation_version=SUPPORT_EVALUATION_VERSION,
            support_evaluation_passed=decision.passed,
            matched_support_patterns=list(decision.matched_support_patterns),
            matched_evidence_fields=list(decision.matched_evidence_fields),
            support_reason=decision.support_reason,
            context_scope=decision.context_scope,
        )


RETRIEVAL_VERSION = f"trusted_search_{RANKING_VERSION}"
