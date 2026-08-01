from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ExplanationError
from app.models import FindingEvidenceLink, KnowledgeChunk, RiskFinding, ScreeningRun
from app.models.enums import DataType, ScreeningStatus
from app.services.explanation.prompts import canonical_json_bytes, canonical_sha256
from app.services.explanation.schemas import (
    ControlledEvidence,
    ControlledFinding,
    ControlledRAGContext,
)
from app.services.review_payload import canonical_portable_source_url
from app.services.screening.evidence import FindingEvidenceAssembler

CONTEXT_SCHEMA_VERSION = "controlled_rag_context_v1"
MAX_QUOTE_LENGTH = 600
MAX_EVIDENCE_PER_FINDING = 4
MAX_CONTEXT_CHARACTERS = 30_000
SUPPORT_TYPE_PRIORITY = {
    "normative_basis": 0,
    "enforcement_example": 1,
    "product_term_context": 2,
}


@dataclass(frozen=True)
class EvidenceBinding:
    finding_key: str
    finding_id: int
    link_id: int
    citation_key: str
    quote: str
    chunk_identity_sha256: str
    chunk_content_sha256: str
    source_url: str
    source_locator: dict[str, Any]


@dataclass(frozen=True)
class BuiltContext:
    payload: ControlledRAGContext
    payload_sha256: str
    bindings: dict[str, EvidenceBinding]


class ControlledRAGContextBuilder:
    version = CONTEXT_SCHEMA_VERSION

    def build(self, session: Session, screening_run_id: int) -> BuiltContext:
        run = session.scalar(
            select(ScreeningRun)
            .options(
                selectinload(ScreeningRun.findings).selectinload(RiskFinding.evidence_links),
                selectinload(ScreeningRun.material),
            )
            .where(ScreeningRun.id == screening_run_id)
        )
        if run is None or run.status != ScreeningStatus.COMPLETED.value:
            raise ExplanationError("explanation_screening_run_not_completed")
        findings = sorted(
            run.findings,
            key=lambda item: (
                item.raw_start_offset,
                item.raw_end_offset,
                item.rule_id,
                item.finding_sha256,
            ),
        )
        if run.finding_count != len(findings):
            raise ExplanationError("explanation_screening_result_incomplete")
        current_index = FindingEvidenceAssembler().verify_trusted_index(session)
        if current_index.payload_hash != run.trusted_index_payload_hash:
            raise ExplanationError("explanation_trusted_index_drift")

        controlled_findings: list[ControlledFinding] = []
        bindings: dict[str, EvidenceBinding] = {}
        citation_ordinal = 1
        truncated = False
        for finding_ordinal, finding in enumerate(findings, start=1):
            finding_key = f"F{finding_ordinal:03d}"
            links = self._sorted_links(finding)
            if not links:
                raise ExplanationError("explanation_finding_evidence_missing")
            evidence_rows: list[ControlledEvidence] = []
            for link in links[:MAX_EVIDENCE_PER_FINDING]:
                citation_key = f"E{citation_ordinal:03d}"
                evidence, binding = self._evidence(session, finding_key, citation_key, link)
                evidence_rows.append(evidence)
                bindings[citation_key] = binding
                citation_ordinal += 1
            if len(links) > MAX_EVIDENCE_PER_FINDING:
                truncated = True
            controlled_findings.append(
                ControlledFinding(
                    finding_key=finding_key,
                    rule_id=finding.rule_id,
                    category=finding.category,
                    severity=finding.severity,
                    signal_strength=finding.signal_strength,
                    matched_text=finding.matched_text,
                    raw_start_offset=finding.raw_start_offset,
                    raw_end_offset=finding.raw_end_offset,
                    deterministic_explanation=finding.explanation,
                    review_question=finding.review_question,
                    evidence_status=finding.evidence_status,
                    evidence=evidence_rows,
                )
            )

        context = ControlledRAGContext(
            context_schema_version=CONTEXT_SCHEMA_VERSION,
            screening_run_payload_sha256=run.run_payload_sha256 or "",
            trusted_index_payload_hash=run.trusted_index_payload_hash,
            material={
                "title": run.material.title,
                "material_type": run.material.material_type,
                "input_sha256": run.material.input_sha256,
                "source_label": run.material.source_label,
            },
            findings=controlled_findings,
            truncated=truncated,
        )
        context, bindings = self._fit_budget(context, bindings)
        payload = context.model_dump(mode="json")
        return BuiltContext(context, canonical_sha256(payload), bindings)

    @staticmethod
    def _sorted_links(finding: RiskFinding) -> list[FindingEvidenceLink]:
        return sorted(
            finding.evidence_links,
            key=lambda link: (
                SUPPORT_TYPE_PRIORITY[link.support_type],
                link.retrieval_rank,
                -link.retrieval_score,
                str(link.source_document_snapshot_json.get("pilot_id") or ""),
                link.chunk_identity_sha256,
            ),
        )

    @staticmethod
    def _evidence(
        session: Session,
        finding_key: str,
        citation_key: str,
        link: FindingEvidenceLink,
    ) -> tuple[ControlledEvidence, EvidenceBinding]:
        chunk = session.get(KnowledgeChunk, link.knowledge_chunk_id)
        source = link.source_document_snapshot_json
        source_locator = link.source_locator_snapshot_json
        if (
            chunk is None
            or not chunk.is_active
            or chunk.record_type == DataType.REGULATORY_CASE.value
            or chunk.chunk_identity_sha256 != link.chunk_identity_sha256
            or chunk.chunk_content_sha256 != link.chunk_content_sha256
            or chunk.source_locator_json != source_locator
            or chunk.source_url != source.get("source_url")
            or not source_locator
        ):
            raise ExplanationError("explanation_citation_snapshot_mismatch")
        record_type = str(source.get("record_type") or "")
        if record_type not in {
            DataType.REGULATION.value,
            DataType.PENALTY.value,
            DataType.PRODUCT_DOCUMENT.value,
        }:
            raise ExplanationError("explanation_citation_snapshot_mismatch")
        portable_url = canonical_portable_source_url(source.get("source_url"))
        if not isinstance(portable_url, str) or not portable_url:
            raise ExplanationError("explanation_citation_snapshot_mismatch")
        quote = chunk.text
        if not quote.strip():
            raise ExplanationError("explanation_citation_snapshot_mismatch")
        short_quote = quote[:MAX_QUOTE_LENGTH]
        evidence = ControlledEvidence(
            citation_key=citation_key,
            support_type=link.support_type,
            source_title=str(source.get("title") or "未命名来源"),
            source_url=portable_url,
            pilot_id=str(source["pilot_id"]) if source.get("pilot_id") else None,
            record_type=record_type,
            chunk_kind=str(source.get("chunk_kind") or chunk.chunk_kind),
            quote=short_quote,
            source_locator=source_locator,
            evidence_references=link.evidence_references_snapshot_json,
            context_scope=link.context_scope,
            chunk_identity_sha256=link.chunk_identity_sha256,
            chunk_content_sha256=link.chunk_content_sha256,
            truncated=len(quote) > len(short_quote),
        )
        return evidence, EvidenceBinding(
            finding_key=finding_key,
            finding_id=link.finding_id,
            link_id=link.id,
            citation_key=citation_key,
            quote=short_quote,
            chunk_identity_sha256=link.chunk_identity_sha256,
            chunk_content_sha256=link.chunk_content_sha256,
            source_url=portable_url,
            source_locator=source_locator,
        )

    @staticmethod
    def _fit_budget(
        context: ControlledRAGContext,
        bindings: dict[str, EvidenceBinding],
    ) -> tuple[ControlledRAGContext, dict[str, EvidenceBinding]]:
        if (
            len(canonical_json_bytes(context.model_dump(mode="json")).decode("utf-8"))
            <= MAX_CONTEXT_CHARACTERS
        ):
            return context, bindings
        rows = [item.model_copy(deep=True) for item in context.findings]
        kept = dict(bindings)
        for finding in reversed(rows):
            while finding.evidence:
                removed = finding.evidence.pop()
                kept.pop(removed.citation_key, None)
                candidate = context.model_copy(update={"findings": rows, "truncated": True})
                if (
                    len(canonical_json_bytes(candidate.model_dump(mode="json")).decode("utf-8"))
                    <= MAX_CONTEXT_CHARACTERS
                ):
                    return candidate, kept
        raise ExplanationError("explanation_context_too_large")
