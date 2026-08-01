from __future__ import annotations

from typing import Protocol

from app.services.explanation.prompts import canonical_json_bytes, canonical_sha256
from app.services.explanation.schemas import (
    ExplanationProviderRequest,
    ExplanationProviderResponse,
)


class ProviderGenerationError(Exception):
    pass


class ExplanationProvider(Protocol):
    provider_name: str
    provider_model: str

    @property
    def safe_configuration(self) -> dict[str, object]: ...

    def generate(self, request: ExplanationProviderRequest) -> ExplanationProviderResponse: ...


class DisabledExternalProvider:
    provider_name = "external"
    provider_model = "not_configured"

    @property
    def safe_configuration(self) -> dict[str, object]:
        return {"enabled": False}

    def generate(self, request: ExplanationProviderRequest) -> ExplanationProviderResponse:
        del request
        raise ProviderGenerationError("explanation_provider_not_configured")


class DeterministicFixtureProvider:
    provider_name = "deterministic_fixture"
    provider_model = "controlled-fixture-v1"

    def __init__(self, scenario: str = "valid") -> None:
        self.scenario = scenario

    @property
    def safe_configuration(self) -> dict[str, object]:
        return {"scenario": self.scenario, "version": "controlled_fixture_provider_v1"}

    @property
    def configuration_sha256(self) -> str:
        return canonical_sha256(self.safe_configuration)

    def generate(self, request: ExplanationProviderRequest) -> ExplanationProviderResponse:
        if self.scenario == "timeout":
            raise ProviderGenerationError("explanation_provider_timeout")
        if self.scenario == "exception":
            raise ProviderGenerationError("explanation_provider_failed")
        if self.scenario == "empty":
            return ExplanationProviderResponse(raw_json="")
        if self.scenario == "markdown":
            return ExplanationProviderResponse(raw_json="```json\n{}\n```")
        payload = self._valid_payload(request)
        self._mutate(payload, request)
        return ExplanationProviderResponse(raw_json=canonical_json_bytes(payload).decode("utf-8"))

    def _valid_payload(self, request: ExplanationProviderRequest) -> dict[str, object]:
        uncertain = {"partially_supported", "evidence_insufficient"}
        illustrative = "illustrative_not_material_specific"
        if request.audience == "institution":
            rows = []
            for finding in request.context.findings:
                assessment = "现有入选证据支持该风险信号，仍需人工复核完整材料。"
                if finding.evidence_status in uncertain:
                    assessment = "当前证据不足以作出结论，需要进一步核验。"
                if any(item.context_scope == illustrative for item in finding.evidence):
                    assessment += request.prompt.illustrative_product_disclaimer
                rows.append(
                    {
                        "finding_key": finding.finding_key,
                        "explanation": f"确定性规则识别到“{finding.matched_text}”这一风险信号。",
                        "why_it_matters": finding.deterministic_explanation,
                        "evidence_assessment": assessment,
                        "review_actions": [finding.review_question],
                        "citations": [
                            {
                                "citation_key": item.citation_key,
                                "cited_quote": item.quote,
                            }
                            for item in finding.evidence
                        ],
                    }
                )
            return {
                "schema_version": "institution_explanation_v1",
                "executive_summary": "以下内容仅解释已持久化的确定性筛查发现。",
                "finding_explanations": rows,
                "cross_finding_observations": [],
                "manual_review_priorities": [
                    item.review_question for item in request.context.findings
                ],
                "disclaimer": request.prompt.required_disclaimer,
            }
        rows = []
        all_citations = []
        for finding in request.context.findings:
            explanation = f"材料中的“{finding.matched_text}”需要您进一步核对。"
            if finding.evidence_status in uncertain:
                explanation += "当前证据不足以作出结论，需要进一步核验。"
            if any(item.context_scope == illustrative for item in finding.evidence):
                explanation += request.prompt.illustrative_product_disclaimer
            citations = [
                {"citation_key": item.citation_key, "cited_quote": item.quote}
                for item in finding.evidence
            ]
            all_citations.extend(citations)
            rows.append(
                {
                    "finding_key": finding.finding_key,
                    "plain_language_explanation": explanation,
                    "what_to_check": [finding.review_question, "请核对正式保险合同。"],
                    "citations": citations,
                }
            )
        return {
            "schema_version": "consumer_explanation_v1",
            "overall_notice": "这是对既有风险筛查结果的通俗说明。",
            "risk_explanations": rows,
            "questions_to_ask": [item.review_question for item in request.context.findings],
            "evidence_links": all_citations,
            "disclaimer": request.prompt.required_disclaimer,
        }

    def _mutate(self, payload: dict[str, object], request: ExplanationProviderRequest) -> None:
        if self.scenario == "valid":
            return
        rows_key = (
            "finding_explanations" if request.audience == "institution" else "risk_explanations"
        )
        rows = payload[rows_key]
        assert isinstance(rows, list) and rows
        first = rows[0]
        assert isinstance(first, dict)
        citations = first["citations"]
        assert isinstance(citations, list) and citations
        first_citation = citations[0]
        assert isinstance(first_citation, dict)
        narrative_key = (
            "explanation" if request.audience == "institution" else "plain_language_explanation"
        )
        if self.scenario == "unknown_finding":
            first["finding_key"] = "F999"
        elif self.scenario == "unknown_citation":
            first_citation["citation_key"] = "E999"
        elif self.scenario == "citation_mismatch":
            first_citation["cited_quote"] = "被篡改且不存在的证据摘录"
        elif self.scenario == "legal_conclusion":
            first[narrative_key] = "该营销材料已违法。"
        elif self.scenario == "financial_advice":
            first[narrative_key] = "建议立即退保。"
        elif self.scenario == "guarantee":
            first[narrative_key] = "系统保证收益并保证赔付。"
        elif self.scenario == "missing_uncertainty":
            for row, finding in zip(rows, request.context.findings, strict=True):
                if finding.evidence_status in {"partially_supported", "evidence_insufficient"}:
                    row[narrative_key] = "该风险已经得到确定证明。"
                    if request.audience == "institution":
                        row["evidence_assessment"] = "证据充分。"
                    break
        elif self.scenario == "missing_product_disclaimer":
            for row in rows:
                for key, value in list(row.items()):
                    if isinstance(value, str):
                        row[key] = value.replace(request.prompt.illustrative_product_disclaimer, "")
        elif self.scenario == "missing_disclaimer":
            payload["disclaimer"] = ""
        elif self.scenario == "html":
            first[narrative_key] = "<script>alert(1)</script>"
        elif self.scenario == "extra_field":
            first["severity"] = "critical"
        elif self.scenario == "duplicate_citation":
            citations.append(dict(first_citation))
        elif self.scenario == "non_json":
            payload.clear()


def provider_configuration_sha256(provider: ExplanationProvider) -> str:
    return canonical_sha256(provider.safe_configuration)


def provider_from_name(name: str, scenario: str = "valid") -> ExplanationProvider:
    if name == "deterministic_fixture":
        return DeterministicFixtureProvider(scenario)
    if name == "external":
        return DisabledExternalProvider()
    raise ProviderGenerationError("explanation_provider_not_configured")
