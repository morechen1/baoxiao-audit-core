from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import get_db
from app.core.exceptions import ScreeningError
from app.main import app
from app.models import (
    FindingEvidenceLink,
    KnowledgeChunk,
    MarketingMaterial,
    MaterialSegment,
    RiskFinding,
    ScreeningRun,
    SourceDocument,
)
from app.services.knowledge import KnowledgeIndexService, SearchResult
from app.services.screening import (
    DeterministicComplianceRuleEngine,
    DeterministicScreeningService,
    MarketingRuleSet,
    load_ruleset,
    normalize_marketing_text,
    segment_marketing_text,
)
from app.services.screening.evidence import (
    DeterministicEvidenceSupportEvaluator,
    EvidenceSupportDecision,
    FindingEvidenceAssembler,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "constructed_screening_eval_v1" / "samples.json"
FORMAL_EVIDENCE_ORACLE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "constructed_screening_eval_v1"
    / "formal_evidence_expectations_v1.json"
)


def _sample_text(sample: dict[str, object]) -> str:
    if "text" in sample:
        return str(sample["text"])
    builder = sample["text_builder"]
    assert isinstance(builder, dict)
    return str(builder["prefix"]) * int(builder["repeat"]) + str(builder["suffix"])


def _engine(text: str):
    material_sha = "1" * 64
    segments = segment_marketing_text(text, material_sha)
    return DeterministicComplianceRuleEngine(load_ruleset()).run(
        raw_text=text,
        material_sha256=material_sha,
        segments=segments,
    )


def test_normalization_nfkc_unicode_whitespace_and_raw_offset() -> None:
    raw = "Ａ保证\n  收益％"
    normalized = normalize_marketing_text(raw)
    assert normalized.text == "a保证 收益%"
    start = normalized.text.index("保证")
    end = len(normalized.text) - 1
    raw_start, raw_end = normalized.raw_span(start, end)
    assert raw[raw_start:raw_end] == "保证\n  收益"


def test_normalization_nfkc_combining_cluster_preserves_following_raw_offset() -> None:
    raw = "e\u0301保证收益"
    normalized = normalize_marketing_text(raw)
    assert normalized.text == "é保证收益"
    assert normalized.raw_span(0, 1) == (0, 2)
    finding = _engine(raw)[0]
    assert finding.matched_text == "保证收益"
    assert (finding.raw_start_offset, finding.raw_end_offset) == (2, 6)


def test_segmentation_and_sha_are_deterministic() -> None:
    raw = "短标题\n\n保证收益。第二段没有风险提示；第三段。"
    first = segment_marketing_text(raw, "2" * 64)
    second = segment_marketing_text(raw, "2" * 64)
    assert first == second
    assert first[0].text.startswith("短标题")
    assert all(raw[item.raw_start_offset : item.raw_end_offset] == item.text for item in first)


def test_ruleset_schema_sha_and_order_are_strict() -> None:
    ruleset = load_ruleset()
    assert len(ruleset.rules) == 12
    assert len(ruleset.sha256) == 64
    assert [rule.rule_id for rule in ruleset.rules] == sorted(
        rule.rule_id for rule in ruleset.rules
    )
    payload = ruleset.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        MarketingRuleSet.model_validate(payload)


@pytest.mark.parametrize(
    ("text", "rule_id"),
    [
        ("保证 收益", "guaranteed_return_or_principal"),
        ("零风险", "no_risk_or_no_loss"),
        ("结算利率固定", "misleading_interest_or_yield"),
        ("监管\n推荐", "regulatory_endorsement"),
        ("立即投保即可中奖", "false_promotion_or_prize"),
        ("购买保险赠送现金", "extra_contractual_benefit"),
        ("保险就是存款", "product_nature_confusion"),
        ("收益最高", "absolute_or_superlative_claim"),
        ("排名第一", "improper_comparison_or_ranking"),
        ("随时退保没有损失", "surrender_or_cash_value_misstatement"),
        ("没有等待期", "waiting_or_cooling_period_misstatement"),
        ("什么情况都赔", "concealment_or_minimization_of_exclusions"),
    ],
)
def test_all_twelve_rule_categories_match(text: str, rule_id: str) -> None:
    assert rule_id in {finding.rule_id for finding in _engine(text)}


@pytest.mark.parametrize(
    "text",
    [
        "本产品不保证收益，实际结果以合同为准。",
        "请保证材料真实性并签字。",
        "普通商场优惠，不含投保或保险购买。",
        "监管备案不等于监管保证。",
    ],
)
def test_exception_patterns_fail_closed_without_false_positive(text: str) -> None:
    assert _engine(text) == []


@pytest.mark.parametrize(
    ("text", "expected_rule", "expected_text"),
    [
        ("本产品不是银行理财，而是保险产品。", None, None),
        ("没有任何免责的说法不准确。", None, None),
        ("保险产品介绍：保障责任详见条款，今天商场中奖名单如下。", None, None),
        ("购买日用品参加中奖活动，本页同时介绍保险知识。", None, None),
        ("监管部门不担保收益，但销售话术称监管推荐。", "regulatory_endorsement", "监管推荐"),
        (
            "本产品保证收益，另一个账户为非保证收益。",
            "guaranteed_return_or_principal",
            "保证收益",
        ),
        (
            "退保一定退全款，合同其他部分写明以现金价值为准。",
            "surrender_or_cash_value_misstatement",
            "退保一定退全款",
        ),
        (
            "限时优惠购买保险，另有说明该超市活动为非保险活动。",
            "false_promotion_or_prize",
            "限时优惠",
        ),
    ],
)
def test_local_context_adversarial_cases(
    text: str, expected_rule: str | None, expected_text: str | None
) -> None:
    findings = _engine(text)
    if expected_rule is None:
        assert findings == []
    else:
        finding = next(value for value in findings if value.rule_id == expected_rule)
        assert finding.matched_text == expected_text


def test_exception_does_not_cross_strong_newline_boundary() -> None:
    text = "保证收益\n本产品不保证收益"
    findings = [
        value for value in _engine(text) if value.rule_id == "guaranteed_return_or_principal"
    ]
    assert [
        (value.raw_start_offset, value.raw_end_offset, value.matched_text) for value in findings
    ] == [(0, 4, "保证收益")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("保险\n中奖", False),
        ("保险。\n中奖", False),
        ("购买保险即可中奖", True),
        ("监管\n推荐", True),
    ],
)
def test_required_context_uses_strong_clause_boundaries(text: str, expected: bool) -> None:
    findings = _engine(text)
    actual = any(
        value.rule_id in {"false_promotion_or_prize", "regulatory_endorsement"}
        for value in findings
    )
    assert actual is expected


@pytest.mark.parametrize(
    "text",
    [
        "银行理财并非保险产品。",
        "银行理财不同于保险。",
        "请勿将保险与银行理财混淆。",
        "监管推荐的说法不准确。",
        "保证收益的说法不准确。",
        "零风险的说法不准确。",
        "收益最高的说法没有依据。",
        "排名第一的说法不准确。",
        "随时退保没有损失的说法不准确。",
        "没有等待期的说法不准确。",
        "禁止限时优惠诱导投保。",
    ],
)
def test_generic_local_negation_and_metalanguage_suppress_findings(text: str) -> None:
    assert _engine(text) == []


@pytest.mark.parametrize(
    ("text", "rule_id"),
    [
        ("监管部门不担保收益，但销售人员仍称监管推荐。", "regulatory_endorsement"),
        ("条款明确为非保证利益，但营销人员承诺保证收益。", "guaranteed_return_or_principal"),
    ],
)
def test_negation_does_not_cross_adversative_or_speaker_boundary(text: str, rule_id: str) -> None:
    assert rule_id in {finding.rule_id for finding in _engine(text)}


@pytest.mark.parametrize("prefix_length", [1197, 1198, 1199])
def test_long_segment_overlap_finds_guarantee_once_with_exact_span(prefix_length: int) -> None:
    text = "甲" * prefix_length + "保证收益"
    findings = [
        value for value in _engine(text) if value.rule_id == "guaranteed_return_or_principal"
    ]
    assert len(findings) == 1
    assert findings[0].matched_text == "保证收益"
    assert (findings[0].raw_start_offset, findings[0].raw_end_offset) == (
        prefix_length,
        prefix_length + 4,
    )


@pytest.mark.parametrize(
    "token",
    ["随时退保没有损失", "结算利率固定5%", "金保监罚决字〔2026〕123号", "罚款100000元"],
)
def test_overlapping_segments_preserve_boundary_tokens(token: str) -> None:
    text = "甲" * 1199 + token
    segments = segment_marketing_text(text, "f" * 64)
    containing = [segment for segment in segments if token in segment.text]
    assert containing
    segment = containing[0]
    relative = segment.text.index(token)
    assert segment.raw_start_offset + relative == 1199


def test_cross_line_match_returns_exact_raw_span() -> None:
    text = "开场：监管\n推荐本产品。"
    finding = next(value for value in _engine(text) if value.rule_id == "regulatory_endorsement")
    assert text[finding.raw_start_offset : finding.raw_end_offset] == "监管\n推荐"
    assert finding.matched_text == "监管\n推荐"


def test_overlap_merge_and_finding_sha_are_stable() -> None:
    text = "保本保息，保证收益。"
    first = _engine(text)
    second = _engine(text)
    assert [value.finding_sha256 for value in first] == [value.finding_sha256 for value in second]
    assert len({value.finding_sha256 for value in first}) == len(first)


def test_constructed_fixture_exact_rules_and_spans() -> None:
    samples = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(samples) == 60
    assert sum(bool(sample["expected_rule_ids"]) for sample in samples) == 31
    for sample in samples:
        findings = _engine(_sample_text(sample))
        actual_rules = sorted({finding.rule_id for finding in findings})
        assert actual_rules == sorted(sample["expected_rule_ids"]), sample["sample_id"]
        actual_spans = [
            {
                "rule_id": finding.rule_id,
                "start": finding.raw_start_offset,
                "end": finding.raw_end_offset,
                "text": finding.matched_text,
            }
            for finding in findings
        ]
        assert actual_spans == sample["expected_spans"], sample["sample_id"]
        assert not set(sample["forbidden_rule_ids"]) & set(actual_rules)


def test_empty_index_screening_is_atomic_and_evidence_insufficient(session) -> None:
    service = DeterministicScreeningService()
    run = service.run(
        session,
        title="测试营销话术",
        material_type="sales_script",
        raw_text="保证收益，零风险。",
        source_label="unit_test",
        is_constructed_evaluation=True,
    )
    assert run.status == "completed"
    assert run.finding_count == 2
    assert run.insufficient_evidence_count == 2
    assert session.query(SourceDocument).count() == 0
    assert session.query(KnowledgeChunk).count() == 0
    assert session.query(FindingEvidenceLink).count() == 0
    assert session.query(RiskFinding).count() == 2


def test_rerun_same_input_has_identical_findings_and_does_not_duplicate_material(session) -> None:
    service = DeterministicScreeningService()
    kwargs = {
        "title": "重复筛查",
        "material_type": "advertisement",
        "raw_text": "监管推荐，保证收益。",
        "source_label": "unit_test",
    }
    first = service.run(session, **kwargs)
    second = service.run(session, **kwargs)
    first_hashes = [value.finding_sha256 for value in first.findings]
    second_hashes = [value.finding_sha256 for value in second.findings]
    assert first_hashes == second_hashes
    assert first.run_payload_sha256 == second.run_payload_sha256
    assert session.query(MarketingMaterial).count() == 1
    assert session.query(ScreeningRun).count() == 2


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"raw_text": ""}, "screening_material_empty"),
        ({"raw_text": "x" * 100_001}, "screening_material_too_large"),
        ({"material_type": "pdf"}, "screening_material_type_invalid"),
    ],
)
def test_input_boundaries_reject_without_partial_rows(session, kwargs, code: str) -> None:
    values = {
        "title": "边界测试",
        "material_type": "other",
        "raw_text": "正常文本",
        "source_label": "unit_test",
    }
    values.update(kwargs)
    with pytest.raises(Exception, match=code):
        DeterministicScreeningService().run(session, **values)
    assert session.query(MarketingMaterial).count() == 0
    assert session.query(MaterialSegment).count() == 0
    assert session.query(ScreeningRun).count() == 0


def test_invalid_trusted_index_fails_before_any_persistence(session, monkeypatch) -> None:
    monkeypatch.setattr(
        KnowledgeIndexService,
        "verify_chunks",
        lambda self, value: SimpleNamespace(valid=False),
    )
    with pytest.raises(ScreeningError, match="screening_trusted_index_invalid"):
        DeterministicScreeningService().run(
            session,
            title="可信索引失败",
            material_type="sales_script",
            raw_text="保证收益",
            source_label="unit_test",
        )
    assert session.query(MarketingMaterial).count() == 0
    assert session.query(ScreeningRun).count() == 0


def test_evidence_link_failure_rolls_back_material_run_and_findings(session, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise ScreeningError("screening_evidence_retrieval_failed")

    monkeypatch.setattr(FindingEvidenceAssembler, "assemble", fail)
    with pytest.raises(ScreeningError, match="screening_evidence_retrieval_failed"):
        DeterministicScreeningService().run(
            session,
            title="证据失败",
            material_type="sales_script",
            raw_text="保证收益",
            source_label="unit_test",
        )
    assert session.query(MarketingMaterial).count() == 1
    assert session.query(MaterialSegment).count() == 1
    run = session.query(ScreeningRun).one()
    assert run.status == "failed"
    assert run.error_code == "screening_evidence_retrieval_failed"
    assert run.completed_at is not None
    assert run.finding_count == 0
    assert session.query(RiskFinding).count() == 0
    assert session.query(FindingEvidenceLink).count() == 0


def test_internal_execution_failure_persists_only_public_failed_run_code(
    session, monkeypatch
) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("database secret detail")

    monkeypatch.setattr(FindingEvidenceAssembler, "assemble", fail)
    with pytest.raises(ScreeningError, match="screening_persistence_failed"):
        DeterministicScreeningService().run(
            session,
            title="内部失败",
            material_type="sales_script",
            raw_text="保证收益",
            source_label="unit_test",
        )
    run = session.query(ScreeningRun).one()
    assert run.status == "failed"
    assert run.error_code == "screening_persistence_failed"
    assert "secret" not in run.error_code
    assert session.query(RiskFinding).count() == 0
    assert session.query(FindingEvidenceLink).count() == 0


def test_evidence_selection_keeps_required_types_deduplicated_and_bounded() -> None:
    def result(identity: str, record_type: str, document_id: int, score: float) -> SearchResult:
        return SearchResult(
            score=score,
            rank=1,
            record_type=record_type,
            pilot_id="TEST",
            source_document_id=document_id,
            structured_record_id=1,
            portable_record_key="a" * 64,
            chunk_kind="test",
            chunk_ordinal=0,
            title="测试可信证据",
            snippet="测试",
            matched_terms=["测试"],
            authority="测试机关",
            relevant_date=None,
            evidence_quality="A",
            authenticity_status="verified_public",
            review_status="approved",
            source_locator={"source_url": "https://example.test"},
            evidence_references=[{"quote": "测试"}],
            source_url="https://example.test",
            chunk_content_sha256=identity,
            chunk_identity_sha256=identity,
        )

    regulation = result("1" * 64, "regulation", 1, 10.0)
    second_regulation = result("2" * 64, "regulation", 3, 9.0)
    third_same_document = result("3" * 64, "regulation", 1, 8.0)
    product = result("4" * 64, "product_document", 2, 1.0)

    def decision(support_type: str, value: SearchResult) -> EvidenceSupportDecision:
        return EvidenceSupportDecision(
            support_type=support_type,
            result=value,
            passed=True,
            matched_support_patterns=("测试",),
            actual_matched_substrings=("测试",),
            matched_pattern_groups=(),
            matched_evidence_fields=("article_text",),
            support_reason="semantic_evidence_match",
            semantic_support_score=1.0,
            semantic_support_reason="all_declared_semantic_patterns_matched",
            context_scope="not_applicable",
        )

    selected = FindingEvidenceAssembler._select(
        [
            decision("normative_basis", regulation),
            decision("normative_basis", regulation),
            decision("normative_basis", second_regulation),
            decision("normative_basis", third_same_document),
            decision("product_term_context", product),
        ],
        ("normative_basis", "product_term_context"),
    )
    assert {value.support_type for value in selected} == {
        "normative_basis",
        "product_term_context",
    }
    assert len({value.result.chunk_identity_sha256 for value in selected}) == len(selected)
    assert len(selected) == 3
    assert sum(value.result.source_document_id == 1 for value in selected) == 1
    assert sum(value.support_type == "normative_basis" for value in selected) == 2


def test_ruleset_cannot_name_regulatory_case_as_preferred_evidence() -> None:
    payload = load_ruleset().model_dump(mode="json")
    payload["rules"][0]["preferred_record_types"].append("regulatory_case")
    with pytest.raises(ValidationError):
        MarketingRuleSet.model_validate(payload)


def test_formal_evidence_oracle_covers_all_rules_and_named_counterexamples() -> None:
    payload = json.loads(FORMAL_EVIDENCE_ORACLE.read_text(encoding="utf-8"))
    assert payload["formal_evidence_oracle_version"] == "formal_evidence_expectations_v1"
    expectations = {
        (rule["rule_id"], item["support_type"]): item
        for rule in payload["rules"]
        for item in rule["expectations"]
    }
    expected_keys = {
        (rule.rule_id, support_type)
        for rule in load_ruleset().rules
        for support_type in rule.evidence_requirements
    }
    assert set(expectations) == expected_keys
    assert (
        "5597f2f9eecccf988d346961600335b92e14272490620cd946af183c3527e04d"
        in expectations[("improper_comparison_or_ranking", "normative_basis")][
            "forbidden_chunk_identities"
        ]
    )
    concealment_normative = expectations[
        ("concealment_or_minimization_of_exclusions", "normative_basis")
    ]
    assert (
        "71374120ef8921bbf809b107edf0c43508c5eed7c1b7474e7193eb90df9cd6b5"
        in concealment_normative["forbidden_chunk_identities"]
    )
    concealment_product = expectations[
        ("concealment_or_minimization_of_exclusions", "product_term_context")
    ]
    assert (
        "a600ecf3be85b1912a2b65e10419a238094a0a7b773f0b4c3237311d84e50c9e"
        in concealment_product["forbidden_chunk_identities"]
    )
    assert {
        "9ab7fb9d4cdeaa5854d2ad10e630a38939aeba2a165816ab9526c127eee604fc",
        "f38161f6d9b11d370cb954e26601e4ad686461e8fdb989d8275010285ae3faf2",
    }.issubset(set(concealment_product["allowed_chunk_identities"]))


def _semantic_result(
    *,
    record_type: str,
    chunk_kind: str,
    evidence_references: list[dict[str, object]],
    identity: str = "9" * 64,
) -> SearchResult:
    return SearchResult(
        score=9.0,
        rank=1,
        record_type=record_type,
        pilot_id="PEN-TEST",
        source_document_id=1,
        structured_record_id=1,
        portable_record_key="a" * 64,
        chunk_kind=chunk_kind,
        chunk_ordinal=0,
        title="测试可信证据",
        snippet="只用于召回，不用于语义准入",
        matched_terms=["保险"],
        authority="测试机关",
        relevant_date=None,
        evidence_quality="A",
        authenticity_status="verified_public",
        review_status="approved",
        source_locator={"source_url": "https://example.test"},
        evidence_references=evidence_references,
        source_url="https://example.test",
        chunk_content_sha256=identity,
        chunk_identity_sha256=identity,
    )


def test_evidence_support_requires_rule_semantics_not_record_type() -> None:
    rule = next(
        value for value in load_ruleset().rules if value.rule_id == "guaranteed_return_or_principal"
    )
    unrelated = _semantic_result(
        record_type="penalty",
        chunk_kind="penalty_entry",
        evidence_references=[
            {
                "field_name": "illegal_facts",
                "quote": "给予合同约定以外利益并委托无资格机构销售",
            }
        ],
    )
    relevant = _semantic_result(
        record_type="penalty",
        chunk_kind="penalty_entry",
        evidence_references=[{"field_name": "illegal_facts", "quote": "销售中承诺保证收益"}],
        identity="8" * 64,
    )
    rejected = DeterministicEvidenceSupportEvaluator.evaluate(
        rule, "enforcement_example", unrelated
    )
    accepted = DeterministicEvidenceSupportEvaluator.evaluate(rule, "enforcement_example", relevant)
    assert rejected.passed is False
    assert rejected.support_reason == "semantic_pattern_not_matched"
    assert accepted.passed is True
    assert accepted.matched_evidence_fields == ("illegal_facts",)


def test_basic_information_cannot_satisfy_normative_basis() -> None:
    rule = next(value for value in load_ruleset().rules if value.rule_id == "no_risk_or_no_loss")
    result = _semantic_result(
        record_type="regulation",
        chunk_kind="basic_information",
        evidence_references=[{"field_name": "title", "quote": "保险销售风险管理办法"}],
    )
    decision = DeterministicEvidenceSupportEvaluator.evaluate(rule, "normative_basis", result)
    assert decision.passed is False
    assert decision.support_reason == "chunk_kind_not_allowed"


@pytest.mark.parametrize(
    ("rule_id", "quote", "expected", "expected_groups"),
    [
        (
            "regulatory_endorsement",
            "金融监管总局规定的其他提示内容",
            False,
            0,
        ),
        (
            "regulatory_endorsement",
            "不得利用监管机构审核或备案程序提供保证等引人误解的表述",
            True,
            2,
        ),
        ("extra_contractual_benefit", "提示保单利益具有不确定性", False, 0),
        ("extra_contractual_benefit", "不得给予合同约定以外的利益或返佣", True, 1),
        ("product_nature_confusion", "规范保险产品销售行为", False, 1),
        ("product_nature_confusion", "规范保险产品销售行为；商标不得引起混淆", False, 1),
        ("product_nature_confusion", "不得将保险产品与理财产品混淆", True, 2),
        ("false_promotion_or_prize", "不得以其他方式诱导消费者", False, 0),
        ("false_promotion_or_prize", "不得进行虚假促销诱导订立保险合同", True, 2),
        ("improper_comparison_or_ranking", "保险产品宣传名称", False, 0),
        ("improper_comparison_or_ranking", "不得通过不当评比和排序进行宣传", True, 2),
        ("concealment_or_minimization_of_exclusions", "提供理赔保全服务渠道", False, 0),
        (
            "concealment_or_minimization_of_exclusions",
            "应提示和说明责任免除及理赔条件",
            True,
            2,
        ),
        ("no_risk_or_no_loss", "提示投保人履行如实告知义务", False, 1),
        ("no_risk_or_no_loss", "应提示风险和可能损失", True, 2),
        ("surrender_or_cash_value_misstatement", "可能发生损失", False, 0),
        ("surrender_or_cash_value_misstatement", "提示退保损失和现金价值", True, 1),
        ("misleading_interest_or_yield", "说明产品收益情况", False, 1),
        ("misleading_interest_or_yield", "保单利益具有不确定性", True, 2),
        ("guaranteed_return_or_principal", "说明产品收益情况", False, 1),
        ("guaranteed_return_or_principal", "不得承诺保证收益", True, 2),
    ],
)
def test_normative_evidence_requires_all_strict_pattern_groups(
    rule_id: str,
    quote: str,
    expected: bool,
    expected_groups: int,
) -> None:
    rule = next(value for value in load_ruleset().rules if value.rule_id == rule_id)
    result = _semantic_result(
        record_type="regulation",
        chunk_kind="article_text",
        evidence_references=[{"field_name": "article_text", "quote": quote}],
    )
    decision = DeterministicEvidenceSupportEvaluator.evaluate(rule, "normative_basis", result)
    assert decision.passed is expected
    assert sum(bool(group["matched_patterns"]) for group in decision.matched_pattern_groups) == (
        expected_groups
    )
    if expected:
        assert decision.semantic_support_score == 1.0
        assert decision.actual_matched_substrings
    else:
        assert decision.semantic_support_score < 1.0


@pytest.mark.parametrize(
    ("quote", "field_name", "expected"),
    [
        ("保险责任包括重大疾病保险金", "insurance_responsibility", False),
        ("责任免除：发生下列情形我们不承担保险责任", "exclusions", True),
    ],
)
def test_exclusion_product_context_requires_actual_exclusion_language(
    quote: str,
    field_name: str,
    expected: bool,
) -> None:
    rule = next(
        value
        for value in load_ruleset().rules
        if value.rule_id == "concealment_or_minimization_of_exclusions"
    )
    result = _semantic_result(
        record_type="product_document",
        chunk_kind="terms_and_risks" if expected else "coverage",
        evidence_references=[{"field_name": field_name, "quote": quote}],
    )
    decision = DeterministicEvidenceSupportEvaluator.evaluate(rule, "product_term_context", result)
    assert decision.passed is expected


def test_missing_required_support_type_yields_partial_status(monkeypatch) -> None:
    rule = next(
        value for value in load_ruleset().rules if value.rule_id == "guaranteed_return_or_principal"
    )
    normative = _semantic_result(
        record_type="regulation",
        chunk_kind="article_text",
        evidence_references=[{"field_name": "article_text", "quote": "保单利益具有不确定性"}],
    )
    unrelated_penalty = _semantic_result(
        record_type="penalty",
        chunk_kind="penalty_entry",
        evidence_references=[{"field_name": "illegal_facts", "quote": "未取得销售资格"}],
        identity="7" * 64,
    )
    monkeypatch.setattr(
        FindingEvidenceAssembler,
        "_search_candidates",
        lambda *args: [
            ("normative_basis", normative),
            ("enforcement_example", unrelated_penalty),
        ],
    )
    monkeypatch.setattr(
        FindingEvidenceAssembler,
        "_to_link",
        lambda *args: SimpleNamespace(
            support_type="normative_basis", support_evaluation_passed=True
        ),
    )
    finding = SimpleNamespace(evidence_status="evidence_insufficient")
    result = FindingEvidenceAssembler().assemble(None, finding, rule)  # type: ignore[arg-type]
    assert finding.evidence_status == "partially_supported"
    assert result.candidates_rejected == 1
    assert len(result.links) == 1


def test_institution_and_consumer_reports_are_stable_and_read_only(session) -> None:
    service = DeterministicScreeningService()
    run = service.run(
        session,
        title="报告测试",
        material_type="sales_script",
        raw_text="随时退保没有损失。",
        source_label="unit_test",
    )
    before = (session.query(RiskFinding).count(), session.query(FindingEvidenceLink).count())
    institution = service.institution_report(session, run.id)
    consumer = service.consumer_notice(session, run.id)
    assert institution == service.institution_report(session, run.id)
    assert consumer == service.consumer_notice(session, run.id)
    assert institution["manual_review_required"] is True
    assert "不构成违法认定" in institution["disclaimer"]
    assert (
        session.query(RiskFinding).count(),
        session.query(FindingEvidenceLink).count(),
    ) == before


def test_historical_reports_use_persisted_rule_snapshot_after_change_or_deletion(session) -> None:
    original = DeterministicScreeningService()
    run = original.run(
        session,
        title="历史快照",
        material_type="advertisement",
        raw_text="本产品收益最高",
        source_label="unit_test",
    )
    institution = original.institution_report(session, run.id)
    consumer = original.consumer_notice(session, run.id)

    changed_payload = load_ruleset().model_dump(mode="json")
    changed_payload["rules"][0]["institution_remediation_template"] = "已修改模板"
    changed_payload["rules"][0]["consumer_notice_template"] = "已修改消费者提示"
    changed = DeterministicScreeningService(MarketingRuleSet.model_validate(changed_payload))
    assert changed.institution_report(session, run.id) == institution
    assert changed.consumer_notice(session, run.id) == consumer

    deleted_payload = load_ruleset().model_dump(mode="json")
    deleted_payload["rules"] = deleted_payload["rules"][1:]
    deleted = DeterministicScreeningService(MarketingRuleSet.model_validate(deleted_payload))
    assert deleted.institution_report(session, run.id) == institution
    assert deleted.consumer_notice(session, run.id) == consumer
    persisted = session.get(ScreeningRun, run.id)
    assert persisted is not None
    assert persisted.ruleset_snapshot_sha256 == persisted.ruleset_sha256


def test_screening_api_and_safe_json_output(session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).post(
            "/api/v1/screenings",
            json={
                "title": "<script>alert(1)</script>",
                "material_type": "social_media",
                "raw_text": "<script>保证收益</script>",
                "source_label": "user_submission",
            },
        )
        assert response.status_code == 200
        run_id = response.json()["screening_run_id"]
        report = TestClient(app).get(f"/api/v1/screenings/{run_id}/institution-report")
    finally:
        app.dependency_overrides.clear()
    assert report.status_code == 200
    assert report.json()["material_title"] == "<script>alert(1)</script>"
    assert report.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("raw_text", "", "screening_material_empty"),
        ("material_type", "pdf", "screening_material_type_invalid"),
        ("title", "x" * 301, "screening_title_invalid"),
    ],
)
def test_screening_api_returns_stable_boundary_error_codes(
    session, field: str, value: str, code: str
) -> None:
    request = {
        "title": "API边界",
        "material_type": "other",
        "raw_text": "普通文本",
        "source_label": "unit_test",
    }
    request[field] = value
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).post("/api/v1/screenings", json=request)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
