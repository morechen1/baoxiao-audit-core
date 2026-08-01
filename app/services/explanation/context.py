from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ExplanationError
from app.models import FindingEvidenceLink, KnowledgeChunk, RiskFinding, ScreeningRun
from app.models.enums import DataType, ScreeningStatus
from app.services.explanation.prompts import canonical_json_bytes, canonical_sha256
from app.services.explanation.schemas import (
    AllowedEvidenceSegment,
    ControlledEvidence,
    ControlledFinding,
    ControlledRAGContext,
    VisibleSegmentV1,
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
ALLOWED_EVIDENCE_FIELDS = {
    "normative_basis": ("article_text",),
    "enforcement_example": ("illegal_facts", "original_sales_wording", "legal_basis"),
    "product_term_context": (
        "waiting_period",
        "cooling_off_period",
        "exclusions",
        "cash_value_description",
        "surrender_risk",
        "insurance_responsibility",
        "guaranteed_benefit",
        "non_guaranteed_benefit",
    ),
}
SUPPORTED_EVIDENCE_STATUSES = {"supported", "partially_supported"}


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
    support_type: str
    source_title: str
    pilot_id: str | None
    record_type: str
    chunk_kind: str
    context_scope: str
    allowed_quote_segments: tuple[AllowedEvidenceSegment, ...]
    semantic_anchors: tuple[str, ...]


@dataclass(frozen=True)
class BuiltContext:
    payload: ControlledRAGContext
    payload_sha256: str
    bindings: dict[str, EvidenceBinding]


class AllowedEvidenceQuoteBuilderV1:
    version = "allowed_evidence_quote_builder_v1"

    def build(self, link: FindingEvidenceLink) -> tuple[AllowedEvidenceSegment, ...]:
        allowed = ALLOWED_EVIDENCE_FIELDS.get(link.support_type)
        if allowed is None or not link.support_evaluation_passed:
            raise ExplanationError("explanation_citation_snapshot_mismatch")
        matched_fields = set(link.matched_evidence_fields)
        field_order = {value: index for index, value in enumerate(allowed)}
        segments: list[AllowedEvidenceSegment] = []
        seen: set[tuple[str, str, int | None, int | None]] = set()
        for reference in link.evidence_references_snapshot_json:
            field_name = str(reference.get("field_name") or "")
            mode = str(reference.get("mode") or "verbatim")
            quote = str(reference.get("quote") or "").strip()
            if (
                field_name not in field_order
                or field_name not in matched_fields
                or mode not in {"verbatim", "normalized"}
                or not quote
            ):
                continue
            key = (
                field_name,
                quote,
                _optional_int(reference.get("start_offset")),
                _optional_int(reference.get("end_offset")),
            )
            if key in seen:
                continue
            seen.add(key)
            original_length = len(quote)
            window = _evidence_window(
                quote,
                tuple(str(value) for value in link.actual_matched_substrings),
                MAX_QUOTE_LENGTH,
            )
            was_truncated = len(window) < original_length
            segments.append(
                AllowedEvidenceSegment(
                    field_name=field_name,
                    quote=window,
                    evidence_snapshot={
                        key: value for key, value in reference.items() if key != "quote"
                    },
                    original_quote_length=original_length,
                    truncated=was_truncated,
                )
            )
        segments.sort(
            key=lambda item: (
                field_order[item.field_name],
                _optional_int(item.evidence_snapshot.get("start_offset")) or -1,
                _optional_int(item.evidence_snapshot.get("end_offset")) or -1,
                item.quote,
            )
        )
        if not segments:
            raise ExplanationError("explanation_citation_snapshot_mismatch")
        return tuple(segments[:8])


class ControlledRAGContextBuilder:
    version = CONTEXT_SCHEMA_VERSION

    def __init__(self) -> None:
        self.allowed_quotes = AllowedEvidenceQuoteBuilderV1()

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
            self._validate_finding_evidence_status(finding.evidence_status, links)
            evidence_rows: list[ControlledEvidence] = []
            for link in links[:MAX_EVIDENCE_PER_FINDING]:
                citation_key = f"E{citation_ordinal:03d}"
                evidence, binding = self._evidence(session, finding_key, citation_key, link)
                evidence_rows.append(evidence)
                bindings[citation_key] = binding
                citation_ordinal += 1
            if len(links) > MAX_EVIDENCE_PER_FINDING or any(
                item.truncated for item in evidence_rows
            ):
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
    def _validate_finding_evidence_status(status: str, links: list[FindingEvidenceLink]) -> None:
        if status in SUPPORTED_EVIDENCE_STATUSES and not links:
            raise ExplanationError("explanation_finding_evidence_missing")
        if status == "evidence_insufficient" and links:
            raise ExplanationError("explanation_finding_evidence_inconsistent")
        if status not in {*SUPPORTED_EVIDENCE_STATUSES, "evidence_insufficient"}:
            raise ExplanationError("explanation_finding_evidence_status_invalid")

    @staticmethod
    def _sorted_links(finding: RiskFinding) -> list[FindingEvidenceLink]:
        return sorted(
            finding.evidence_links,
            key=lambda link: (
                SUPPORT_TYPE_PRIORITY.get(link.support_type, 99),
                link.retrieval_rank,
                -link.retrieval_score,
                str(link.source_document_snapshot_json.get("pilot_id") or ""),
                link.chunk_identity_sha256,
            ),
        )

    def _evidence(
        self,
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
            or chunk.title != source.get("title")
            or chunk.pilot_id != source.get("pilot_id")
            or chunk.record_type != source.get("record_type")
            or chunk.chunk_kind != source.get("chunk_kind")
            or chunk.authenticity_status != source.get("authenticity_status")
            or chunk.review_status != source.get("review_status")
            or chunk.evidence_reference_json != link.evidence_references_snapshot_json
            or not link.support_evaluation_passed
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
        segments = self.allowed_quotes.build(link)
        primary = segments[0]
        visible = [
            VisibleSegmentV1(
                field_name=item.field_name,
                quote=item.quote,
                truncated=item.truncated,
                original_quote_length=item.original_quote_length,
            )
            for item in segments
        ]
        evidence = ControlledEvidence(
            citation_key=citation_key,
            support_type=link.support_type,
            source_title=str(source["title"]),
            source_url=portable_url,
            pilot_id=str(source["pilot_id"]) if source.get("pilot_id") else None,
            record_type=record_type,
            chunk_kind=str(source["chunk_kind"]),
            quote=primary.quote,
            source_locator=source_locator,
            evidence_references=[item.evidence_snapshot for item in segments],
            context_scope=link.context_scope,
            chunk_identity_sha256=link.chunk_identity_sha256,
            chunk_content_sha256=link.chunk_content_sha256,
            evidence_field_name=primary.field_name,
            visible_segments=visible,
            truncated=any(item.truncated for item in segments),
        )
        return evidence, EvidenceBinding(
            finding_key=finding_key,
            finding_id=link.finding_id,
            link_id=link.id,
            citation_key=citation_key,
            quote=primary.quote,
            chunk_identity_sha256=link.chunk_identity_sha256,
            chunk_content_sha256=link.chunk_content_sha256,
            source_url=portable_url,
            source_locator=source_locator,
            support_type=link.support_type,
            source_title=str(source["title"]),
            pilot_id=str(source["pilot_id"]) if source.get("pilot_id") else None,
            record_type=record_type,
            chunk_kind=str(source["chunk_kind"]),
            context_scope=link.context_scope,
            allowed_quote_segments=segments,
            semantic_anchors=tuple(str(value) for value in link.actual_matched_substrings),
        )

    @staticmethod
    def _fit_budget(
        context: ControlledRAGContext,
        bindings: dict[str, EvidenceBinding],
    ) -> tuple[ControlledRAGContext, dict[str, EvidenceBinding]]:
        if _context_length(context) <= MAX_CONTEXT_CHARACTERS:
            return context, bindings
        rows = [item.model_copy(deep=True) for item in context.findings]
        kept = dict(bindings)

        for quote_limit in (320, 160, 96, 64, 48, 32):
            for finding in rows:
                for index, evidence in enumerate(finding.evidence):
                    binding = kept[evidence.citation_key]
                    shortened = tuple(
                        item.model_copy(
                            update={
                                "quote": _evidence_window(
                                    item.quote, binding.semantic_anchors, quote_limit
                                )
                            }
                        )
                        for item in binding.allowed_quote_segments
                    )
                    primary = shortened[0].quote
                    updated_visible = [
                        VisibleSegmentV1(
                            field_name=seg.field_name,
                            quote=seg.quote,
                            truncated=True,
                            original_quote_length=seg.original_quote_length,
                        )
                        for seg in shortened
                    ]
                    finding.evidence[index] = evidence.model_copy(
                        update={
                            "quote": primary,
                            "evidence_field_name": shortened[0].field_name,
                            "truncated": True,
                            "visible_segments": updated_visible,
                        }
                    )
                    kept[evidence.citation_key] = replace(
                        binding,
                        quote=primary,
                        allowed_quote_segments=shortened,
                    )
            candidate = context.model_copy(update={"findings": rows, "truncated": True})
            if _context_length(candidate) <= MAX_CONTEXT_CHARACTERS:
                return candidate, kept

        for target_count in (2, 1):
            for finding in rows:
                if len(finding.evidence) <= target_count:
                    continue
                removed = finding.evidence[target_count:]
                finding.evidence = finding.evidence[:target_count]
                for item in removed:
                    kept.pop(item.citation_key, None)
            candidate = context.model_copy(update={"findings": rows, "truncated": True})
            if _context_length(candidate) <= MAX_CONTEXT_CHARACTERS:
                return candidate, kept
        raise ExplanationError("explanation_context_too_large")


def _context_length(context: ControlledRAGContext) -> int:
    return len(canonical_json_bytes(context.model_dump(mode="json")).decode("utf-8"))


def _evidence_window(value: str, anchors: tuple[str, ...], limit: int) -> str:
    if len(value) <= limit:
        return value
    positions = [value.find(anchor) for anchor in anchors if anchor and value.find(anchor) >= 0]
    center = min(positions) if positions else len(value) // 2
    start = max(0, center - limit // 3)
    end = min(len(value), start + limit)
    start = max(0, end - limit)
    return value[start:end]


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
