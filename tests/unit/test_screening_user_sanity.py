from __future__ import annotations

import pytest

from app.services.screening import (
    DeterministicComplianceRuleEngine,
    load_ruleset,
    segment_marketing_text,
)


def _rule_ids(text: str) -> set[str]:
    material_sha256 = "9" * 64
    findings = DeterministicComplianceRuleEngine(load_ruleset()).run(
        raw_text=text,
        material_sha256=material_sha256,
        segments=segment_marketing_text(text, material_sha256),
    )
    return {finding.rule_id for finding in findings}


@pytest.mark.parametrize(
    ("text", "required_rule"),
    [
        ("本产品保证收益", "guaranteed_return_or_principal"),
        ("收益已锁定", "guaranteed_return_or_principal"),
        ("这款产品固定赚", "guaranteed_return_or_principal"),
        ("绝对高收益", "absolute_or_superlative_claim"),
        ("收益一定很高", "absolute_or_superlative_claim"),
        ("肯定能赚钱", "absolute_or_superlative_claim"),
        ("百分百高回报", "absolute_or_superlative_claim"),
        ("绝对安全", "no_risk_or_no_loss"),
        ("完全没风险", "no_risk_or_no_loss"),
        ("百分百不亏", "no_risk_or_no_loss"),
        ("保险就是存款", "product_nature_confusion"),
        ("这是监管推荐的保险", "regulatory_endorsement"),
        ("随时退保没有损失", "surrender_or_cash_value_misstatement"),
        ("这款保险没有等待期", "waiting_or_cooling_period_misstatement"),
        ("购买保险赠送现金", "extra_contractual_benefit"),
        ("什么情况都赔", "concealment_or_minimization_of_exclusions"),
    ],
)
def test_basic_human_visible_positive_claims(text: str, required_rule: str) -> None:
    assert required_rule in _rule_ids(text)


@pytest.mark.parametrize(
    "text",
    [
        "合同要点说明",
        "本产品不保证高收益，保险利益以合同约定为准。",
        "本产品不存在绝对收益",
        "不得宣传绝对高收益",
        "收益并非一定很高",
        "不承诺本金不受损失",
        "没有任何产品是绝对安全的",
        "保险产品并非无风险",
        "请勿相信‘百分百不亏’的宣传",
        "产品收益可能发生波动",
        "预期收益不代表实际收益",
        "保险利益以合同约定为准",
        "退保可能有损失，以现金价值为准",
        "等待期和犹豫期以合同约定为准",
        "监管备案不等于监管保证",
        "产品说明称保障较全面，整体很安全",
    ],
)
def test_basic_normal_warning_and_negated_claims_are_not_flagged(text: str) -> None:
    assert _rule_ids(text) == set()


def test_live_user_phrase_has_general_absolute_claim_detection() -> None:
    rules = _rule_ids("放心买，很安全的，绝对高收益")
    assert "absolute_or_superlative_claim" in rules
