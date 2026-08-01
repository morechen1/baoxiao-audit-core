from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from pydantic import ValidationError

from app.core.exceptions import ExplanationError
from app.services.explanation.context import BuiltContext
from app.services.explanation.schemas import (
    ConsumerExplanationV1,
    ExplanationOutput,
    InstitutionExplanationV1,
    OutputCitation,
    PromptDefinition,
    ValidatedCitation,
    ValidatedExplanation,
)

UNCERTAINTY_EXPRESSIONS = (
    "需要进一步核验",
    "当前证据不足以作出结论",
    "可能存在风险信号",
    "现有证据显示",
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


class UnsupportedClaimDetectorV1:
    version = "unsupported_claim_detector_v1"

    def detect(self, output: ExplanationOutput) -> str | None:
        text = "\n".join(_narrative_strings(output.model_dump(mode="json")))
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
        finding_key: str,
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
            if binding.finding_key != finding_key:
                raise ExplanationError("explanation_citation_wrong_finding")
            start = binding.quote.find(citation.cited_quote)
            if start < 0:
                raise ExplanationError("explanation_citation_snapshot_mismatch")
            validated.append(
                ValidatedCitation(
                    citation_key=citation.citation_key,
                    finding_key=finding_key,
                    finding_id=binding.finding_id,
                    finding_evidence_link_id=binding.link_id,
                    chunk_identity_sha256=binding.chunk_identity_sha256,
                    chunk_content_sha256=binding.chunk_content_sha256,
                    cited_quote=citation.cited_quote,
                    quote_start_offset=start,
                    quote_end_offset=start + len(citation.cited_quote),
                )
            )
        return validated


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
        unknown = set(row_keys) - set(context_by_key)
        if unknown:
            raise ExplanationError("explanation_unknown_finding_key")
        if Counter(row_keys) != Counter(context_by_key.keys()):
            raise ExplanationError("explanation_output_invalid_schema")
        claim_error = self.claims.detect(output)
        if claim_error:
            raise ExplanationError(claim_error)

        validated: list[ValidatedCitation] = []
        for row in rows:
            context_finding = context_by_key[row.finding_key]
            narrative = self._row_narrative(row)
            if context_finding.evidence_status in {
                "partially_supported",
                "evidence_insufficient",
            } and not any(value in narrative for value in UNCERTAINTY_EXPRESSIONS):
                raise ExplanationError("explanation_missing_uncertainty")
            if (
                any(
                    evidence.context_scope == "illustrative_not_material_specific"
                    for evidence in context_finding.evidence
                )
                and prompt.illustrative_product_disclaimer not in narrative
            ):
                raise ExplanationError("explanation_missing_product_context_disclaimer")
            validated.extend(self.citations.validate(built, row.finding_key, list(row.citations)))

        if isinstance(output, ConsumerExplanationV1):
            allowed = set(built.bindings)
            for citation in output.evidence_links:
                if citation.citation_key not in allowed:
                    raise ExplanationError("explanation_unknown_citation_key")
                binding = built.bindings[citation.citation_key]
                if citation.cited_quote not in binding.quote:
                    raise ExplanationError("explanation_citation_snapshot_mismatch")
        return ValidatedExplanation(
            output=output.model_dump(mode="json"),
            citations=validated,
        )

    @staticmethod
    def _row_narrative(row: Any) -> str:
        values = row.model_dump(mode="json")
        return "\n".join(_narrative_strings(values))


def _narrative_strings(value: Any, key: str | None = None) -> list[str]:
    if key in {"citation_key", "cited_quote"}:
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
