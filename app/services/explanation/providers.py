from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

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
    provider_model = "controlled-fixture-v2"
    valid_scenarios = {
        "valid",
        "historical_prompt",
        "historical_context",
        "cross_database",
        "deterministic_rerun",
        "all_sources_distinguished",
        "evidence_insufficient",
    }

    def __init__(self, scenario: str = "valid") -> None:
        self.scenario = scenario

    @property
    def safe_configuration(self) -> dict[str, object]:
        return {"scenario": self.scenario, "version": "controlled_fixture_provider_v2"}

    @property
    def configuration_sha256(self) -> str:
        return canonical_sha256(self.safe_configuration)

    def generate(self, request: ExplanationProviderRequest) -> ExplanationProviderResponse:
        if self.scenario == "provider_timeout":
            raise ProviderGenerationError("explanation_provider_timeout")
        if self.scenario == "provider_exception":
            raise ProviderGenerationError("explanation_provider_failed")
        if self.scenario == "empty":
            return ExplanationProviderResponse(raw_json="")
        if self.scenario == "markdown":
            return ExplanationProviderResponse(raw_json="```json\n{}\n```")
        payload = self._valid_payload(request)
        self._mutate(payload, request)
        return ExplanationProviderResponse(raw_json=canonical_json_bytes(payload).decode("utf-8"))

    @staticmethod
    def _template(text: str, finding_key: str) -> dict[str, object]:
        return {
            "claim_type": "deterministic_template",
            "text": text,
            "finding_keys": [finding_key],
            "citations": [],
        }

    @staticmethod
    def _grounded(
        claim_type: str,
        text: str,
        finding_key: str,
        citations: list[dict[str, str]],
    ) -> dict[str, object]:
        return {
            "claim_type": claim_type,
            "text": text,
            "finding_keys": [finding_key],
            "citations": citations,
        }

    def _valid_payload(self, request: ExplanationProviderRequest) -> dict[str, object]:
        uncertain = {"partially_supported", "evidence_insufficient"}
        illustrative = "illustrative_not_material_specific"
        finding_keys = [item.finding_key for item in request.context.findings]
        if request.audience == "institution":
            rows = []
            for finding in request.context.findings:
                citations = [
                    {"citation_key": item.citation_key, "cited_quote": item.quote}
                    for item in finding.evidence
                ]
                if finding.evidence_status == "evidence_insufficient":
                    explanation = self._template(
                        "当前证据不足以作出结论，需要进一步核验。",
                        finding.finding_key,
                    )
                    assessment = self._template(
                        "当前证据不足以作出结论，需要进一步核验。",
                        finding.finding_key,
                    )
                else:
                    base_text = (
                        "现有证据显示，确定性规则识别到“" + finding.matched_text + "”这一表述。"
                    )
                    if finding.evidence_status in uncertain:
                        base_text += "可能存在风险信号，需要进一步核验。"
                    else:
                        base_text += "需要进一步核验。"
                    explanation = self._grounded(
                        "deterministic_finding_explanation",
                        base_text,
                        finding.finding_key,
                        citations[:1],
                    )
                    assessment_text = "现有证据显示可能存在风险信号，需要进一步核验。"
                    if finding.evidence_status in uncertain:
                        assessment_text = "现有证据显示可能存在风险信号，需要进一步核验。"
                    if any(item.context_scope == illustrative for item in finding.evidence):
                        assessment_text += request.prompt.illustrative_product_disclaimer
                    assessment = self._grounded(
                        "evidence_assessment",
                        assessment_text,
                        finding.finding_key,
                        citations,
                    )
                rows.append(
                    {
                        "finding_key": finding.finding_key,
                        "explanation": explanation,
                        "why_it_matters": self._template(
                            finding.deterministic_explanation, finding.finding_key
                        ),
                        "evidence_assessment": assessment,
                        "review_actions": [
                            self._template(finding.review_question, finding.finding_key)
                        ],
                    }
                )
            return {
                "schema_version": "institution_explanation_v1",
                "executive_summary": {
                    "claim_type": "deterministic_template",
                    "text": "以下内容仅解释已持久化的确定性筛查发现。",
                    "finding_keys": finding_keys,
                    "citations": [],
                },
                "finding_explanations": rows,
                "cross_finding_observations": [],
                "manual_review_priorities": [
                    self._template(item.review_question, item.finding_key)
                    for item in request.context.findings
                ],
                "disclaimer": request.prompt.required_disclaimer,
            }
        rows = []
        all_citations: list[dict[str, str]] = []
        for finding in request.context.findings:
            citations = [
                {"citation_key": item.citation_key, "cited_quote": item.quote}
                for item in finding.evidence
            ]
            all_citations.extend(citations)
            if finding.evidence_status == "evidence_insufficient":
                explanation = self._template(
                    "当前证据不足以作出结论，需要进一步核验。",
                    finding.finding_key,
                )
            else:
                text = "现有证据显示，材料中的" + finding.matched_text + "需要您进一步核对。"
                if finding.evidence_status in uncertain:
                    text += "需要进一步核验。"
                if any(item.context_scope == illustrative for item in finding.evidence):
                    text += request.prompt.illustrative_product_disclaimer
                explanation = self._grounded(
                    "plain_language_finding_explanation",
                    text,
                    finding.finding_key,
                    citations,
                )
            rows.append(
                {
                    "finding_key": finding.finding_key,
                    "plain_language_explanation": explanation,
                    "what_to_check": [
                        self._template(finding.review_question, finding.finding_key),
                        self._template("请核对正式保险合同。", finding.finding_key),
                    ],
                }
            )
        return {
            "schema_version": "consumer_explanation_v1",
            "overall_notice": {
                "claim_type": "deterministic_template",
                "text": "这是对既有风险筛查结果的通俗说明。",
                "finding_keys": finding_keys,
                "citations": [],
            },
            "risk_explanations": rows,
            "questions_to_ask": [
                self._template(item.review_question, item.finding_key)
                for item in request.context.findings
            ],
            "evidence_links": all_citations,
            "disclaimer": request.prompt.required_disclaimer,
        }

    def _mutate(self, payload: dict[str, object], request: ExplanationProviderRequest) -> None:
        if self.scenario in self.valid_scenarios:
            return
        rows_key = (
            "finding_explanations" if request.audience == "institution" else "risk_explanations"
        )
        rows = payload[rows_key]
        assert isinstance(rows, list) and rows
        first = rows[0]
        assert isinstance(first, dict)
        claim_key = (
            "explanation" if request.audience == "institution" else "plain_language_explanation"
        )
        claim = first[claim_key]
        assert isinstance(claim, dict)
        citations = claim["citations"]
        assert isinstance(citations, list)
        first_citation = citations[0] if citations else None
        if self.scenario == "unknown_finding":
            first["finding_key"] = "F999"
        elif self.scenario in {"unknown_citation", "unselected_chunk", "regulatory_case"}:
            assert isinstance(first_citation, dict)
            first_citation["citation_key"] = "E999"
        elif self.scenario == "wrong_finding":
            assert isinstance(first_citation, dict)
            first_citation["citation_key"] = _other_citation(request, first["finding_key"])
        elif self.scenario in {"citation_mismatch", "tampered_quote", "joined_quote"}:
            assert isinstance(first_citation, dict)
            first_citation["cited_quote"] = "不存在的篡改引文"
        elif self.scenario == "continuous_short_quote":
            assert isinstance(first_citation, dict)
            first_citation["cited_quote"] = str(first_citation["cited_quote"])[:6]
        elif self.scenario == "new_risk":
            extra = deepcopy(first)
            extra["finding_key"] = "F999"
            rows.append(extra)
        elif self.scenario in {
            "legal_conclusion",
            "legal_illegal",
            "legal_fraud",
            "certain_penalty",
        }:
            claim["text"] = {
                "legal_fraud": "该材料构成欺诈。",
                "certain_penalty": "该主体必将受到处罚。",
            }.get(self.scenario, "该营销材料已违法。")
        elif self.scenario in {"financial_advice", "advice_buy", "advice_surrender"}:
            claim["text"] = "建议购买。" if self.scenario == "advice_buy" else "建议立即退保。"
        elif self.scenario in {"guarantee", "guaranteed_return", "guaranteed_claim"}:
            claim["text"] = "系统保证收益并保证赔付。"
        elif self.scenario in {"missing_uncertainty", "insufficient_hidden"}:
            target_index = next(
                (
                    index
                    for index, finding in enumerate(request.context.findings)
                    if finding.evidence_status in {"partially_supported", "evidence_insufficient"}
                ),
                0,
            )
            target = rows[target_index]
            assert isinstance(target, dict)
            target_claim = target[claim_key]
            assert isinstance(target_claim, dict)
            target_claim["text"] = "该风险已经得到确定证明。"
            if request.audience == "institution":
                assessment = target["evidence_assessment"]
                assert isinstance(assessment, dict)
                assessment["text"] = "证据充分。"
        elif self.scenario in {"missing_product_disclaimer", "missing_illustrative"}:
            _remove_text(payload, request.prompt.illustrative_product_disclaimer)
        elif self.scenario == "missing_disclaimer":
            payload["disclaimer"] = ""
        elif self.scenario in {"html", "html_script"}:
            claim["text"] = "<script>alert(1)</script>"
        elif self.scenario in {"extra_field", "modified_severity", "modified_matched_text"}:
            first["unexpected"] = True
        elif self.scenario == "duplicate_citation":
            assert isinstance(first_citation, dict)
            citations.append(deepcopy(first_citation))
        elif self.scenario in {"non_json", "missing_field"}:
            payload.pop(
                "executive_summary" if request.audience == "institution" else "overall_notice"
            )
        elif self.scenario == "no_citations" or self.scenario == "uncited_claim":
            claim["text"] = "该公司于2025年被监管罚款1000万元，已有100名消费者获赔。"
            claim["citations"] = []
        elif self.scenario == "trivial_quote":
            assert isinstance(first_citation, dict)
            first_citation["cited_quote"] = "监"
        elif self.scenario == "metadata_quote":
            assert isinstance(first_citation, dict)
            first_citation["cited_quote"] = str(
                request.context.findings[0].evidence[0].source_title
            )
        elif self.scenario == "metadata_pilot_id":
            assert isinstance(first_citation, dict)
            first_citation["cited_quote"] = str(request.context.findings[0].evidence[0].pilot_id)
        elif self.scenario == "metadata_url":
            assert isinstance(first_citation, dict)
            first_citation["cited_quote"] = request.context.findings[0].evidence[0].source_url
        elif self.scenario == "new_numeric_claim":
            claim["text"] = (
                "可能存在风险信号，需要进一步核验。该公司被罚款1000万元，产品实际收益率为8%。"
            )
        elif self.scenario == "unknown_claim_type":
            claim["claim_type"] = "model_free_form"
        elif self.scenario == "executive_uncited":
            summary_key = (
                "executive_summary" if request.audience == "institution" else "overall_notice"
            )
            summary = payload[summary_key]
            assert isinstance(summary, dict)
            summary.update(
                {
                    "claim_type": (
                        "deterministic_finding_explanation"
                        if request.audience == "institution"
                        else "plain_language_finding_explanation"
                    ),
                    "text": "该公司于2025年被监管罚款1000万元。",
                    "citations": [],
                }
            )
        elif self.scenario == "unicode_citation":
            assert isinstance(first_citation, dict)
            first_citation["citation_key"] = "E００１"
        elif self.scenario in {"forged_url", "forged_locator"}:
            assert isinstance(first_citation, dict)
            first_citation["source_url" if self.scenario == "forged_url" else "source_locator"] = (
                "https://evil.test" if self.scenario == "forged_url" else {"page": 999}
            )
        elif self.scenario == "database_id_citation":
            assert isinstance(first_citation, dict)
            first_citation["citation_key"] = "1"
        elif self.scenario == "oversized":
            claim["text"] = "甲" * 4001
        else:
            raise AssertionError(f"unsupported scenario: {self.scenario}")


def _other_citation(request: ExplanationProviderRequest, current_finding: object) -> str:
    for finding in request.context.findings:
        if finding.finding_key != current_finding and finding.evidence:
            return finding.evidence[0].citation_key
    return "E999"


def _remove_text(value: Any, needle: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str):
                value[key] = child.replace(needle, "")
            else:
                _remove_text(child, needle)
    elif isinstance(value, list):
        for child in value:
            _remove_text(child, needle)


def provider_configuration_sha256(provider: ExplanationProvider) -> str:
    return canonical_sha256(provider.safe_configuration)


def provider_from_name(name: str, scenario: str = "valid") -> ExplanationProvider:
    if name == "deterministic_fixture":
        return DeterministicFixtureProvider(scenario)
    if name == "external":
        return DisabledExternalProvider()
    raise ProviderGenerationError("explanation_provider_not_configured")
