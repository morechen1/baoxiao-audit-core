from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SYSTEM_FREEZE_HEAD = "1176d10e1fd62b7778324157e585b1f22b3cb035"
DATASET_NAME = "FINAL EXTENDED FROZEN VALIDATION v1"
MODEL = "deepseek-v4-flash"

RULES = [
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
]

# Cases are authored from the frozen business taxonomy definitions and ordinary Chinese
# insurance-marketing language. No matcher pattern, parser prompt example, or old prediction
# is imported by this builder.
SINGLE: dict[str, list[tuple[str, str]]] = {
    "absolute_or_superlative_claim": [
        ("这是市场上唯一能同时照顾三代家庭保障需求的保险方案。", "explicit"),
        ("这份保障方案是家庭资产安排的最佳选择。", "explicit"),
        ("无论家庭情况怎样，选择这款产品总是最合适。", "paraphrase"),
        ("宣传材料把这份方案描述成所有家庭都适用的最周全保障。", "paraphrase"),
        ("我敢说这就是最省心的保障安排，挑它不会错。", "colloquial"),
        ("这份保障肯定能把你们家的需求全照顾到。", "colloquial"),
        ("产品介绍将其称为没有替代选项的家庭保障选择。", "light_implicit"),
        ("海报把该方案定位成适用于人生任何阶段的终极保障。", "light_implicit"),
    ],
    "concealment_or_minimization_of_exclusions": [
        ("销售时无需向客户说明责任免除，先把保障利益讲清即可。", "explicit"),
        ("健康告知中的既往症影响可以略去不谈，以免客户放弃投保。", "explicit"),
        ("介绍产品时把免责部分放到最后一页，口头沟通不必提及。", "paraphrase"),
        ("客户问到不保事项时，只需强调保障范围，避免展开限制条件。", "paraphrase"),
        ("免赔和除外那些细节先别跟客户说，省得他犹豫。", "colloquial"),
        ("健康告知就轻描淡写带过，重点把能赔的讲漂亮。", "colloquial"),
        ("宣传页完整列出保障责任，却有意省略同一疾病的除外条件。", "light_implicit"),
        ("销售说明把责任免除缩成难以阅读的小字，并称其无需关注。", "light_implicit"),
    ],
    "extra_contractual_benefit": [
        ("现在投保，我个人再返还你两千元现金，合同里不体现。", "explicit"),
        ("签单后由销售团队额外赠送一年家政服务，不写入保险合同。", "explicit"),
        ("达到约定保费后，客户可从业务员处领取合同之外的购物卡。", "paraphrase"),
        ("完成投保即可享受公司另行提供、保单未约定的体检套餐。", "paraphrase"),
        ("你今天把单签了，我私下给你包个大红包。", "colloquial"),
        ("保费交完我再送你一台平板，这个不走合同。", "colloquial"),
        ("营销方案把未载入条款的会员权益作为促成投保的附加回馈。", "light_implicit"),
        ("销售人员以保单外的机场接送服务吸引客户尽快投保。", "light_implicit"),
    ],
    "false_promotion_or_prize": [
        ("投保本产品即可参加豪车抽奖，奖品数量和活动规则暂不公开。", "explicit"),
        ("限时投保赠送境外双人游，活动细则稍后另行通知。", "explicit"),
        ("缴费后自动获得幸运大奖资格，奖项安排不在宣传页说明。", "paraphrase"),
        ("今日签单可进入神秘奖池，具体奖品由销售人员另行告知。", "paraphrase"),
        ("现在买就能抽豪车，规则先不用管。", "colloquial"),
        ("把保单办了就送你一个大奖机会，奖品之后再说。", "colloquial"),
        ("营销页面用倒计时和稀缺名额推动投保，却没有披露促销期限与规则。", "light_implicit"),
        ("宣传将普通积分兑换包装成限量大奖活动，引导客户立即签约。", "light_implicit"),
    ],
    "guaranteed_return_or_principal": [
        ("本产品承诺每年按固定收益率结算，并在期满返还全部本金。", "explicit"),
        ("宣传口径把本金返还和年度收益列为确定给付。", "explicit"),
        ("销售人员表示未来收益已经锁定，客户按期领取即可。", "paraphrase"),
        ("介绍材料将本金完整返还描述为产品承诺而非演示结果。", "paraphrase"),
        ("这笔钱放进来，每年拿多少早就定好了。", "colloquial"),
        ("到期本金照数回来，收益也按说好的给。", "colloquial"),
        ("收益表中的固定数值被作为保单实际承诺向客户说明。", "light_implicit"),
        ("销售说明把非保证利益表述成到期确定兑现的金额。", "light_implicit"),
    ],
    "improper_comparison_or_ranking": [
        ("宣传称本产品保障范围优于同类保险，却没有提供比较依据。", "explicit"),
        ("销售话术声称本公司理赔服务胜过A公司，但没有可核验数据。", "explicit"),
        ("介绍页把两款不同期限产品直接比较，并宣称本款回报更高。", "paraphrase"),
        ("宣传只选取对手较弱项目，得出本产品保障更好的结论。", "paraphrase"),
        ("同样交一万，我们这款就是比别家的更值。", "colloquial"),
        ("别家那种保障差远了，还是我们这个划算。", "colloquial"),
        ("材料以不同统计口径制作排名，将本产品置于同类首位。", "light_implicit"),
        ("销售展示未经来源说明的行业对比表，突出本产品领先位置。", "light_implicit"),
    ],
    "misleading_interest_or_yield": [
        ("宣传把演示收益率百分之六写成客户实际年收益。", "explicit"),
        ("产品页面只展示高档收益情景，未说明该数值属于非保证利益。", "explicit"),
        ("销售用过去某一年度的结算率推断客户未来每年的回报。", "paraphrase"),
        ("宣传将预期收益区间当作客户可实际取得的回报。", "paraphrase"),
        ("演示表上写百分之六，你就按这个数理解每年收益吧。", "colloquial"),
        ("去年结算得不错，今年照着那个水平看就行。", "colloquial"),
        ("收益图表突出最高演示档位，弱化实际结果可能波动的说明。", "light_implicit"),
        ("销售测算把不同期限的累计回报换算成年化数字，却未说明口径。", "light_implicit"),
    ],
    "no_risk_or_no_loss": [
        ("这款产品不存在市场波动导致的资金损失。", "explicit"),
        ("投保资金不会因为风险变化出现亏损。", "explicit"),
        ("产品风险已经被消除，资金只会稳步积累。", "paraphrase"),
        ("销售介绍将产品描述为零波动、零损失的资金安排。", "paraphrase"),
        ("放心放进来，怎么都亏不了。", "colloquial"),
        ("这份保险不用担心赔钱的可能。", "colloquial"),
        ("材料暗示客户无需评估风险，因为本金不受市场影响。", "light_implicit"),
        ("宣传把保险描述成无需承担损失可能的稳健工具。", "light_implicit"),
    ],
    "product_nature_confusion": [
        ("这不是保险，就是银行定期存款的替代品。", "explicit"),
        ("销售把年金保险称为可随取随用的银行理财账户。", "explicit"),
        ("介绍中将保费缴纳描述为往储蓄账户存款。", "paraphrase"),
        ("宣传把保险合同包装成与货币基金相同的现金管理工具。", "paraphrase"),
        ("你就当在银行存钱，名字叫保险而已。", "colloquial"),
        ("这其实就是个理财账户，和买保险没什么关系。", "colloquial"),
        ("页面全程使用存入、利息和账户余额等措辞，回避其保险合同性质。", "light_implicit"),
        ("销售以基金申购的方式解释投保流程，使客户误认产品属性。", "light_implicit"),
    ],
    "regulatory_endorsement": [
        ("该产品已获金融监管部门认证，购买更有保障。", "explicit"),
        ("监管机构指定本公司承办这款养老保险。", "explicit"),
        ("宣传称产品方案得到监管专家认可并推荐给家庭客户。", "paraphrase"),
        ("销售介绍借用监管备案概念，声称官方为产品安全背书。", "paraphrase"),
        ("这是监管点名推荐给消费者的产品。", "colloquial"),
        ("官方都认可这款，您放心投保就行。", "colloquial"),
        ("海报把监管部门标识放在产品名称旁，暗示双方存在推荐关系。", "light_implicit"),
        ("材料称完成备案意味着产品品质获得监管保证。", "light_implicit"),
    ],
    "surrender_or_cash_value_misstatement": [
        ("保单生效后随时退保都能按已交保费原额领取。", "explicit"),
        ("前五年退保不扣任何费用，现金价值等同累计保费。", "explicit"),
        ("销售把现金价值说成客户想取就能全额取出的余额。", "paraphrase"),
        ("宣传称中途退出只影响保障，不会造成资金减少。", "paraphrase"),
        ("哪天不想要了就退，交多少拿回多少。", "colloquial"),
        ("这钱随时能退出来，一分钱都不会少。", "colloquial"),
        ("材料用可灵活支取描述退保，却未提示现金价值可能低于已交保费。", "light_implicit"),
        ("销售示例把某一保单年度的现金价值直接等同退保到账金额。", "light_implicit"),
    ],
    "waiting_or_cooling_period_misstatement": [
        ("本产品投保次日即覆盖所有疾病，不设等待期。", "explicit"),
        ("犹豫期结束后仍可无条件全额撤单。", "explicit"),
        ("销售称等待期只是流程名称，期间出险同样按正常责任赔付。", "paraphrase"),
        ("宣传把十五天犹豫期解释为从收到宣传册当天开始计算。", "paraphrase"),
        ("今天买明天生病也照常赔，根本不用等。", "colloquial"),
        ("过了犹豫期想退也还是原款退给你。", "colloquial"),
        ("产品页面省略九十天等待安排，并暗示保障自缴费时完整生效。", "light_implicit"),
        ("销售将犹豫期与宽限期混为一谈，告知客户缴费后六十天内可撤销。", "light_implicit"),
    ],
}

MULTI: list[tuple[str, list[str]]] = [
    ("这是唯一能确保每年固定回报并完整返还本金的家庭保险方案。", [RULES[0], RULES[4]]),
    ("这款产品是全行业最安全的选择，资金完全不可能出现损失。", [RULES[0], RULES[7]]),
    ("我们的保障排名市场第一，各项责任都优于其他同类产品。", [RULES[0], RULES[5]]),
    ("免责条款不必向客户展开，因为购买这款产品不存在损失风险。", [RULES[1], RULES[7]]),
    ("销售时不要提九十天等待期，直接告诉客户缴费后保障立即全部生效。", [RULES[1], RULES[11]]),
    ("介绍产品时隐去退保费用，并告诉客户退出时可以取回全部已交保费。", [RULES[1], RULES[10]]),
    ("今天签单额外送购物卡，还能参加规则尚未公布的豪车抽奖。", [RULES[2], RULES[3]]),
    ("投保后业务员私下返现，同时承诺保单每年提供固定收益。", [RULES[2], RULES[4]]),
    ("销售称这是监管推出的惠民产品，投保后另送合同外健康服务。", [RULES[2], RULES[9]]),
    ("这是本季度唯一的限量投保大奖活动，名额倒计时结束后不再开放。", [RULES[0], RULES[3]]),
    ("宣传称监管部门支持本次投保抽奖，具体活动规则由销售随后说明。", [RULES[3], RULES[9]]),
    ("演示表中的百分之六被说成已经锁定、每年固定到账的实际收益。", [RULES[4], RULES[6]]),
    ("销售承诺期满本金和收益照数返还，并表示整个持有过程不会亏损。", [RULES[4], RULES[7]]),
    ("宣传用不同期限产品作比较，声称本款的演示收益明显高于所有竞品。", [RULES[5], RULES[6]]),
    ("销售说这款保险其实就是银行理财，而且比同类存款产品更划算。", [RULES[5], RULES[8]]),
    ("材料把高档演示收益当成实际回报，并据此夸大退保时可领取的现金价值。", [RULES[6], RULES[10]]),
    ("这份保险就是随取随用的储蓄账户，放进去的资金没有亏损可能。", [RULES[7], RULES[8]]),
    ("宣传把保险称作官方认可的储蓄产品，让客户误以为由监管机构运营。", [RULES[8], RULES[9]]),
    ("销售称产品由监管部门担保，并承诺客户每年取得确定回报。", [RULES[4], RULES[9]]),
    ("客户随时退保都不会损失，已交保费可以原额拿回。", [RULES[7], RULES[10]]),
    ("销售把等待期说成可忽略的手续，还称过了犹豫期退保仍能全额退款。", [RULES[10], RULES[11]]),
    ("保障从投保当天完整生效，等待期间也不存在任何保障缺口。", [RULES[7], RULES[11]]),
    ("把保费存入这个保险账户还能得到合同外礼品，使用方式和储蓄账户一样。", [RULES[2], RULES[8]]),
    ("销售借监管备案为产品排名背书，宣称其保障水平高于其他保险公司。", [RULES[5], RULES[9]]),
]

NEGATIVE: dict[str, list[str]] = {
    "negation": [
        "本产品不保证收益，实际利益以合同和经营情况为准。",
        "销售人员不得承诺本金安全，也不能保证客户不会发生损失。",
        "该演示利率不代表未来回报，实际结果可能高于或低于演示值。",
        "本保险并非银行存款，也不具备随时支取的储蓄账户属性。",
        "监管备案不代表对产品收益、安全或品质作出认可与推荐。",
        "退保金额不等于已交保费，具体以退保时现金价值为准。",
        "等待期内发生的疾病是否承担责任，应依保险合同约定判断。",
        "本公司不会向投保人提供保险合同之外的返现或礼品。",
        "宣传材料不得省略责任免除和健康告知的重要影响。",
        "产品不存在所谓唯一最佳方案，客户应结合自身需求选择。",
        "本次活动不设置抽奖或保证中奖安排，详情以公开规则为准。",
        "收益并非固定到账，消费者应充分了解非保证利益的不确定性。",
    ],
    "educational": [
        "不得使用最高、唯一、百分之百等绝对化语言宣传保险产品。",
        "向消费者介绍产品时，应完整说明责任免除和不保事项。",
        "禁止以合同外返现、礼品或服务诱导消费者投保。",
        "保险营销活动不得虚构限时名额、抽奖资格或奖品价值。",
        "销售人员不得向客户承诺固定收益、保本或到期必然获利。",
        "比较保险产品时应采用一致口径并提供可核验的数据来源。",
        "演示收益和历史结算水平不得作为未来实际回报进行宣传。",
        "保险产品具有风险，不得宣称零风险、零损失或资金绝对安全。",
        "介绍保险时不得混同银行存款、基金或普通理财产品。",
        "禁止暗示监管机构对具体保险产品作出认证、推荐或担保。",
        "说明退保时应提示现金价值及可能发生的费用和损失。",
        "销售人员应准确解释等待期、犹豫期的起算和保障效果。",
    ],
    "compliant": [
        "本产品为长期人身保险，保险责任、责任免除及给付条件详见合同条款。",
        "页面所列利益演示基于假设，不代表实际收益，也不构成任何承诺。",
        "投保前请如实完成健康告知，并仔细阅读免责事项和等待期说明。",
        "犹豫期、退保现金价值和相关费用以正式保险合同及保全规则为准。",
        "消费者可结合保障需求和缴费能力自主选择，宣传内容仅作产品介绍。",
        "历史数据仅供参考，保险利益可能受合同约定和实际经营情况影响。",
    ],
    "hard_negative": [
        "公司决定季度业绩达标后奖励销售团队手机，奖励对象仅限内部员工。",
        "培训负责人提出为优秀代理人发放购物卡，不面向投保客户。",
        "分公司内部竞赛设置旅行奖励，依据销售人员合规服务质量评定。",
        "客服人员提醒客户警惕所谓监管推荐产品的虚假宣传。",
        "合规课程分析了保证收益、随时退保无损失等典型错误话术。",
        "会议要求下架使用零风险和最佳产品字样的旧宣传材料。",
    ],
}

FORBIDDEN_EXACT = {
    "放心买，很安全的，绝对高收益",
    "这是我们开门红主推的产品，佣金达到30%，季度末之前累计达到20万，再送您17promax一台！",
    "购买本保险产品并达到约定保费金额，即额外赠送您一台 iPhone。",
    "监管推荐本产品，保证收益8%，本金绝对安全，今天投保即可领取限量礼品。",
    "资金使用灵活，如有需要可随时退保没有损失。具体权益和现金价值请以合同约定为准。",
    "本材料仅作产品信息说明，保险责任、等待期、责任免除及退保安排以正式保险合同为准。投保前请阅读条款并按需咨询持证人员。",
}


def normalize(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text).lower()


def grams(text: str, size: int = 3) -> set[str]:
    value = normalize(text)
    return {value[index : index + size] for index in range(max(0, len(value) - size + 1))}


def similarity(left: str, right: str) -> float:
    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def old_texts() -> set[str]:
    texts = set(FORBIDDEN_EXACT)
    json_paths = [
        ROOT / "data/evaluations/candidates/m6b_constructed_candidate_v1.json",
        ROOT / "data/evaluations/candidates/m6c_external_candidate_v2.json",
        ROOT / "data/evaluations/provisional/m6b_constructed_provisional_v1.json",
        ROOT / "data/evaluations/provisional/m6c_external_provisional_v2.json",
        ROOT / "tests/fixtures/constructed_screening_eval_v1/samples.json",
    ]

    def visit(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and key in {"text", "raw_text", "input_text", "marketing_text"}:
            if len(normalize(value)) >= 8:
                texts.add(value)

    for path in json_paths:
        if path.exists():
            visit(json.loads(path.read_text(encoding="utf-8")))

    sanity_path = ROOT / "tests/unit/test_screening_user_sanity.py"
    if sanity_path.exists():
        tree = ast.parse(sanity_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if len(normalize(node.value)) >= 12:
                    texts.add(node.value)
    return texts


def build_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for rule_index, rule in enumerate(RULES, start=1):
        entries = SINGLE[rule]
        assert len(entries) == 8
        for item_index, (text, stratum) in enumerate(entries, start=1):
            cases.append(
                {
                    "case_id": f"FEV-S-{rule_index:02d}-{item_index:02d}",
                    "text": text,
                    "expected_rules": [rule],
                    "primary_rule": rule,
                    "case_type": "single_positive",
                    "stratum": stratum,
                    "rationale": f"文本以{stratum}表达呈现{rule}风险，未标注其他非必要类别。",
                    "review_status": "AI_ASSISTED_FROZEN_VALIDATION",
                    "seen_by_system_before_freeze": False,
                }
            )
    for index, (text, rules) in enumerate(MULTI, start=1):
        cases.append(
            {
                "case_id": f"FEV-M-{index:03d}",
                "text": text,
                "expected_rules": rules,
                "primary_rule": rules[0],
                "case_type": "multi_positive",
                "stratum": "multi_label",
                "rationale": "文本自然包含两个独立且可辨识的保险营销风险主张。",
                "review_status": "AI_ASSISTED_FROZEN_VALIDATION",
                "seen_by_system_before_freeze": False,
            }
        )
    negative_index = 0
    for stratum in ("negation", "educational", "compliant", "hard_negative"):
        for text in NEGATIVE[stratum]:
            negative_index += 1
            cases.append(
                {
                    "case_id": f"FEV-N-{negative_index:03d}",
                    "text": text,
                    "expected_rules": [],
                    "primary_rule": None,
                    "case_type": "negative",
                    "stratum": stratum,
                    "rationale": "该文本属于合规限定、教育禁止或非消费者营销语境，不应形成风险标签。",
                    "review_status": "AI_ASSISTED_FROZEN_VALIDATION",
                    "seen_by_system_before_freeze": False,
                }
            )
    return cases


def main() -> None:
    cases = build_cases()
    assert len(cases) == 156
    assert len({case["text"] for case in cases}) == 156
    assert len({case["case_id"] for case in cases}) == 156
    assert sum(case["case_type"] == "single_positive" for case in cases) == 96
    assert sum(case["case_type"] == "multi_positive" for case in cases) == 24
    assert sum(case["case_type"] == "negative" for case in cases) == 36
    assert sum(len(case["expected_rules"]) for case in cases) == 144
    assert all(set(case["expected_rules"]) <= set(RULES) for case in cases)

    prior = old_texts()
    exact_external = sorted(case["case_id"] for case in cases if case["text"] in prior)
    normalized_seen: dict[str, str] = {}
    normalized_duplicates: list[list[str]] = []
    for case in cases:
        key = normalize(str(case["text"]))
        if key in normalized_seen:
            normalized_duplicates.append([normalized_seen[key], str(case["case_id"])])
        normalized_seen[key] = str(case["case_id"])

    internal_near: list[dict[str, object]] = []
    for left_index, left in enumerate(cases):
        for right in cases[left_index + 1 :]:
            score = similarity(str(left["text"]), str(right["text"]))
            if score >= 0.82:
                internal_near.append({"left": left["case_id"], "right": right["case_id"], "score": round(score, 4)})
    external_near: list[dict[str, object]] = []
    for case in cases:
        best_score = 0.0
        for old in prior:
            score = similarity(str(case["text"]), old)
            best_score = max(best_score, score)
        if best_score >= 0.86:
            external_near.append({"case_id": case["case_id"], "score": round(best_score, 4)})

    quality = {
        "cases": len(cases),
        "unique_texts": len({case["text"] for case in cases}),
        "exact_external_duplicates": exact_external,
        "normalized_internal_duplicates": normalized_duplicates,
        "internal_near_duplicates": internal_near,
        "external_near_duplicates": external_near,
        "near_duplicate_method": "normalized Chinese/alphanumeric character 3-gram Jaccard; internal>=0.82, external>=0.86",
        "old_sets_used_only_for_duplicate_detection": True,
    }
    (OUT / "quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if exact_external or normalized_duplicates or internal_near or external_near:
        raise SystemExit("dataset_quality_check_failed")

    jsonl = "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases)
    dataset_path = OUT / "final_extended_frozen_validation_v1.jsonl"
    dataset_path.write_text(jsonl, encoding="utf-8")
    (OUT / "final_extended_frozen_validation_v1.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    dataset_sha = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    manifest = {
        "dataset_name": DATASET_NAME,
        "version": "v1",
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "cases": 156,
        "taxonomy": RULES,
        "taxonomy_count": 12,
        "single_positive": 96,
        "multi_positive": 24,
        "negative": 36,
        "positive_cases": 120,
        "positive_label_assignments": 144,
        "dataset_sha256": dataset_sha,
        "system_freeze_head": SYSTEM_FREEZE_HEAD,
        "semantic_parser_model": MODEL,
        "semantic_parser_enabled": True,
        "semantic_screening_enabled": False,
        "label_status": "AI_ASSISTED_FROZEN_VALIDATION",
        "evaluation_status": "FROZEN",
        "quality_report": "quality_report.json",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dataset_sha256": dataset_sha, "cases": 156, "quality": "PASS"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
