"""Fail-closed trusted-RAG semantic supplements; never changes deterministic matching."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import (
    FindingEvidenceLink,
    KnowledgeChunk,
    MaterialSegment,
    RiskFinding,
    ScreeningRun,
    SourceDocument,
)
from app.models.enums import APPROVABLE_STATUSES, FindingEvidenceStatus, FindingSupportType
from app.services.knowledge import SearchRequest, TrustedKnowledgeSearchService
from app.services.screening.normalization import normalize_marketing_text
from app.services.screening.rules import MarketingRiskRule, MarketingRuleSet, load_ruleset

SEMANTIC_SCREENING_VERSION = "trusted_rag_semantic_screening_v1"
SEMANTIC_TAXONOMY = frozenset(
    {
        "absolute_or_superlative_claim",
        "concealment_or_minimization_of_exclusions",
        "extra_contractual_benefit",
        "false_promotion_or_prize",
        "guaranteed_return_or_principal",
        "improper_comparison_or_ranking",
        "misleading_interest_or_yield",
        "no_risk_or_no_loss",
        "product_nature_confusion",
        "regulatory_endorsement",
        "surrender_or_cash_value_misstatement",
        "waiting_or_cooling_period_misstatement",
    }
)


class SemanticScreeningError(Exception):
    """A fail-closed semantic error with safe, machine-readable diagnostics."""

    def __init__(self, code: str, *, diagnostic: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostic = diagnostic or {}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticCandidate(_StrictModel):
    rule_id: str
    matched_text: str = Field(min_length=1, max_length=500)
    citation_key: str = Field(pattern=r"^S[0-9]{3}$")
    cited_quote: str = Field(min_length=1, max_length=600)
    confidence: Literal["high", "medium", "low"]
    uncertainty: Literal["none", "limited", "uncertain"]
    claim_polarity: Literal[
        "affirmative_marketing_claim", "negative_or_warning", "prohibition_or_critique"
    ]


class SemanticScreeningOutput(_StrictModel):
    candidates: list[SemanticCandidate] = Field(max_length=12)


@dataclass(frozen=True)
class SemanticCitation:
    citation_key: str
    chunk: KnowledgeChunk
    quote: str
    retrieval_rank: int
    retrieval_score: float


@dataclass(frozen=True)
class ResolvedSemanticCandidate:
    """Provider output after the system has resolved its only permitted source span."""

    candidate: SemanticCandidate
    start_offset: int
    end_offset: int
    segment: MaterialSegment


class SemanticScreeningProvider(Protocol):
    def generate(
        self, *, raw_text: str, taxonomy: list[dict[str, str]], citations: list[dict[str, str]]
    ) -> str: ...


class OpenAICompatibleSemanticScreeningProvider:
    """Small DeepSeek/OpenAI-compatible JSON adapter; raw JSON stays unmodified."""

    def __init__(
        self, settings: Settings | None = None, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport

    def generate(
        self, *, raw_text: str, taxonomy: list[dict[str, str]], citations: list[dict[str, str]]
    ) -> str:
        if not (
            self.settings.llm_enabled
            and self.settings.llm_provider == "openai_compatible"
            and self.settings.llm_api_key
            and self.settings.llm_model
            and _is_https_url(self.settings.llm_base_url)
        ):
            raise SemanticScreeningError("semantic_provider_not_configured")
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": "Return ONLY a valid JSON object matching the required schema. Do not return Markdown or any text outside the JSON object. Each candidate must contain only the schema fields: never provide offsets or extra fields. 你只进行保险营销文本的受控风险分类。仅返回不被既有确定性筛查覆盖的补充风险。仅可使用给定 taxonomy、原文精确片段和 Citation。matched_text 必须逐字复制营销原文，并且仅在该片段在原文中唯一出现时输出；只可复制给定 citation_key 和 cited_quote。只识别肯定性的营销主张；否定、风险提示、禁止性说明、法规引用和对错误宣传的批判必须输出空 candidates。不得创造类别、证据、引用或改写 quoted text。仅 high confidence 且 uncertainty=none 的候选可被接受。",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "marketing_text": raw_text,
                        "taxonomy": taxonomy,
                        "allowed_citations": citations,
                        "output_schema": SemanticScreeningOutput.model_json_schema(),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        payload: dict[str, object] = {
            "model": self.settings.llm_model,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        base_url = self.settings.llm_base_url.rstrip("/")
        endpoint = (
            base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
        )
        try:
            with httpx.Client(
                timeout=self.settings.llm_timeout_seconds, transport=self.transport
            ) as client:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise SemanticScreeningError("semantic_provider_timeout") from exc
        except httpx.RequestError as exc:
            raise SemanticScreeningError("semantic_provider_network_error") from exc
        if response.status_code == 429:
            raise SemanticScreeningError("semantic_provider_rate_limited")
        if response.is_error:
            try:
                error = response.json().get("error", {})
            except (TypeError, ValueError):
                error = {}
            message = str(error.get("message", ""))[:300]
            raise SemanticScreeningError(
                "semantic_provider_http_error:"
                f"status={response.status_code};type={error.get('type')};"
                f"code={error.get('code')};message={message};"
                f"endpoint={endpoint};model={self.settings.llm_model};"
                f"request_bytes={len(json.dumps(payload, ensure_ascii=False).encode())};"
                f"message_chars={sum(len(item['content']) for item in messages)}"
            )
        try:
            raw = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise SemanticScreeningError("semantic_provider_invalid_response") from exc
        if not isinstance(raw, str) or not raw.strip():
            raise SemanticScreeningError("semantic_provider_invalid_response")
        return raw


class SemanticOutputValidator:
    def __init__(self, rules: dict[str, MarketingRiskRule]) -> None:
        self.rules = rules

    def validate(
        self,
        *,
        raw_json: str,
        raw_text: str,
        citations: dict[str, SemanticCitation],
        deterministic: set[tuple[str, int, int]],
        segments: list[MaterialSegment],
    ) -> list[ResolvedSemanticCandidate]:
        try:
            output = SemanticScreeningOutput.model_validate_json(raw_json)
        except ValidationError as exc:
            raise SemanticScreeningError("semantic_output_schema_invalid") from exc
        accepted: list[ResolvedSemanticCandidate] = []
        identities = set(deterministic)
        for index, candidate in enumerate(output.candidates):
            diagnostic = _candidate_diagnostic(index, candidate, raw_text)
            occurrences = _literal_occurrences(raw_text, candidate.matched_text)
            diagnostic["literal_occurrence_count"] = len(occurrences)
            if not occurrences:
                raise SemanticScreeningError("matched_text_not_found", diagnostic=diagnostic)
            if len(occurrences) != 1:
                raise SemanticScreeningError("matched_text_ambiguous", diagnostic=diagnostic)
            start_offset = occurrences[0]
            end_offset = start_offset + len(candidate.matched_text)
            diagnostic.update(
                {"resolved_start_offset": start_offset, "resolved_end_offset": end_offset}
            )
            # Python str indices are authoritative for both resolution and persistence.
            if raw_text[start_offset:end_offset] != candidate.matched_text:
                raise SemanticScreeningError("matched_text_not_found", diagnostic=diagnostic)
            contained_segments = [
                segment
                for segment in segments
                if segment.raw_start_offset <= start_offset and end_offset <= segment.raw_end_offset
            ]
            diagnostic["segment_containment_count"] = len(contained_segments)
            if not contained_segments:
                raise SemanticScreeningError("span_segment_not_found", diagnostic=diagnostic)
            if len(contained_segments) != 1:
                raise SemanticScreeningError("span_segment_ambiguous", diagnostic=diagnostic)

            identity = (candidate.rule_id, start_offset, end_offset)
            citation = citations.get(candidate.citation_key)
            if candidate.rule_id not in SEMANTIC_TAXONOMY or candidate.rule_id not in self.rules:
                raise SemanticScreeningError("taxonomy_rejected", diagnostic=diagnostic)
            if citation is None:
                raise SemanticScreeningError("citation_key_invalid", diagnostic=diagnostic)
            if candidate.cited_quote != citation.quote:
                raise SemanticScreeningError("citation_quote_invalid", diagnostic=diagnostic)
            if candidate.confidence != "high":
                raise SemanticScreeningError("confidence_rejected", diagnostic=diagnostic)
            if candidate.uncertainty != "none":
                raise SemanticScreeningError("uncertainty_rejected", diagnostic=diagnostic)
            if candidate.claim_polarity != "affirmative_marketing_claim":
                raise SemanticScreeningError("polarity_rejected", diagnostic=diagnostic)
            if not _affirmative_marketing_context(raw_text, start_offset, end_offset):
                raise SemanticScreeningError("non_affirmative_context", diagnostic=diagnostic)
            if identity in deterministic:
                raise SemanticScreeningError("deterministic_duplicate", diagnostic=diagnostic)
            if identity in identities:
                raise SemanticScreeningError("semantic_duplicate", diagnostic=diagnostic)
            identities.add(identity)
            accepted.append(
                ResolvedSemanticCandidate(
                    candidate=candidate,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    segment=contained_segments[0],
                )
            )
        return accepted


class TrustedRAGSemanticScreeningService:
    """Adds only validator-approved, cited semantic findings to an existing completed run."""

    def __init__(
        self,
        ruleset: MarketingRuleSet | None = None,
        settings: Settings | None = None,
        provider: SemanticScreeningProvider | None = None,
    ) -> None:
        self.ruleset = ruleset or load_ruleset()
        self.rules = {rule.rule_id: rule for rule in self.ruleset.rules}
        if set(self.rules) != SEMANTIC_TAXONOMY:
            raise SemanticScreeningError("semantic_taxonomy_ruleset_mismatch")
        self.settings = settings or get_settings()
        self.provider = provider or OpenAICompatibleSemanticScreeningProvider(self.settings)

    def enrich(self, session: Session, run: ScreeningRun) -> int:
        if not self.settings.semantic_screening_enabled:
            return 0
        citations = self._trusted_citations(session, run.material.raw_text)
        if not citations:
            return 0
        deterministic = {
            (item.rule_id, item.raw_start_offset, item.raw_end_offset) for item in run.findings
        }
        raw = self.provider.generate(
            raw_text=run.material.raw_text,
            taxonomy=[
                {"rule_id": rule.rule_id, "category": rule.category} for rule in self.ruleset.rules
            ],
            citations=[
                {"citation_key": item.citation_key, "cited_quote": item.quote}
                for item in citations.values()
            ],
        )
        segments = sorted(run.material.segments, key=lambda item: item.ordinal)
        accepted = SemanticOutputValidator(self.rules).validate(
            raw_json=raw,
            raw_text=run.material.raw_text,
            citations=citations,
            deterministic=deterministic,
            segments=segments,
        )
        if not accepted:
            return 0
        persisted = 0
        for resolved in accepted:
            candidate = resolved.candidate
            rule = self.rules[candidate.rule_id]
            citation = citations[candidate.citation_key]
            normalized = normalize_marketing_text(candidate.matched_text).text
            snapshot = rule.model_dump(mode="json")
            finding = RiskFinding(
                screening_run_id=run.id,
                segment_id=resolved.segment.id,
                rule_id=rule.rule_id,
                rule_version=rule.version,
                category=rule.category,
                severity=rule.severity,
                signal_strength=rule.signal_strength,
                matched_text=candidate.matched_text,
                raw_start_offset=resolved.start_offset,
                raw_end_offset=resolved.end_offset,
                normalized_match=normalized,
                explanation=rule.explanation_template,
                review_question=rule.review_question_template,
                remediation_template=rule.institution_remediation_template,
                consumer_notice_template=rule.consumer_notice_template,
                rule_snapshot_json=snapshot,
                rule_snapshot_sha256=_sha(snapshot),
                evidence_status=FindingEvidenceStatus.SUPPORTED.value,
                finding_sha256=_sha(
                    {
                        "version": SEMANTIC_SCREENING_VERSION,
                        "run": run.id,
                        "rule": rule.rule_id,
                        "start": resolved.start_offset,
                        "end": resolved.end_offset,
                        "citation": citation.chunk.chunk_identity_sha256,
                    }
                ),
            )
            session.add(finding)
            session.flush()
            session.add(
                FindingEvidenceLink(
                    finding_id=finding.id,
                    knowledge_chunk_id=citation.chunk.id,
                    support_type=rule.evidence_requirements[0]
                    if rule.evidence_requirements
                    else FindingSupportType.NORMATIVE_BASIS.value,
                    retrieval_rank=citation.retrieval_rank,
                    retrieval_score=citation.retrieval_score,
                    chunk_identity_sha256=citation.chunk.chunk_identity_sha256,
                    chunk_content_sha256=citation.chunk.chunk_content_sha256,
                    source_document_snapshot_json={
                        "source_document_id": citation.chunk.source_document_id,
                        "record_type": citation.chunk.record_type,
                        "chunk_kind": citation.chunk.chunk_kind,
                        "pilot_id": citation.chunk.pilot_id,
                        "title": citation.chunk.title,
                        "source_url": citation.chunk.source_url,
                        "authenticity_status": citation.chunk.authenticity_status,
                        "review_status": citation.chunk.review_status,
                    },
                    source_locator_snapshot_json=citation.chunk.source_locator_json,
                    evidence_references_snapshot_json=citation.chunk.evidence_reference_json,
                    support_evaluation_version=SEMANTIC_SCREENING_VERSION,
                    support_evaluation_passed=True,
                    matched_support_patterns=[],
                    actual_matched_substrings=[citation.quote],
                    matched_pattern_groups=[],
                    matched_evidence_fields=["semantic_screening_citation"],
                    support_reason="semantic_rag_citation_exact",
                    semantic_support_score=1.0,
                    semantic_support_reason="model_high_confidence_affirmative_claim",
                    context_scope="not_applicable",
                )
            )
            session.flush()
            persisted += 1
        return persisted

    def _trusted_citations(self, session: Session, raw_text: str) -> dict[str, SemanticCitation]:
        # Constructed contest fixtures are valid for the isolated Demo but never for semantic screening.
        rows = list(
            session.scalars(
                select(KnowledgeChunk)
                .join(SourceDocument)
                .where(KnowledgeChunk.is_active.is_(True))
            )
        )
        permitted = {
            item.chunk_identity_sha256: item
            for item in rows
            if item.authenticity_status == "verified_public"
            and item.review_status in APPROVABLE_STATUSES
            and item.source_document.authenticity_type == "verified_public"
            and item.source_document.final_review_status in APPROVABLE_STATUSES
            and item.source_document.knowledge_index_status == "indexed"
            and not bool((item.source_document.metadata_json or {}).get("constructed_contest_demo"))
        }
        if not permitted:
            return {}
        search = TrustedKnowledgeSearchService()
        selected: dict[str, SemanticCitation] = {}
        for rule in self.ruleset.rules:
            results = search.search(
                session, SearchRequest(query=" ".join(rule.retrieval_queries)[:500], limit=3)
            )
            for result in results:
                chunk = permitted.get(result.chunk_identity_sha256)
                if chunk is None:
                    continue
                quote = next(
                    (
                        str(ref.get("quote"))
                        for ref in chunk.evidence_reference_json
                        if isinstance(ref, dict)
                        and isinstance(ref.get("quote"), str)
                        and ref["quote"] in chunk.text
                        and len(ref["quote"]) <= 600
                    ),
                    None,
                )
                if quote is None:
                    continue
                key = f"S{len(selected) + 1:03d}"
                selected.setdefault(
                    chunk.chunk_identity_sha256,
                    SemanticCitation(key, chunk, quote, result.rank, result.score),
                )
                if len(selected) >= 12:
                    return {item.citation_key: item for item in selected.values()}
        return {item.citation_key: item for item in selected.values()}


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _literal_occurrences(text: str, matched_text: str) -> list[int]:
    """Return every literal occurrence, including overlaps, using Python str indices."""
    occurrences: list[int] = []
    start = 0
    while True:
        index = text.find(matched_text, start)
        if index < 0:
            return occurrences
        occurrences.append(index)
        start = index + 1


def _candidate_diagnostic(
    index: int, candidate: SemanticCandidate, raw_text: str
) -> dict[str, object]:
    """Keep rejection diagnostics useful without retaining provider or source text."""
    return {
        "candidate_index": index,
        "rule_id": candidate.rule_id,
        "citation_key": candidate.citation_key,
        "raw_text_sha256": hashlib.sha256(raw_text.encode()).hexdigest(),
        "matched_text_sha256": hashlib.sha256(candidate.matched_text.encode()).hexdigest(),
    }


def _is_https_url(value: str) -> bool:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        return False
    return url.scheme == "https" and bool(url.host) and not bool(url.username or url.password)


def _affirmative_marketing_context(text: str, start: int, end: int) -> bool:
    """Reject obvious local negation, warning, prohibition, or corrective contexts."""
    left = text[max(0, start - 16) : start]
    right = text[end : min(len(text), end + 12)]
    negative_markers = ("不保证", "不承诺", "不得", "禁止", "严禁", "切勿", "不是", "并非")
    if left.endswith("不") or any(marker in left or marker in right for marker in negative_markers):
        return False
    return not any(
        marker in left or marker in right
        for marker in ("风险提示", "可能产生损失", "以合同约定为准")
    )
