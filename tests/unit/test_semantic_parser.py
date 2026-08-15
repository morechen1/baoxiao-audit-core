from __future__ import annotations

import json
from collections.abc import Iterable

import httpx

from app.core.config import Settings
from app.models import RiskFinding
from app.services.screening import (
    DeterministicComplianceRuleEngine,
    DeterministicScreeningService,
    OpenAICompatibleSemanticParserProvider,
    SemanticClaimParser,
    SemanticParserError,
    SemanticParserProviderResponse,
    load_ruleset,
    segment_marketing_text,
)
from app.services.screening.semantic_v2 import PARSER_CACHE, segment_semantic_text


class QueueProvider:
    def __init__(self, values: Iterable[str | SemanticParserError]) -> None:
        self.values = list(values)
        self.calls = 0

    def generate(self, raw_text: str) -> SemanticParserProviderResponse:
        del raw_text
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, SemanticParserError):
            raise value
        return SemanticParserProviderResponse(value)


def _claim(
    rule: str,
    quote: str,
    **overrides: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "candidate_rule": rule,
        "source_quote": quote,
        "actor": "salesperson",
        "audience": "consumer",
        "beneficiary": "consumer",
        "claim_type": "extra_benefit_or_gift",
        "semantic_features": ["extra_benefit", "gift"],
        "assertion": quote,
        "negated": False,
        "conditionality": "none",
        "educational_or_prohibitive_context": False,
        "speaker_role": "salesperson",
        "statement_mode": "DIRECT_MARKETING",
        "quoted_statement": False,
        "educational_context": False,
        "prohibitive_context": False,
        "historical_case_context": False,
        "internal_incentive_context": False,
        "conditional": False,
        "comparison_target": None,
        "guarantee_strength": "none",
        "risk_strength": "none",
        "time_scope": None,
        "role_ambiguity": False,
        "semantic_ambiguity": False,
        "confidence": 0.95,
        "reason": "原文直接表达相关营销主张。",
    }
    value.update(overrides)
    return value


def _payload(*claims: dict[str, object]) -> str:
    return json.dumps({"claims": list(claims)}, ensure_ascii=False)


def _run(
    raw_text: str,
    provider: QueueProvider,
    *,
    enabled: bool = True,
    deterministic: bool = False,
):
    ruleset = load_ruleset()
    segments = segment_marketing_text(raw_text, "a" * 64)
    deterministic_candidates = (
        DeterministicComplianceRuleEngine(ruleset).run(
            raw_text=raw_text,
            material_sha256="a" * 64,
            segments=segments,
        )
        if deterministic
        else []
    )
    parser = SemanticClaimParser(
        ruleset=ruleset,
        settings=Settings(semantic_parser_enabled=enabled),
        provider=provider,
    )
    return parser.supplement(
        raw_text=raw_text,
        material_sha256="a" * 64,
        segments=segments,
        deterministic=deterministic_candidates,
    )


def test_valid_consumer_extra_benefit_has_system_owned_exact_span() -> None:
    raw = "开场。购买本保险产品即可额外获赠一台手机。结束。"
    quote = "购买本保险产品即可额外获赠一台手机"
    outcome = _run(raw, QueueProvider([_payload(_claim("extra_contractual_benefit", quote))]))

    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert raw[candidate.raw_start_offset : candidate.raw_end_offset] == quote
    assert candidate.severity == load_ruleset().rules[2].severity
    assert outcome.diagnostics["hallucinated_quote_accepted"] == 0


def test_invalid_taxonomy_fails_closed_after_one_format_retry() -> None:
    raw = "普通文本"
    invalid = _payload(_claim("invented_risk", raw))
    provider = QueueProvider([invalid, invalid])
    outcome = _run(raw, provider)
    assert outcome.candidates == ()
    assert outcome.diagnostics["status"] == "failed"
    assert outcome.diagnostics["failure_code"] == "schema_invalid"
    assert outcome.diagnostics["retries"] == 1
    assert provider.calls == 2


def test_quote_not_found_is_rejected() -> None:
    outcome = _run(
        "购买保险可以获得手机。",
        QueueProvider([_payload(_claim("extra_contractual_benefit", "投保即可额外赠送手机"))]),
    )
    assert outcome.candidates == ()
    assert outcome.diagnostics["reject_reasons"] == {"quote_not_found": 1}


def test_negation_reuses_existing_local_context_gate() -> None:
    raw = "本产品不保证收益，实际结果以合同为准。"
    outcome = _run(
        raw,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "guaranteed_return_or_principal",
                        "保证收益",
                        claim_type="return_or_principal_guarantee",
                        semantic_features=["guarantee", "return_or_yield"],
                        guarantee_strength="explicit",
                    )
                )
            ]
        ),
    )
    assert outcome.candidates == ()
    assert outcome.diagnostics["reject_reasons"] == {"negation": 1}


def test_educational_or_prohibitive_context_is_rejected() -> None:
    quote = "不得宣传保证收益"
    outcome = _run(
        quote,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "guaranteed_return_or_principal",
                        quote,
                        claim_type="return_or_principal_guarantee",
                        educational_or_prohibitive_context=True,
                    )
                )
            ]
        ),
    )
    assert outcome.diagnostics["reject_reasons"] == {"educational_context": 1}


def test_role_ambiguity_is_rejected() -> None:
    quote = "季度末累计达到二十万，再送您一台手机"
    outcome = _run(
        quote,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "extra_contractual_benefit",
                        quote,
                        beneficiary="unknown",
                        role_ambiguity=True,
                    )
                )
            ]
        ),
    )
    assert outcome.diagnostics["reject_reasons"] == {"role_ambiguity": 1}


def test_salesperson_incentive_is_not_consumer_extra_benefit() -> None:
    quote = "销售人员季度业绩达到二十万元，公司奖励一台手机"
    outcome = _run(
        quote,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "extra_contractual_benefit",
                        quote,
                        actor="insurer",
                        audience="salesperson",
                        beneficiary="salesperson",
                    )
                )
            ]
        ),
    )
    assert outcome.candidates == ()
    assert outcome.diagnostics["reject_reasons"] == {"role_ambiguity": 1}


def test_internal_commission_context_fails_closed_when_model_assigns_consumer_role() -> None:
    raw = "本季度佣金达到30%，累计业绩达到20万，再送您一台手机"
    outcome = _run(
        raw,
        QueueProvider(
            [
                _payload(
                    _claim("extra_contractual_benefit", "再送您一台手机"),
                    _claim(
                        "false_promotion_or_prize",
                        "累计业绩达到20万，再送您一台手机",
                        claim_type="promotion_lottery_or_prize",
                        semantic_features=["promotion", "prize"],
                    ),
                )
            ]
        ),
    )
    assert outcome.candidates == ()
    assert outcome.diagnostics["reject_reasons"] == {"role_ambiguity": 2}


def test_deterministic_and_semantic_overlap_fuses_without_duplicate() -> None:
    raw = "本产品保证收益。"
    outcome = _run(
        raw,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "guaranteed_return_or_principal",
                        "保证收益",
                        claim_type="return_or_principal_guarantee",
                        semantic_features=["guarantee", "return_or_yield"],
                        guarantee_strength="explicit",
                    )
                )
            ]
        ),
        deterministic=True,
    )
    assert outcome.candidates == ()
    assert outcome.diagnostics["reject_reasons"] == {"duplicate": 1}
    assert outcome.diagnostics["deterministic_plus_semantic"] == 1


def test_parser_disabled_never_calls_provider() -> None:
    provider = QueueProvider([_payload()])
    outcome = _run("普通产品说明。", provider, enabled=False)
    assert outcome.candidates == ()
    assert outcome.diagnostics["status"] == "disabled"
    assert provider.calls == 0


def test_provider_unavailable_fails_closed_without_retry() -> None:
    provider = QueueProvider([SemanticParserError("provider_not_configured")])
    outcome = _run("保证收益", provider)
    assert outcome.candidates == ()
    assert outcome.diagnostics["status"] == "failed"
    assert outcome.diagnostics["retries"] == 0
    assert provider.calls == 1


def test_invalid_json_retries_once_then_fails_closed() -> None:
    provider = QueueProvider(["not-json", "still-not-json"])
    outcome = _run("保证收益", provider)
    assert outcome.candidates == ()
    assert outcome.diagnostics["failure_code"] == "schema_invalid"
    assert outcome.diagnostics["retries"] == 1
    assert provider.calls == 2


def test_confidence_threshold_is_fixed_and_fail_closed() -> None:
    quote = "购买本保险产品即可额外获赠一台手机"
    outcome = _run(
        quote,
        QueueProvider([_payload(_claim("extra_contractual_benefit", quote, confidence=0.71))]),
    )
    assert outcome.candidates == ()
    assert outcome.diagnostics["reject_reasons"] == {"confidence": 1}


def test_low_risk_empty_claims_produces_no_finding() -> None:
    outcome = _run("合同要点说明，保险利益以合同约定为准。", QueueProvider([_payload()]))
    assert outcome.candidates == ()
    assert outcome.diagnostics["semantic_supplements"] == 0


def test_existing_deterministic_behavior_survives_empty_semantic_output(session) -> None:
    parser = SemanticClaimParser(
        settings=Settings(semantic_parser_enabled=True),
        provider=QueueProvider([_payload()]),
    )
    run = DeterministicScreeningService(
        settings=Settings(semantic_parser_enabled=True),
        semantic_parser=parser,
    ).run(
        session,
        title="确定性保留",
        material_type="advertisement",
        raw_text="保证收益",
        source_label="unit_test",
    )
    assert run.finding_count == 1
    assert session.query(RiskFinding).one().rule_id == "guaranteed_return_or_principal"


def test_v2_context_guard_suppresses_deterministic_quoted_claim_after_parser_success(
    session,
) -> None:
    raw = "培训课件展示错误话术：“这款产品保证收益”。"
    parser = SemanticClaimParser(
        settings=Settings(semantic_parser_enabled=True),
        provider=QueueProvider([_payload()]),
    )
    run = DeterministicScreeningService(
        settings=Settings(semantic_parser_enabled=True),
        semantic_parser=parser,
    ).run(
        session,
        title="引用话术",
        material_type="advertisement",
        raw_text=raw,
        source_label="unit_test",
    )
    assert run.finding_count == 0
    assert session.query(RiskFinding).count() == 0
    diagnostics = run.evidence_evaluation_summary_json["semantic_parser"]
    assert diagnostics["deterministic_context_suppressions"] == {"educational_context": 1}


def test_v2_context_guard_keeps_deterministic_claim_when_parser_fails(session) -> None:
    raw = "培训课件展示错误话术：“这款产品保证收益”。"
    parser = SemanticClaimParser(
        settings=Settings(
            semantic_parser_enabled=True,
            semantic_parser_cache_enabled=False,
        ),
        provider=QueueProvider([SemanticParserError("provider_not_configured")]),
    )
    run = DeterministicScreeningService(
        settings=Settings(
            semantic_parser_enabled=True,
            semantic_parser_cache_enabled=False,
        ),
        semantic_parser=parser,
    ).run(
        session,
        title="失败保底",
        material_type="advertisement",
        raw_text=raw,
        source_label="unit_test",
    )
    assert run.finding_count == 1
    assert session.query(RiskFinding).one().rule_id == "guaranteed_return_or_principal"


def test_openai_compatible_payload_contains_no_rag_or_client_secret() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": _payload()}}]})

    settings = Settings(
        llm_enabled=True,
        llm_provider="openai_compatible",
        llm_base_url="https://api.example.test/v1",
        llm_api_key="test-secret",
        llm_model="deepseek-v4-flash",
    )
    provider = OpenAICompatibleSemanticParserProvider(
        settings,
        transport=httpx.MockTransport(handler),
    )
    response = provider.generate("原始营销文本")
    assert response.raw_json == _payload()
    assert captured["thinking"] == {"type": "disabled"}
    serialized = json.dumps(captured, ensure_ascii=False)
    assert "original_marketing_text" in serialized
    assert "output_schema" in serialized
    assert "test-secret" not in serialized
    messages = captured["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    user_payload = json.loads(user_message["content"])
    assert set(user_payload) == {"original_marketing_text", "taxonomy", "output_schema"}
    claim_schema = user_payload["output_schema"]["$defs"]["SemanticClaim"]
    assert "statement_mode" in claim_schema["required"]
    assert "speaker_role" in claim_schema["required"]


def test_context_guard_is_scoped_to_current_clause() -> None:
    raw = "监管部门明确禁止宣称保证收益。本产品现在承诺保证收益。"
    quote = "本产品现在承诺保证收益"
    outcome = _run(
        raw,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "guaranteed_return_or_principal",
                        quote,
                        claim_type="return_or_principal_guarantee",
                        guarantee_strength="explicit",
                    )
                )
            ]
        ),
    )
    assert [item.rule_id for item in outcome.candidates] == ["guaranteed_return_or_principal"]


def test_v2_statement_modes_reject_quoted_historical_and_internal_contexts() -> None:
    cases = (
        ("培训材料列举“保证收益”属于错误话术。", "保证收益", "QUOTED_MARKETING", "quoted_context"),
        (
            "历史案例中销售人员曾宣称保证收益。",
            "保证收益",
            "HISTORICAL_CASE_DESCRIPTION",
            "historical_context",
        ),
        (
            "销售人员季度达标，公司奖励手机。",
            "公司奖励手机",
            "INTERNAL_EMPLOYEE_INCENTIVE",
            "internal_incentive_context",
        ),
    )
    for raw, quote, mode, reason in cases:
        rule = (
            "extra_contractual_benefit"
            if mode == "INTERNAL_EMPLOYEE_INCENTIVE"
            else "guaranteed_return_or_principal"
        )
        outcome = _run(
            raw,
            QueueProvider(
                [
                    _payload(
                        _claim(
                            rule,
                            quote,
                            statement_mode=mode,
                            claim_type=(
                                "extra_benefit_or_gift"
                                if rule == "extra_contractual_benefit"
                                else "return_or_principal_guarantee"
                            ),
                        )
                    )
                ]
            ),
        )
        assert outcome.candidates == ()
        assert outcome.diagnostics["reject_reasons"] == {reason: 1}


def test_v2_semantic_contract_rejects_unknown_mode_and_weak_risk_strength() -> None:
    unknown = _run(
        "这款产品不会亏损",
        QueueProvider(
            [
                _payload(
                    _claim(
                        "no_risk_or_no_loss",
                        "不会亏损",
                        claim_type="risk_or_loss",
                        statement_mode="UNKNOWN",
                        risk_strength="explicit",
                    )
                )
            ]
        ),
    )
    weak = _run(
        "这款产品不会亏损",
        QueueProvider(
            [
                _payload(
                    _claim(
                        "no_risk_or_no_loss",
                        "不会亏损",
                        claim_type="risk_or_loss",
                        risk_strength="weak",
                    )
                )
            ]
        ),
    )
    assert unknown.diagnostics["reject_reasons"] == {"semantic_ambiguity": 1}
    assert weak.diagnostics["reject_reasons"] == {"semantic_ambiguity": 1}


def test_comparison_contract_uses_explicit_target_as_authoritative_structure() -> None:
    quote = "这款产品的保障水平稳居行业第一"
    outcome = _run(
        quote,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "improper_comparison_or_ranking",
                        quote,
                        claim_type="comparison_or_ranking",
                        semantic_features=[],
                        comparison_target="行业同类产品",
                    )
                )
            ]
        ),
    )
    assert [item.rule_id for item in outcome.candidates] == [
        "improper_comparison_or_ranking"
    ]


def test_long_text_chunks_keep_document_offsets_and_fuse_overlap() -> None:
    quote = "购买本保险即可额外获赠手机"
    raw = "甲" * 440 + quote + "乙" * 360

    class ContextProvider:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, raw_text: str) -> SemanticParserProviderResponse:
            self.calls += 1
            claims = [_claim("extra_contractual_benefit", quote)] if quote in raw_text else []
            return SemanticParserProviderResponse(_payload(*claims))

    provider = ContextProvider()
    ruleset = load_ruleset()
    parser = SemanticClaimParser(
        ruleset=ruleset,
        settings=Settings(
            semantic_parser_enabled=True,
            semantic_parser_max_chunk_chars=500,
            semantic_parser_chunk_overlap=200,
            max_semantic_chunks_per_document=4,
        ),
        provider=provider,
    )
    outcome = parser.supplement(
        raw_text=raw,
        material_sha256="a" * 64,
        segments=segment_marketing_text(raw, "a" * 64),
        deterministic=[],
    )
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert raw[candidate.raw_start_offset : candidate.raw_end_offset] == quote
    assert outcome.diagnostics["semantic_chunks"] >= 2
    assert provider.calls >= 2


def test_taxonomy_arbitration_reduces_semantic_over_labeling() -> None:
    raw = "本产品保证固定收益"
    outcome = _run(
        raw,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "misleading_interest_or_yield",
                        "保证固定收益",
                        claim_type="interest_or_yield",
                    ),
                    _claim(
                        "guaranteed_return_or_principal",
                        "保证固定收益",
                        claim_type="return_or_principal_guarantee",
                        guarantee_strength="explicit",
                    ),
                )
            ]
        ),
    )
    assert [item.rule_id for item in outcome.candidates] == ["guaranteed_return_or_principal"]
    assert outcome.diagnostics["reject_reasons"] == {"taxonomy_arbitration": 1}


def test_specialized_taxonomy_prevents_generic_financial_over_labeling() -> None:
    quote = "购买本保单等同于把钱存进普通储蓄账户"
    outcome = _run(
        quote,
        QueueProvider(
            [
                _payload(
                    _claim(
                        "guaranteed_return_or_principal",
                        quote,
                        claim_type="return_or_principal_guarantee",
                        semantic_features=["guarantee", "principal_preservation"],
                        guarantee_strength="explicit",
                    ),
                    _claim(
                        "product_nature_confusion",
                        quote,
                        claim_type="product_nature",
                        semantic_features=["product_nature"],
                    ),
                )
            ]
        ),
    )
    assert [item.rule_id for item in outcome.candidates] == ["product_nature_confusion"]
    assert outcome.diagnostics["reject_reasons"] == {"taxonomy_arbitration": 1}


def test_parser_cache_binds_text_model_and_schema() -> None:
    PARSER_CACHE.clear()
    quote = "购买本保险即可额外获赠手机"
    provider = QueueProvider([_payload(_claim("extra_contractual_benefit", quote))])
    settings = Settings(
        semantic_parser_enabled=True,
        semantic_parser_cache_enabled=True,
        llm_model="cache-test-model",
    )
    parser = SemanticClaimParser(settings=settings, provider=provider)
    segments = segment_marketing_text(quote, "a" * 64)
    first = parser.supplement(
        raw_text=quote,
        material_sha256="a" * 64,
        segments=segments,
        deterministic=[],
    )
    second = parser.supplement(
        raw_text=quote,
        material_sha256="a" * 64,
        segments=segments,
        deterministic=[],
    )
    assert len(first.candidates) == len(second.candidates) == 1
    assert provider.calls == 1
    assert second.diagnostics["cache_hits"] == 1


def test_semantic_chunk_budget_reports_partial_coverage() -> None:
    coverage = segment_semantic_text(
        "段落。" * 1000,
        max_chars=500,
        overlap=50,
        max_chunks=2,
        max_text_length=10_000,
    )
    assert len(coverage.chunks) == 2
    assert coverage.partial is True
    for chunk in coverage.chunks:
        assert (
            chunk.text == ("段落。" * 1000)[chunk.document_offset_start : chunk.document_offset_end]
        )
