from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
JSONL = HERE / "v2_development_v1.jsonl"
JSON_FILE = HERE / "v2_development_v1.json"
MANIFEST = HERE / "manifest.json"

POSITIVES: dict[str, list[tuple[str, str]]] = {
    "absolute_or_superlative_claim": [
        ("explicit", "这份保障方案绝对适合每一位家庭成员。"),
        ("paraphrase", "无论客户情况怎样，这款产品都必然是正确选择。"),
        ("colloquial", "闭眼选就行，它肯定是最好的。"),
        ("light_implicit", "市场上不会有比这份保障更完整的方案。"),
        ("multi_sentence", "您不用再比较了。这就是唯一值得购买的保险方案。"),
        ("context_dependent", "面向首次投保家庭，我们可以明确说它百分之百合适。"),
    ],
    "concealment_or_minimization_of_exclusions": [
        ("explicit", "合同里的责任免除只是格式内容，理赔时可以忽略。"),
        ("paraphrase", "健康告知怎么填写都不会影响以后获得赔付。"),
        ("colloquial", "那些不保事项不用看，真出事都会赔。"),
        ("light_implicit", "即便属于条款列明的免责情形，公司照样承担责任。"),
        ("multi_sentence", "合同确实列了除外责任。不过这些内容不会限制实际保障。"),
        ("context_dependent", "向客户介绍时可以略过健康告知，它不会影响承保结果。"),
    ],
    "extra_contractual_benefit": [
        ("explicit", "今天投保后，销售人员会另外返给您八百元现金。"),
        ("paraphrase", "签单后可获得合同没有写明的专属购物卡。"),
        ("colloquial", "保单办下来我私下再送你一部手机。"),
        ("light_implicit", "完成缴费即可领取条款之外的全年机场接送服务。"),
        ("multi_sentence", "保险利益以合同为准。除此之外，投保客户还能额外拿到现金补贴。"),
        ("context_dependent", "只要成为本产品的新客户，就赠送合同外的免费体检套餐。"),
    ],
    "false_promotion_or_prize": [
        ("explicit", "本周投保可参加抽奖，而且每位客户保证中奖。"),
        ("paraphrase", "现在下单就能获得其实并不存在的限量免费名额。"),
        ("colloquial", "赶紧买，今晚过后所谓的百万奖池就没了。"),
        ("light_implicit", "本次活动的头奖人人可得，每位投保客户都不会落空。"),
        ("multi_sentence", "活动仅剩十分钟。投保即可抽取豪车，中奖结果已为您锁定。"),
        ("context_dependent", "现在签单就是前一百名，高额奖品名额已经为您锁定。"),
    ],
    "guaranteed_return_or_principal": [
        ("explicit", "本产品保证每年获得固定的百分之六收益。"),
        ("paraphrase", "到期时本金和利息都会一分不少地返还。"),
        ("colloquial", "钱放进来稳赚，到点连本带利拿走。"),
        ("light_implicit", "无论市场如何变化，您的本金与既定回报都不会改变。"),
        ("multi_sentence", "收益表只是展示形式。实际到账金额已经提前锁定。"),
        ("context_dependent", "选择十年缴费方案后，公司承诺最终一定盈利。"),
    ],
    "improper_comparison_or_ranking": [
        ("explicit", "这款产品的保障水平稳居行业第一。"),
        ("paraphrase", "同类公司的方案在各方面都不如我们的产品。"),
        ("colloquial", "别家的保险都差一截，我们这款才是头名。"),
        ("light_implicit", "本产品的安全性全面领先所有竞争产品。"),
        ("multi_sentence", "其他同类产品都有不足。本产品收益全面高于它们。"),
        ("context_dependent", "在销售海报中把本公司描述成市场唯一领先的保险机构。"),
    ],
    "misleading_interest_or_yield": [
        ("explicit", "演示表中的百分之八收益率就是客户每年实际到账回报。"),
        ("paraphrase", "去年的高收益会原样延续到未来每一个保单年度。"),
        ("colloquial", "图上写多少利率，你以后就能拿多少。"),
        ("light_implicit", "这里的预期回报就是客户能够持续取得的稳定年化收益。"),
        ("multi_sentence", "该数字来自情景演示。不过您可以把它当作持续不变的实际利率。"),
        ("context_dependent", "用单个历史年度的结算表现推断客户未来都能获得同等收益。"),
    ],
    "no_risk_or_no_loss": [
        ("explicit", "购买这款保险完全没有任何风险。"),
        ("paraphrase", "无论发生什么情况，投入的资金都不会有损失。"),
        ("colloquial", "放心放钱，怎么都不可能亏。"),
        ("light_implicit", "将产品描述为资金绝对安全且不存在价值波动。"),
        ("multi_sentence", "市场可能出现变化。但您的账户不承担任何损失可能。"),
        ("context_dependent", "面向谨慎客户时，直接说明这份产品属于零风险选择。"),
    ],
    "product_nature_confusion": [
        ("explicit", "这不是保险，本质上就是银行的定期存款。"),
        ("paraphrase", "购买本保单等同于把钱存进普通储蓄账户。"),
        ("colloquial", "把它当存款就行，跟保险没什么关系。"),
        ("light_implicit", "您购买的就是银行理财产品，不必把它看作人身保险。"),
        ("multi_sentence", "合同名称虽然写着年金保险，但它其实就是一只基金。"),
        ("context_dependent", "在柜面销售时把保险缴费描述成开立高息存款账户。"),
    ],
    "regulatory_endorsement": [
        ("explicit", "监管部门已经认证并推荐购买这款保险。"),
        ("paraphrase", "本产品的收益和安全性由监管机构提供担保。"),
        ("colloquial", "这是监管点名让大家买的产品。"),
        ("light_implicit", "本方案属于监管机关官方指定的保险。"),
        ("multi_sentence", "产品已经完成备案，所以监管部门也为它的收益提供站台。"),
        ("context_dependent", "面对犹豫客户时，销售人员表示监管机关已经认可该产品表现。"),
    ],
    "surrender_or_cash_value_misstatement": [
        ("explicit", "任何时候退保都能把已交保费全额拿回。"),
        ("paraphrase", "提前终止合同不会产生费用或本金损失。"),
        ("colloquial", "想退就退，交进去的钱一分都不会少。"),
        ("light_implicit", "保单早期的现金价值始终等于您累计交纳的保费。"),
        ("multi_sentence", "合同虽然列有现金价值表，但退保金额永远等于本金。"),
        ("context_dependent", "在首个保单年度申请退保，也承诺立即返还全部缴费。"),
    ],
    "waiting_or_cooling_period_misstatement": [
        ("explicit", "本产品没有等待期，生效当天任何疾病都能赔。"),
        ("paraphrase", "犹豫期结束后仍可随时无条件撤销并拿回全部保费。"),
        ("colloquial", "不用等，刚签完合同马上什么情况都保。"),
        ("light_implicit", "合同写了九十天等待期，实际只需要等待一天。"),
        ("multi_sentence", "条款写有犹豫期。不过超过该期限退保效果也完全相同。"),
        ("context_dependent", "针对健康险客户，销售人员否认疾病保障存在任何等待阶段。"),
    ],
}

NEGATIVES: dict[str, list[str]] = {
    "quoted": [
        "培训课件展示错误话术：“这款产品保证收益”。",
        "投诉记录原文写道：“投保肯定返钱”，现要求核查该表述。",
        "媒体报道引用销售人员的话：“没有任何风险”。",
        "风险复盘材料列出“监管推荐”这一曾被使用的说法。",
    ],
    "educational": [
        "合规课堂说明，把预期收益说成固定回报属于误导性宣传。",
        "本页用于识别“最高收益”和“行业第一”等绝对化话术。",
        "消费者教育提示：保险与银行存款是不同性质的金融安排。",
        "内部教材解释退保金额应以现金价值和合同约定为准。",
    ],
    "prohibition": [
        "监管要求销售人员不得宣称产品保证收益。",
        "宣传材料中禁止使用零风险、绝不亏损等用语。",
        "公司严禁把保险介绍成银行存款。",
        "任何渠道均不得声称监管部门推荐具体保险产品。",
    ],
    "consumer_warning": [
        "请勿相信所谓百分之百不亏的保险宣传。",
        "遇到声称投保送高额返现的话术，请先核对正式合同。",
        "消费者应警惕把演示利率当作实际收益的介绍。",
        "不要因为销售人员说可以随时全额退保就仓促签约。",
    ],
    "internal_incentive": [
        "公司完成季度销售目标后奖励销售人员一部手机。",
        "业务员年度业绩达到标准，可获得内部旅游激励。",
        "机构向优秀员工发放绩效奖金，与客户投保权益无关。",
        "团队新增保单达到考核数量后，公司给组员发放购物卡。",
    ],
    "comparison_explanation": [
        "本文仅解释不同保险责任的比较方法，不评价任何产品排名。",
        "表格对两份合同的等待期作客观列示，未声称哪一方更优。",
        "消费者可比较保障范围、费用与免责事项后再作选择。",
        "报告说明行业排名需要统一范围和公开数据，不能随意宣称第一。",
    ],
    "historical": [
        "历史案例中，某销售人员曾因承诺保本而受到处理。",
        "以往处罚材料记载机构使用过“监管指定”的宣传语。",
        "案件复盘记录客户曾被告知退保没有损失。",
        "新闻回顾提到多年前市场上出现过保证中奖的投保活动。",
    ],
    "negated": [
        "本产品不保证收益，非保证利益可能发生变化。",
        "保险合同不承诺本金不受损失，请审慎评估需求。",
        "该方案并非无风险，也不能替代银行存款。",
        "我们不提供合同之外的返现或礼品。",
    ],
    "conditional": [
        "若符合合同约定的保险责任，保险公司将按条款给付保险金。",
        "演示收益可能随实际经营状况变化，不代表未来结果。",
        "如在犹豫期内按合同申请解除，退款金额依条款计算。",
        "等待期是否适用以及期限长短，应以正式合同载明内容为准。",
    ],
    "role_ambiguity": [
        "会议纪要提到有人建议送手机，但未说明对象、条件或是否采纳。",
        "转述材料称某人说过收益很高，发言身份和使用场景均不明确。",
        "草稿边注写有‘第一’，无法确认是标题编号还是营销表述。",
        "聊天记录只出现‘奖励’，未说明奖励客户、员工还是合作方。",
    ],
    "clarification": [
        "本产品是保险，不是银行存款，保险利益以合同为准。",
        "监管备案不代表监管机构对产品收益作出保证或推荐。",
        "现金价值可能低于已交保费，提前退保可能产生损失。",
        "合同包含等待期和责任免除，投保前请完整阅读条款。",
    ],
    "compliant": [
        "本材料用于说明产品基本信息，具体责任以保险合同为准。",
        "非保证利益存在不确定性，展示数值仅供理解情景使用。",
        "投保人应根据保障需求和支付能力审慎决定是否购买。",
        "如需解除合同，请查阅现金价值表并咨询退保流程。",
    ],
}


def canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    ordinal = 1
    for rule, positive_examples in POSITIVES.items():
        if len(positive_examples) != 6:
            raise RuntimeError(f"positive_count_invalid:{rule}")
        for stratum, text in positive_examples:
            expected = [rule]
            if rule == "improper_comparison_or_ranking" and any(
                marker in text for marker in ("第一", "唯一")
            ):
                expected.append("absolute_or_superlative_claim")
            records.append(
                {
                    "case_id": f"V2DEV-{ordinal:03d}",
                    "split": "DEV_ONLY",
                    "case_type": "positive",
                    "stratum": stratum,
                    "context_group": "direct_marketing",
                    "expected_rules": sorted(expected),
                    "text": text,
                }
            )
            ordinal += 1
    for context_group, negative_examples in NEGATIVES.items():
        if len(negative_examples) != 4:
            raise RuntimeError(f"negative_count_invalid:{context_group}")
        for text in negative_examples:
            records.append(
                {
                    "case_id": f"V2DEV-{ordinal:03d}",
                    "split": "DEV_ONLY",
                    "case_type": "negative",
                    "stratum": "hard_negative" if context_group != "compliant" else "compliant",
                    "context_group": context_group,
                    "expected_rules": [],
                    "text": text,
                }
            )
            ordinal += 1
    if len(records) != 120 or len({row["text"] for row in records}) != 120:
        raise RuntimeError("dataset_cardinality_invalid")
    return records


def main() -> None:
    records = build()
    JSONL.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records),
        encoding="utf-8",
    )
    JSON_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "dataset_id": "v2_development_v1",
        "split": "DEV_ONLY",
        "headline_benchmark": False,
        "case_count": len(records),
        "positive_count": sum(bool(row["expected_rules"]) for row in records),
        "negative_count": sum(not row["expected_rules"] for row in records),
        "per_rule_support": dict(
            sorted(Counter(rule for row in records for rule in row["expected_rules"]).items())
        ),
        "context_groups": dict(sorted(Counter(row["context_group"] for row in records).items())),
        "dataset_sha256": canonical_sha(records),
        "construction_policy": (
            "Independent V2 development material; not a holdout and not a headline benchmark. "
            "No V1 frozen predictions or failure cases were used for tuning."
        ),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
