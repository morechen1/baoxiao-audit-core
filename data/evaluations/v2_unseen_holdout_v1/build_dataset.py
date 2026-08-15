from __future__ import annotations

import hashlib
import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
JSONL = HERE / "v2_unseen_holdout_v1.jsonl"
JSON_FILE = HERE / "v2_unseen_holdout_v1.json"
MANIFEST = HERE / "manifest.json"
QUALITY = HERE / "quality_report.json"

POSITIVES: dict[str, list[tuple[str, str]]] = {
    "absolute_or_superlative_claim": [
        ("explicit", "这项保障对所有投保家庭都绝对适用。"),
        ("paraphrase", "无论收入和年龄如何，选择它必然不会错。"),
        ("colloquial", "买这份准没错，肯定最合适。"),
        ("light_implicit", "任何客户采用该方案都会得到理想保障。"),
        ("multi_sentence", "无需考虑个人差异。这个计划百分之百符合您的需要。"),
        ("context_dependent", "对首次咨询者直接断言，这就是唯一正确的投保决定。"),
        (
            "long_sentence",
            "即使家庭结构和保障缺口完全不同，这一组合也被描述为永远适合每个人的方案。",
        ),
        ("indirect_marketing", "顾问告诉客户，选用该计划一定能够作出正确安排。"),
        ("boundary", "宣传页把该保障称作毫无例外的完美选择。"),
    ],
    "concealment_or_minimization_of_exclusions": [
        ("explicit", "责任免除不会影响赔付，阅读这些条款没有必要。"),
        ("paraphrase", "即使未如实完成健康告知，后续理赔也照常办理。"),
        ("colloquial", "免责那几页别管，出险以后一样给钱。"),
        ("light_implicit", "条款列出的不保范围只是文字说明，实际承保没有限制。"),
        ("multi_sentence", "合同中确有除外事项。但销售时可以告诉客户这些内容不发生作用。"),
        ("context_dependent", "面对既往症咨询时，顾问表示隐瞒病史不会改变核保和赔付。"),
        (
            "long_sentence",
            "材料声称无论事故是否属于责任免除，也无论投保资料是否完整，公司都会按最高标准赔付。",
        ),
        ("indirect_marketing", "代理人向客户解释，免责清单只是手续要求，不会被理赔部门采用。"),
        ("boundary", "宣传册将职业限制说成完全可以忽略的附注。"),
    ],
    "extra_contractual_benefit": [
        ("explicit", "签署保单后，代理人另行转账六百元给投保人。"),
        ("paraphrase", "完成首期缴费即可领取未写入合同的酒店住宿券。"),
        ("colloquial", "今天把单签了，我私下给你送台平板。"),
        ("light_implicit", "新客户除合同利益外还能免费使用一年健身会籍。"),
        ("multi_sentence", "保险责任按条款执行。成交后门店会额外返还一张千元购物卡。"),
        ("context_dependent", "为促成这位客户投保，经理承诺赠送合同外的机场贵宾服务。"),
        (
            "long_sentence",
            "只要本月底前完成投保，消费者便可从销售团队处获得保单中没有约定的现金补助和餐饮券。",
        ),
        ("indirect_marketing", "销售顾问表示，客户购买后可在公司权益之外再领取个人准备的礼物。"),
        ("boundary", "文案把免费家政套餐作为签单后额外兑现的客户福利。"),
    ],
    "false_promotion_or_prize": [
        ("explicit", "投保即可参与活动，并承诺每名参与者都获得一等奖。"),
        ("paraphrase", "广告虚构最后五个免费资格，要求客户立即购买。"),
        ("colloquial", "马上签，人人都能抽到那辆新车。"),
        ("light_implicit", "页面宣称奖池名额已经为每位新投保人预留。"),
        ("multi_sentence", "倒计时只剩三分钟。现在购买就必定获得旅游大奖。"),
        ("context_dependent", "直播间以并不存在的限量赠险资格催促观众成交。"),
        (
            "long_sentence",
            "活动页面声称抽奖结果已由系统提前确定，所有当天投保的客户都将得到价值万元的奖品。",
        ),
        ("indirect_marketing", "主播告诉观众，只要生成保单号码就保证抽中头奖。"),
        ("boundary", "海报用虚假的仅剩一席和必中奖字样制造紧迫感。"),
    ],
    "guaranteed_return_or_principal": [
        ("explicit", "公司保证该保单每年固定取得百分之五回报。"),
        ("paraphrase", "无论投资表现怎样，到期都会原额返还本金并支付既定利息。"),
        ("colloquial", "这笔钱放进来保本还稳赚。"),
        ("light_implicit", "合同之外的介绍承诺账户价值到期只增不减。"),
        ("multi_sentence", "市场收益可能波动。但客户最终拿到的本息金额已经锁死。"),
        ("context_dependent", "对保守型客户，顾问承诺十年后本金和约定利润完整到账。"),
        (
            "long_sentence",
            "宣传材料称即便底层资产发生重大亏损，保险公司仍确保投入本金及每年固定增值全部兑现。",
        ),
        ("indirect_marketing", "代理人明确告诉投保人，这份计划一定保住本金并实现确定盈利。"),
        ("boundary", "收益说明把非保证部分描述为到期必然支付的固定金额。"),
    ],
    "improper_comparison_or_ranking": [
        ("explicit", "本方案的保障范围优于市场上全部同类合同。"),
        ("paraphrase", "其他保险公司的服务水平都比不上我们的安排。"),
        ("colloquial", "同类产品跟这款比起来全都差远了。"),
        ("light_implicit", "宣传页在没有数据来源的情况下称其收益表现领先竞品。"),
        ("multi_sentence", "材料未说明比较口径，却断言本产品的理赔体验胜过各家对手。"),
        ("context_dependent", "销售人员贬低另一家公司，称对方保障明显逊于本产品。"),
        (
            "long_sentence",
            "在未列明样本、时间区间和评价标准的情况下，广告声称该计划在价格、保障和服务方面全面超过同业。",
        ),
        ("indirect_marketing", "顾问向客户表示，竞争机构没有任何方案能达到本产品的保障水准。"),
        ("boundary", "海报把未经验证的同业对比结论当作购买理由。"),
    ],
    "misleading_interest_or_yield": [
        ("explicit", "演示中的百分之七年化率就是客户今后实际获得的收益。"),
        ("paraphrase", "上个年度的结算水平会持续复制到整个保险期间。"),
        ("colloquial", "计划书算出多少利息，以后就按这个数拿。"),
        ("light_implicit", "文案将假设情景下的回报率描述为稳定可实现的收入。"),
        ("multi_sentence", "表格只是利益演示。销售人员却称其中数值会逐年真实到账。"),
        ("context_dependent", "用某一高收益年份的数据推断新客户未来长期回报。"),
        (
            "long_sentence",
            "尽管页面脚注标注结果可能变化，正文仍把测算的高年化数字作为客户每年能够取得的实际收益宣传。",
        ),
        ("indirect_marketing", "顾问把历史结算利率介绍成下一保单年度确定执行的利率。"),
        ("boundary", "宣传把预期增值直接等同于可领取的真实利息。"),
    ],
    "no_risk_or_no_loss": [
        ("explicit", "购买该计划不存在任何风险和损失可能。"),
        ("paraphrase", "不论市场发生何种变化，客户投入的资金都不会减少。"),
        ("colloquial", "随便怎么买都亏不了一分钱。"),
        ("light_implicit", "账户被描述为绝对安全且价值永不波动。"),
        ("multi_sentence", "底层资产会随行情变化。不过投保人的钱完全不可能受损。"),
        ("context_dependent", "对担心亏损的客户，代理人断言这是一项零损失安排。"),
        (
            "long_sentence",
            "材料声称无论提前领取、市场下跌还是费用扣除，客户都不承担任何本金减少的可能。",
        ),
        ("indirect_marketing", "销售人员告诉咨询者，该保险从任何角度看都没有风险。"),
        ("boundary", "页面使用资金永远安全来消除客户的风险顾虑。"),
    ],
    "product_nature_confusion": [
        ("explicit", "这份年金险实际上就是银行定期储蓄。"),
        ("paraphrase", "缴纳保费等同于在银行开立一笔普通存款。"),
        ("colloquial", "别把它当保险，就当买了个基金。"),
        ("light_implicit", "销售页面将保险账户包装成随取随用的理财账户。"),
        ("multi_sentence", "合同载明产品属于人身保险。顾问却称客户购买的是存款凭证。"),
        ("context_dependent", "在银行网点把长期寿险介绍为柜台储蓄产品。"),
        (
            "long_sentence",
            "宣传材料刻意隐去保险合同属性，并反复把缴费、领取和账户价值描述成银行存款的存取过程。",
        ),
        ("indirect_marketing", "代理人对客户说，这张保单本质上是一只保本基金而非保险。"),
        ("boundary", "产品介绍把保险费称为理财本金并将保单说成证券账户。"),
    ],
    "regulatory_endorsement": [
        ("explicit", "该保险由金融监管机关官方推荐购买。"),
        ("paraphrase", "监管部门已经为产品品质和表现作出认可。"),
        ("colloquial", "这是监管让我们重点卖给客户的产品。"),
        ("light_implicit", "海报将完成备案解释成获得监管认证。"),
        ("multi_sentence", "产品已经依法报备。宣传因此声称监管机构为其安全性站台。"),
        ("context_dependent", "客户犹豫时，销售人员称该方案属于监管指定项目。"),
        (
            "long_sentence",
            "广告同时使用监管专属、官方认可和权威推荐等措辞，暗示监管机关对具体产品作出信用背书。",
        ),
        ("indirect_marketing", "顾问告诉客户，监管单位已确认这款保险值得购买。"),
        ("boundary", "页面以监管盖章认可作为产品可信度证明。"),
    ],
    "surrender_or_cash_value_misstatement": [
        ("explicit", "客户在任意保单年度退保都可取回全部已交保费。"),
        ("paraphrase", "提前结束合同不会扣除费用，也不会减少可领取金额。"),
        ("colloquial", "哪天不想要了就退，交的钱保证一分不少。"),
        ("light_implicit", "材料宣称首年现金价值与累计保费始终相同。"),
        ("multi_sentence", "合同附有现金价值表。顾问却表示退保时无需查看，直接全额退款。"),
        ("context_dependent", "针对短期资金需求，销售人员承诺三个月后退保没有损失。"),
        (
            "long_sentence",
            "宣传称客户无论何时解除合同，都不会承担退保费用或现金价值不足，并可立即收回此前全部缴费。",
        ),
        ("indirect_marketing", "代理人向投保人保证，中途终止保单仍按本金数额返还。"),
        ("boundary", "退保说明把现金价值错误描述为已交保费的等额余额。"),
    ],
    "waiting_or_cooling_period_misstatement": [
        ("explicit", "本健康险生效后无需等待即可承担疾病责任。"),
        ("paraphrase", "超过犹豫期后仍能无条件撤销合同并退回全部费用。"),
        ("colloquial", "保单一签就全保，不用等上一天。"),
        ("light_implicit", "条款约定六十日等待期，宣传却称第二天即可理赔。"),
        ("multi_sentence", "合同注明十五日犹豫期。销售人员表示半年后反悔也享受相同退款待遇。"),
        ("context_dependent", "向健康状况复杂的客户介绍时，顾问否认产品设有疾病等待期。"),
        (
            "long_sentence",
            "材料把等待期内发生的保险事故描述为当然赔付，并称超过犹豫期限解除合同也不产生任何差异。",
        ),
        ("indirect_marketing", "代理人告诉客户，九十天等待约定不会真正执行。"),
        ("boundary", "页面用即时全额保障否定合同明确载明的等待安排。"),
    ],
}

MULTI_LABEL: list[tuple[str, list[str], str]] = [
    (
        "explicit",
        ["absolute_or_superlative_claim", "improper_comparison_or_ranking"],
        "广告称该计划绝对是同业中保障最完整的产品。",
    ),
    (
        "paraphrase",
        ["absolute_or_superlative_claim", "improper_comparison_or_ranking"],
        "宣传断言任何竞争方案都不可能达到这款保险的最高水准。",
    ),
    (
        "colloquial",
        ["guaranteed_return_or_principal", "no_risk_or_no_loss"],
        "本金肯定拿得回来，而且怎么都不会亏。",
    ),
    (
        "multi_sentence",
        ["guaranteed_return_or_principal", "no_risk_or_no_loss"],
        "公司承诺到期保本付息。整个持有期间也不存在损失风险。",
    ),
    (
        "explicit",
        ["guaranteed_return_or_principal", "misleading_interest_or_yield"],
        "演示的百分之六年化收益已经锁定，并保证每年照数支付。",
    ),
    (
        "context_dependent",
        ["guaranteed_return_or_principal", "misleading_interest_or_yield"],
        "顾问把测算回报说成确定利率，并承诺本金与收益全部兑现。",
    ),
    (
        "colloquial",
        ["absolute_or_superlative_claim", "misleading_interest_or_yield"],
        "这个收益率绝对最高，往后每年都能照着拿。",
    ),
    (
        "long_sentence",
        ["absolute_or_superlative_claim", "misleading_interest_or_yield"],
        "页面把某次高结算结果称为全市场最高水平，并断言该数字必然持续到合同结束。",
    ),
    (
        "explicit",
        ["extra_contractual_benefit", "false_promotion_or_prize"],
        "投保可获合同外礼卡，还保证在抽奖中得到头奖。",
    ),
    (
        "multi_sentence",
        ["extra_contractual_benefit", "false_promotion_or_prize"],
        "签单后另送未写入保单的手机。当天活动还宣称每人必中大奖。",
    ),
    (
        "paraphrase",
        ["product_nature_confusion", "guaranteed_return_or_principal"],
        "顾问把保单说成银行存款，并保证本金和固定利息到期返还。",
    ),
    (
        "indirect_marketing",
        ["product_nature_confusion", "guaranteed_return_or_principal"],
        "代理人称客户买的是储蓄账户，同时承诺绝对保本。",
    ),
    (
        "explicit",
        ["regulatory_endorsement", "guaranteed_return_or_principal"],
        "宣传声称监管机构为该产品背书，并担保客户本金收益。",
    ),
    (
        "context_dependent",
        ["regulatory_endorsement", "guaranteed_return_or_principal"],
        "为打消疑虑，销售人员称监管已保证这份保单按固定回报兑付。",
    ),
    (
        "colloquial",
        ["surrender_or_cash_value_misstatement", "no_risk_or_no_loss"],
        "随时退都原额返钱，所以买进去完全没损失。",
    ),
    (
        "long_sentence",
        ["surrender_or_cash_value_misstatement", "no_risk_or_no_loss"],
        "材料承诺首年退保即可全额取回缴费，并进一步宣称客户因此不承担任何资金损失。",
    ),
    (
        "explicit",
        ["waiting_or_cooling_period_misstatement", "concealment_or_minimization_of_exclusions"],
        "产品没有等待期，合同列明的除外疾病也都会赔付。",
    ),
    (
        "multi_sentence",
        ["waiting_or_cooling_period_misstatement", "concealment_or_minimization_of_exclusions"],
        "顾问说等待期只是形式。责任免除同样不会影响任何理赔。",
    ),
    (
        "paraphrase",
        ["absolute_or_superlative_claim", "no_risk_or_no_loss"],
        "该方案被描述为百分之百安全，资金在任何情况下都不减少。",
    ),
    (
        "boundary",
        ["absolute_or_superlative_claim", "no_risk_or_no_loss"],
        "页面使用绝对可靠和永无亏损来概括产品风险。",
    ),
    (
        "explicit",
        ["product_nature_confusion", "misleading_interest_or_yield"],
        "销售把保险包装成高息存款，并称演示利率就是实际利息。",
    ),
    (
        "multi_sentence",
        ["product_nature_confusion", "misleading_interest_or_yield"],
        "文案称这不是保险而是理财账户。页面上的预期回报会按年真实支付。",
    ),
    (
        "explicit",
        ["regulatory_endorsement", "absolute_or_superlative_claim"],
        "广告宣称这是监管唯一认可、绝对值得购买的保险。",
    ),
    (
        "indirect_marketing",
        ["regulatory_endorsement", "absolute_or_superlative_claim"],
        "顾问称监管已经指定该方案，并断言所有客户选择它都正确。",
    ),
]

NEGATIVES: dict[str, list[str]] = {
    "quoted": [
        "审计记录摘录过往话术：“本产品固定赚钱”，用于说明问题来源。",
        "新闻报道引用投诉人转述：“销售说这是监管特批项目”。",
        "课件展示反面示例“闭眼买也不会亏”，并要求学员识别风险。",
        "调查笔录保留原句“退保能拿回全部保费”，尚待核实事实。",
    ],
    "educational": [
        "消费者课堂说明，保险收益演示不能被理解为确定回报。",
        "培训材料讲解为什么不得把年金保险称作银行储蓄。",
        "风险教育页面提示，责任免除和等待期会影响保障范围。",
        "合规手册用案例说明无依据的同业比较可能误导客户。",
    ],
    "prohibition": [
        "公司制度要求任何人员不得承诺本金或固定收益。",
        "渠道规范禁止使用监管认证具体产品的宣传措辞。",
        "审核意见明确要求删除零风险和绝对安全字样。",
        "销售守则严禁以合同外返现推动客户签单。",
    ],
    "consumer_warning": [
        "如有人宣传必中大奖，请消费者先查验活动规则与真实性。",
        "不要轻信随时退保都无损失的口头介绍。",
        "遇到把保险说成存款的推销，应核对产品合同和性质。",
        "请警惕所谓行业最好或监管指定等未经证实的说法。",
    ],
    "internal_incentive": [
        "分公司向完成续期指标的业务团队发放年终奖金。",
        "员工培训考核排名前三者可领取公司准备的耳机。",
        "渠道经理达到月度管理目标后获得内部积分奖励。",
        "公司为优秀理赔人员安排旅游，与客户购买权益无关。",
    ],
    "comparison_explanation": [
        "报告按相同口径列示三款产品费用，没有给出优劣结论。",
        "本文介绍比较保险责任时应统一保障期限和样本范围。",
        "表格客观呈现不同合同的现金价值，供消费者自行判断。",
        "研究说明排名结论必须有公开数据支持，并未评价具体产品。",
    ],
    "historical": [
        "旧案卷记载某机构曾因夸大历史收益受到监管处罚。",
        "案件回放说明早年有人以虚构奖品诱导客户投保。",
        "往年投诉中出现过代理人淡化健康告知的情况。",
        "处罚通报回顾某销售把保险包装成基金的既往事实。",
    ],
    "negated": [
        "本计划不承诺固定回报，本金与非保证利益可能变化。",
        "本公司不会向投保人提供保单约定之外的礼品或补贴。",
        "产品并非监管推荐项目，备案不代表官方认可。",
        "提前解除合同不能保证全额退款，金额以现金价值为准。",
    ],
    "conditional": [
        "保险金是否给付取决于事故是否符合合同约定责任。",
        "未来结算水平可能高于或低于演示数字，不构成承诺。",
        "投保申请经审核通过后，保障按合同载明日期开始。",
        "犹豫期内解除合同的处理方式以条款和实际申请时间为准。",
    ],
    "role_ambiguity": [
        "便签只写着赠送手表，无法判断对象和使用场景。",
        "会议录音出现收益不错，但说话人身份无法确认。",
        "草案中的官方两字可能是栏目名称，尚未确定是否对外发布。",
        "聊天截图提到奖励计划，未说明是员工政策还是客户活动。",
    ],
    "clarification": [
        "本合同属于人身保险，与存款和基金具有不同法律性质。",
        "产品备案仅是监管程序，不表示监管机关保证其收益。",
        "等待期、责任免除和赔付条件均以正式保险条款为准。",
        "退保所得通常按现金价值计算，可能低于累计缴费。",
    ],
    "compliant": [
        "建议投保人结合保障需求、家庭预算和合同内容审慎选择。",
        "页面数值用于情景说明，非保证利益存在不确定性。",
        "具体保险责任、期限和费用请查阅正式条款及产品说明书。",
        "客户可在充分了解风险和解除合同流程后自主决定是否投保。",
    ],
}


def canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _extract_texts(payload: object) -> set[str]:
    texts: set[str] = set()
    if isinstance(payload, dict):
        value = payload.get("text")
        if isinstance(value, str):
            texts.add(value)
        for child in payload.values():
            texts.update(_extract_texts(child))
    elif isinstance(payload, list):
        for child in payload:
            texts.update(_extract_texts(child))
    return texts


def _prior_texts() -> set[str]:
    texts: set[str] = set()
    for path in ROOT.glob("data/**/*.json*"):
        if path.parent == HERE or any(
            marker in path.name for marker in ("prediction", "summary", "report", "manifest")
        ):
            continue
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            payloads: list[object] = []
            for line in content.splitlines():
                try:
                    payloads.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            texts.update(_extract_texts(payloads))
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        texts.update(_extract_texts(payload))
    return texts


def build() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordinal = 1
    for rule, examples in POSITIVES.items():
        if len(examples) != 9:
            raise RuntimeError(f"single_positive_count_invalid:{rule}")
        for stratum, text in examples:
            rows.append(
                {
                    "case_id": f"V2HOLD-{ordinal:03d}",
                    "split": "UNSEEN_HOLDOUT",
                    "case_type": "single_positive",
                    "stratum": stratum,
                    "context_group": "direct_marketing",
                    "expected_rules": [rule],
                    "text": text,
                }
            )
            ordinal += 1
    for stratum, rules, text in MULTI_LABEL:
        rows.append(
            {
                "case_id": f"V2HOLD-{ordinal:03d}",
                "split": "UNSEEN_HOLDOUT",
                "case_type": "multi_label",
                "stratum": stratum,
                "context_group": "direct_marketing",
                "expected_rules": sorted(rules),
                "text": text,
            }
        )
        ordinal += 1
    for context_group, negative_examples in NEGATIVES.items():
        if len(negative_examples) != 4:
            raise RuntimeError(f"negative_count_invalid:{context_group}")
        for negative_text in negative_examples:
            rows.append(
                {
                    "case_id": f"V2HOLD-{ordinal:03d}",
                    "split": "UNSEEN_HOLDOUT",
                    "case_type": "negative",
                    "stratum": "compliant" if context_group == "compliant" else "hard_negative",
                    "context_group": context_group,
                    "expected_rules": [],
                    "text": negative_text,
                }
            )
            ordinal += 1
    if len(rows) != 180 or len({row["text"] for row in rows}) != 180:
        raise RuntimeError("holdout_cardinality_invalid")
    prior = _prior_texts()
    overlaps = sorted(row["text"] for row in rows if row["text"] in prior)
    if overlaps:
        raise RuntimeError(f"prior_exact_duplicate:{len(overlaps)}")
    return rows


def main() -> None:
    rows = build()
    highest_similarity = 0.0
    closest_pair: tuple[str, str] | None = None
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            score = SequenceMatcher(None, left["text"], right["text"]).ratio()
            if score > highest_similarity:
                highest_similarity = score
                closest_pair = (left["case_id"], right["case_id"])
    near_duplicate_threshold = 0.86
    if highest_similarity >= near_duplicate_threshold:
        raise RuntimeError(f"holdout_near_duplicate:{closest_pair}:{highest_similarity:.4f}")
    prior = _prior_texts()
    highest_prior_similarity = 0.0
    closest_prior_case: str | None = None
    for row in rows:
        for prior_text in prior:
            score = SequenceMatcher(None, row["text"], prior_text).ratio()
            if score > highest_prior_similarity:
                highest_prior_similarity = score
                closest_prior_case = row["case_id"]
    if highest_prior_similarity >= near_duplicate_threshold:
        raise RuntimeError(
            f"prior_near_duplicate:{closest_prior_case}:{highest_prior_similarity:.4f}"
        )
    JSONL.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    JSON_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = canonical_sha(rows)
    per_rule = dict(sorted(Counter(rule for row in rows for rule in row["expected_rules"]).items()))
    manifest = {
        "dataset_id": "v2_unseen_holdout_v1",
        "split": "UNSEEN_HOLDOUT",
        "headline_benchmark": True,
        "system_freeze_head": "08acc0dd210eb022c10c38ce97fe8b16e70558f3",
        "case_count": 180,
        "single_positive_count": 108,
        "multi_label_count": 24,
        "negative_count": 48,
        "per_rule_support": per_rule,
        "context_groups": dict(sorted(Counter(row["context_group"] for row in rows).items())),
        "dataset_sha256": digest,
        "construction_policy": (
            "Constructed only after V2 system freeze; labels frozen before any provider call. "
            "No V1, M6, V2 development, USER, or demo text is reused."
        ),
    }
    quality = {
        "case_count": len(rows),
        "unique_text_count": len({row["text"] for row in rows}),
        "prior_exact_duplicates": 0,
        "within_holdout_exact_duplicates": 0,
        "near_duplicate_threshold": near_duplicate_threshold,
        "highest_within_holdout_similarity": round(highest_similarity, 6),
        "closest_pair": closest_pair,
        "highest_prior_similarity": round(highest_prior_similarity, 6),
        "closest_prior_case": closest_prior_case,
        "near_duplicates": 0,
        "expected_rule_set_complete": all(isinstance(row["expected_rules"], list) for row in rows),
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    QUALITY.write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
