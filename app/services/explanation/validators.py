from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from app.core.exceptions import ExplanationError
from app.services.explanation.context import BuiltContext, EvidenceBinding
from app.services.explanation.schemas import (
    AllowedEvidenceSegment,
    ConsumerExplanationV1,
    ExplanationOutput,
    GroundedClaimV1,
    InstitutionExplanationV1,
    OutputCitation,
    PromptDefinition,
    ResolvedCitationV1,
    ValidatedCitation,
    ValidatedExplanation,
)

UNCERTAINTY_EXPRESSIONS = (
    "可能存在",
    "可能涉及",
    "风险信号",
    "需要进一步核验",
    "尚需核验",
    "部分支持",
    "当前证据有限",
    "当前证据不足",
    "尚不能作出确定结论",
    "仍需结合原始材料",
    "不能据此直接认定",
    "当前证据不足以作出结论",
)
PARTIAL_CERTAINTY_PATTERNS = (
    r"已经得到确定证明",
    r"证据充分证明",
    r"已经确认",
    r"(?<!不)可以确认",
    r"(?<!不)可以认定",
    r"事实明确",
    r"确定存在",
    r"已被证实",
    r"无疑[^地]",
    r"已经查明",
    r"结论明确",
    r"必然",
    r"一定构成",
    r"证据充分[^证]",
    r"可以得出明确结论",
)
LEGAL_CONCLUSION_PATTERNS = (
    r"已违法",
    r"(?<!不)构成违法",
    r"(?<!不)构成欺诈",
    r"必将受到处罚",
    r"监管已经认定",
)
FINANCIAL_ADVICE_PATTERNS = (
    r"建议(?:立即)?退保",
    r"建议购买",
    r"建议投资",
)
UNSUPPORTED_CLAIM_PATTERNS = (
    r"保证赔付",
    r"(?:系统|本解释|本产品|该产品)保证收益",
    r"一定可以全额退款",
    r"不存在任何风险",
)
UNSAFE_MARKUP_PATTERN = re.compile(r"<\s*/?\s*(?:script|iframe|object|embed|html)\b", re.I)
NUMBER_TOKEN_PATTERN = re.compile(
    r"(?:[A-Za-z]+[\- ]?)?(?:\d+(?:\.\d+)?)(?:%|％|万?元|人|年|月|日|号)?"
)
MIN_SUBSTANTIVE_QUOTE_LENGTH = 6
SHORT_DIRECT_WORDING_ALLOWLIST = frozenset({"优惠", "中奖"})
CITATION_FORMAT_V1 = "E followed by exactly three ASCII digits"
DETERMINISTIC_TEMPLATE_TEXT = frozenset(
    {
        "以下内容仅解释已持久化的确定性筛查发现。",
        "这是对既有风险筛查结果的通俗说明。",
        "当前证据不足以作出结论，需要进一步核验。",
        "请核对正式保险合同。",
    }
)


class UnsupportedClaimDetectorV1:
    version = "unsupported_claim_detector_v1"

    def detect(self, output: ExplanationOutput, prompt: PromptDefinition) -> str | None:
        text = "\n".join(_narrative_strings(output.model_dump(mode="json")))
        for pattern in prompt.forbidden_claim_patterns:
            if re.search(pattern, text):
                return _forbidden_pattern_code(pattern)
        if any(re.search(pattern, text) for pattern in LEGAL_CONCLUSION_PATTERNS):
            return "explanation_legal_conclusion_detected"
        if any(re.search(pattern, text) for pattern in FINANCIAL_ADVICE_PATTERNS):
            return "explanation_financial_advice_detected"
        if any(re.search(pattern, text) for pattern in UNSUPPORTED_CLAIM_PATTERNS):
            return "explanation_unsupported_claim"
        if UNSAFE_MARKUP_PATTERN.search(text):
            return "explanation_unsupported_claim"
        return None


class ExplanationCitationValidator:
    def validate(
        self,
        built: BuiltContext,
        allowed_finding_keys: set[str],
        citations: list[OutputCitation],
    ) -> list[ValidatedCitation]:
        keys = [citation.citation_key for citation in citations]
        if len(keys) != len(set(keys)):
            raise ExplanationError("explanation_duplicate_citation")
        validated: list[ValidatedCitation] = []
        for citation in citations:
            binding = built.bindings.get(citation.citation_key)
            if binding is None:
                raise ExplanationError("explanation_unknown_citation_key")
            if binding.finding_key not in allowed_finding_keys:
                raise ExplanationError("explanation_citation_wrong_finding")
            normalized_length = len(re.sub(r"\s+", "", citation.cited_quote))
            if (
                normalized_length < MIN_SUBSTANTIVE_QUOTE_LENGTH
                and citation.cited_quote not in SHORT_DIRECT_WORDING_ALLOWLIST
            ):
                raise ExplanationError("explanation_citation_too_short")
            segment = self._segment(binding, citation.cited_quote)
            if segment is None:
                metadata_values = {
                    binding.source_title,
                    binding.source_url,
                    binding.pilot_id or "",
                }
                code = (
                    "explanation_citation_not_substantive"
                    if citation.cited_quote in metadata_values
                    else "explanation_citation_snapshot_mismatch"
                )
                raise ExplanationError(code)
            start = segment.quote.find(citation.cited_quote)
            validated.append(
                ValidatedCitation(
                    citation_key=citation.citation_key,
                    finding_key=binding.finding_key,
                    finding_id=binding.finding_id,
                    finding_evidence_link_id=binding.link_id,
                    chunk_identity_sha256=binding.chunk_identity_sha256,
                    chunk_content_sha256=binding.chunk_content_sha256,
                    cited_quote=citation.cited_quote,
                    quote_start_offset=start,
                    quote_end_offset=start + len(citation.cited_quote),
                    support_type=binding.support_type,
                    source_title=binding.source_title,
                    source_url=binding.source_url,
                    pilot_id=binding.pilot_id,
                    record_type=binding.record_type,
                    chunk_kind=binding.chunk_kind,
                    source_locator=binding.source_locator,
                    context_scope=binding.context_scope,
                    evidence_field_name=segment.field_name,
                )
            )
        return validated

    @staticmethod
    def _segment(binding: EvidenceBinding, quote: str) -> AllowedEvidenceSegment | None:
        for segment in binding.allowed_quote_segments:
            if quote in segment.quote:
                return segment
        return None


class ControlledExplanationValidator:
    def __init__(self) -> None:
        self.citations = ExplanationCitationValidator()
        self.claims = UnsupportedClaimDetectorV1()

    def validate(
        self,
        raw_json: str,
        prompt: PromptDefinition,
        built: BuiltContext,
    ) -> ValidatedExplanation:
        if prompt.citation_format != CITATION_FORMAT_V1:
            raise ExplanationError("explanation_output_invalid_schema")
        try:
            raw = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ExplanationError("explanation_output_invalid_schema") from exc
        if not isinstance(raw, dict):
            raise ExplanationError("explanation_output_invalid_schema")
        if raw.get("disclaimer") != prompt.required_disclaimer:
            raise ExplanationError("explanation_missing_disclaimer")
        try:
            output: ExplanationOutput
            rows: list[Any]
            if prompt.audience == "institution":
                output = InstitutionExplanationV1.model_validate(raw)
                rows = list(output.finding_explanations)
            else:
                output = ConsumerExplanationV1.model_validate(raw)
                rows = list(output.risk_explanations)
        except ValidationError as exc:
            raise ExplanationError("explanation_output_invalid_schema") from exc
        if output.schema_version != prompt.output_schema_version:
            raise ExplanationError("explanation_output_invalid_schema")
        context_by_key = {item.finding_key: item for item in built.payload.findings}
        row_keys = [row.finding_key for row in rows]
        if set(row_keys) - set(context_by_key):
            raise ExplanationError("explanation_unknown_finding_key")
        if Counter(row_keys) != Counter(context_by_key.keys()):
            raise ExplanationError("explanation_output_invalid_schema")
        claim_error = self.claims.detect(output, prompt)
        if claim_error:
            raise ExplanationError(claim_error)

        validated_by_key: dict[str, ValidatedCitation] = {}
        for claim, scope in self._claims(output):
            self._validate_claim(
                claim,
                scope,
                prompt,
                built,
                context_by_key,
                validated_by_key,
            )

        for row in rows:
            context_finding = context_by_key[row.finding_key]
            row_claims = [claim for claim, _ in self._row_claims(row)]
            narrative = "\n".join(claim.text for claim in row_claims)
            row_citation_count = sum(len(claim.citations) for claim in row_claims)
            if context_finding.evidence_status == "evidence_insufficient" and not any(
                value in narrative for value in UNCERTAINTY_EXPRESSIONS
            ):
                raise ExplanationError("explanation_missing_uncertainty")
            if context_finding.evidence_status in {"supported", "partially_supported"}:
                if row_citation_count == 0:
                    raise ExplanationError("explanation_unsupported_claim")
            else:
                if row_citation_count or any(
                    claim.claim_type != "deterministic_template" for claim in row_claims
                ):
                    raise ExplanationError("explanation_unsupported_claim")
            if (
                any(
                    evidence.context_scope == "illustrative_not_material_specific"
                    for evidence in context_finding.evidence
                )
                and prompt.illustrative_product_disclaimer not in narrative
            ):
                raise ExplanationError("explanation_missing_product_context_disclaimer")

        if isinstance(output, ConsumerExplanationV1):
            catalog = self.citations.validate(
                built,
                set(context_by_key),
                list(output.evidence_links),
            )
            expected = set(validated_by_key)
            actual = {item.citation_key for item in catalog}
            if actual != expected:
                raise ExplanationError("explanation_output_invalid_schema")

        validated = [validated_by_key[key] for key in sorted(validated_by_key)]
        return ValidatedExplanation(
            output=output.model_dump(mode="json"),
            citations=validated,
            resolved_citations=[_resolved(item) for item in validated],
        )

    def _validate_claim(
        self,
        claim: GroundedClaimV1,
        scope: set[str],
        prompt: PromptDefinition,
        built: BuiltContext,
        context_by_key: dict[str, Any],
        validated_by_key: dict[str, ValidatedCitation],
    ) -> None:
        if not scope or scope - set(context_by_key):
            raise ExplanationError("explanation_unknown_finding_key")
        if claim.claim_type not in prompt.allowed_claim_types:
            raise ExplanationError("explanation_output_invalid_schema")
        if set(claim.finding_keys) != scope:
            raise ExplanationError("explanation_unknown_finding_key")
        if claim.claim_type == "deterministic_template":
            if claim.citations or claim.text not in self._template_texts(scope, context_by_key):
                raise ExplanationError("explanation_unsupported_claim")
            return
        if not claim.citations:
            raise ExplanationError("explanation_unsupported_claim")
        citations = self.citations.validate(built, scope, list(claim.citations))
        self._check_partial_uncertainty(claim, scope, context_by_key)
        self._check_cross_finding_citation_coverage(scope, citations)
        cited_text = "\n".join(item.cited_quote for item in citations)
        deterministic_text = "\n".join(
            value
            for key in scope
            for value in (
                context_by_key[key].matched_text,
                context_by_key[key].deterministic_explanation,
                context_by_key[key].review_question,
            )
        )
        for token in NUMBER_TOKEN_PATTERN.findall(claim.text):
            if token in cited_text or token in deterministic_text:
                continue
            if re.match(r"^[FE][0-9]{3}$", token):
                continue
            raise ExplanationError("explanation_unsupported_claim")
        for item in citations:
            previous = validated_by_key.get(item.citation_key)
            if previous is not None and previous != item:
                raise ExplanationError("explanation_citation_snapshot_mismatch")
            validated_by_key[item.citation_key] = item

    @staticmethod
    def _template_texts(scope: set[str], context_by_key: dict[str, Any]) -> set[str]:
        result = set(DETERMINISTIC_TEMPLATE_TEXT)
        for key in scope:
            finding = context_by_key[key]
            result.update({finding.deterministic_explanation, finding.review_question})
        return result

    @staticmethod
    def _check_partial_uncertainty(
        claim: GroundedClaimV1,
        scope: set[str],
        context_by_key: dict[str, Any],
    ) -> None:
        has_partial = any(
            context_by_key[key].evidence_status == "partially_supported" for key in scope
        )
        if not has_partial or claim.claim_type == "deterministic_template":
            return
        if not claim.text.strip() or not claim.citations:
            raise ExplanationError("explanation_missing_uncertainty")
        for pattern in PARTIAL_CERTAINTY_PATTERNS:
            if re.search(pattern, claim.text):
                raise ExplanationError("explanation_missing_uncertainty")
        if not any(value in claim.text for value in UNCERTAINTY_EXPRESSIONS):
            raise ExplanationError("explanation_missing_uncertainty")

    @staticmethod
    def _check_cross_finding_citation_coverage(
        scope: set[str],
        citations: list[ValidatedCitation],
    ) -> None:
        covered: set[str] = set()
        for citation in citations:
            if citation.finding_key in scope:
                covered.add(citation.finding_key)
        if covered != scope:
            raise ExplanationError("explanation_citation_wrong_finding")

    @staticmethod
    def _row_claims(row: Any) -> Iterable[tuple[GroundedClaimV1, set[str]]]:
        scope = {row.finding_key}
        if hasattr(row, "explanation"):
            yield row.explanation, scope
            yield row.why_it_matters, scope
            yield row.evidence_assessment, scope
            for claim in row.review_actions:
                yield claim, scope
        else:
            yield row.plain_language_explanation, scope
            for claim in row.what_to_check:
                yield claim, scope

    def _claims(self, output: ExplanationOutput) -> Iterable[tuple[GroundedClaimV1, set[str]]]:
        all_keys = {
            row.finding_key
            for row in (
                output.finding_explanations
                if isinstance(output, InstitutionExplanationV1)
                else output.risk_explanations
            )
        }
        if isinstance(output, InstitutionExplanationV1):
            yield output.executive_summary, set(output.executive_summary.finding_keys)
            for row in output.finding_explanations:
                yield from self._row_claims(row)
            for claim in output.cross_finding_observations:
                yield claim, set(claim.finding_keys)
            for claim in output.manual_review_priorities:
                yield claim, set(claim.finding_keys)
        else:
            yield output.overall_notice, set(output.overall_notice.finding_keys)
            for consumer_row in output.risk_explanations:
                yield from self._row_claims(consumer_row)
            for claim in output.questions_to_ask:
                yield claim, set(claim.finding_keys)
        if not all_keys:
            raise ExplanationError("explanation_output_invalid_schema")


def _resolved(item: ValidatedCitation) -> ResolvedCitationV1:
    return ResolvedCitationV1(
        citation_key=item.citation_key,
        finding_key=item.finding_key,
        support_type=item.support_type,
        source_title=item.source_title,
        source_url=item.source_url,
        pilot_id=item.pilot_id,
        record_type=item.record_type,
        chunk_kind=item.chunk_kind,
        source_locator=item.source_locator,
        context_scope=item.context_scope,
        evidence_field_name=item.evidence_field_name,
        cited_quote=item.cited_quote,
        chunk_identity_sha256=item.chunk_identity_sha256,
        chunk_content_sha256=item.chunk_content_sha256,
    )


def _forbidden_pattern_code(pattern: str) -> str:
    if any(value in pattern for value in ("违法", "欺诈", "处罚", "监管已经认定")):
        return "explanation_legal_conclusion_detected"
    if any(value in pattern for value in ("购买", "退保", "投资")):
        return "explanation_financial_advice_detected"
    return "explanation_unsupported_claim"


def _narrative_strings(value: Any, key: str | None = None) -> list[str]:
    if key in {
        "citation_key",
        "cited_quote",
        "source_url",
        "source_locator",
        "source_title",
        "disclaimer",
    }:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child_key, child in value.items():
            result.extend(_narrative_strings(child, child_key))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(_narrative_strings(child, key))
        return result
    return []
