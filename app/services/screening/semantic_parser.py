"""Server-side semantic claim parsing with deterministic evidence and safety gates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings, get_settings
from app.services.screening.engine import FindingCandidate, claim_is_excepted_at_raw_span
from app.services.screening.normalization import normalize_marketing_text
from app.services.screening.rules import MarketingRuleSet, load_ruleset
from app.services.screening.segmenter import SegmentCandidate

SEMANTIC_PARSER_VERSION = "semantic_claim_parser_v1"
SEMANTIC_PARSER_CONFIDENCE_THRESHOLD = 0.72

TaxonomyId = Literal[
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

TAXONOMY_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "absolute_or_superlative_claim",
        "无客观限定地宣称绝对、必然、唯一、最佳、最高、第一或百分之百等营销结论。",
    ),
    (
        "concealment_or_minimization_of_exclusions",
        "隐瞒、淡化或否认免责、除外责任、健康告知及不保事项对保障或理赔的影响。",
    ),
    (
        "extra_contractual_benefit",
        "为促成消费者投保，承诺保险合同之外的返现、礼品、服务或其他额外利益。",
    ),
    (
        "false_promotion_or_prize",
        "以虚假或误导性的限时促销、免费名额、抽奖、保证中奖或夸大奖品诱导投保。",
    ),
    (
        "guaranteed_return_or_principal",
        "明确保证固定收益、必然获利、本金安全、保本保息或到期全额返还。",
    ),
    (
        "improper_comparison_or_ranking",
        "无充分依据地贬低其他机构或宣称产品在收益、安全、保障等方面排名或全面优于竞品。",
    ),
    (
        "misleading_interest_or_yield",
        "夸大收益水平，或把演示、预期、历史利率表述为实际、持续或确定的回报。",
    ),
    (
        "no_risk_or_no_loss",
        "明确声称产品没有风险、不会亏损、不会损失或资金绝对安全。",
    ),
    (
        "product_nature_confusion",
        "把保险混同或包装为存款、基金、银行理财、普通储蓄等其他金融产品。",
    ),
    (
        "regulatory_endorsement",
        "声称监管机构认可、认证、推荐、指定、站台或为产品收益和安全性担保。",
    ),
    (
        "surrender_or_cash_value_misstatement",
        "误导退保损失、退保金额、现金价值、费用或承诺随时退保可全额返还。",
    ),
    (
        "waiting_or_cooling_period_misstatement",
        "错误说明、取消、缩短或否认等待期、犹豫期及其保障、退保效果。",
    ),
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticClaim(_StrictModel):
    candidate_rule: TaxonomyId
    source_quote: str = Field(min_length=1, max_length=500)
    actor: Literal["insurer", "salesperson", "regulator", "consumer", "other", "unknown"]
    audience: Literal["consumer", "salesperson", "internal_staff", "general_public", "unknown"]
    beneficiary: Literal["consumer", "salesperson", "insurer", "other", "none", "unknown"]
    claim_type: Literal[
        "absolute_or_superlative",
        "exclusions_or_liability_limitation",
        "extra_benefit_or_gift",
        "promotion_lottery_or_prize",
        "return_or_principal_guarantee",
        "comparison_or_ranking",
        "interest_or_yield",
        "risk_or_loss",
        "product_nature",
        "regulatory_endorsement",
        "surrender_or_cash_value",
        "waiting_or_cooling_period",
    ]
    semantic_features: list[
        Literal[
            "comparison",
            "return_or_yield",
            "guarantee",
            "principal_preservation",
            "risk_minimization",
            "extra_benefit",
            "gift",
            "promotion",
            "lottery",
            "prize",
            "product_nature",
            "regulatory_endorsement",
            "surrender",
            "cash_value",
            "waiting_period",
            "cooling_period",
            "exclusions",
            "liability_limitation",
        ]
    ] = Field(default_factory=list, max_length=8)
    assertion: str = Field(min_length=1, max_length=500)
    negated: bool
    conditionality: Literal["none", "conditional", "unclear"]
    educational_or_prohibitive_context: bool
    role_ambiguity: bool
    semantic_ambiguity: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class SemanticClaimOutput(_StrictModel):
    claims: list[SemanticClaim] = Field(default_factory=list, max_length=24)


@dataclass(frozen=True)
class SemanticParserProviderResponse:
    raw_json: str
    provider_calls: int = 1


class SemanticParserError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, provider_calls: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.provider_calls = provider_calls


class SemanticParserProvider(Protocol):
    def generate(self, raw_text: str) -> SemanticParserProviderResponse: ...


class OpenAICompatibleSemanticParserProvider:
    """One-call OpenAI-compatible adapter using the existing controlled-LLM settings."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport

    def generate(self, raw_text: str) -> SemanticParserProviderResponse:
        if not self._configured():
            raise SemanticParserError("provider_not_configured")
        payload = self._request_payload(raw_text)
        try:
            with httpx.Client(
                timeout=self.settings.llm_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    self._endpoint(),
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise SemanticParserError("provider_timeout", retryable=True, provider_calls=1) from exc
        except httpx.RequestError as exc:
            raise SemanticParserError(
                "provider_network_error", retryable=True, provider_calls=1
            ) from exc
        if response.status_code == 429:
            raise SemanticParserError("provider_rate_limited", retryable=True, provider_calls=1)
        if response.status_code >= 500:
            raise SemanticParserError("provider_http_5xx", retryable=True, provider_calls=1)
        if response.is_error:
            raise SemanticParserError("provider_http_error", provider_calls=1)
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise SemanticParserError(
                "provider_invalid_envelope", retryable=True, provider_calls=1
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise SemanticParserError("provider_invalid_envelope", retryable=True, provider_calls=1)
        return SemanticParserProviderResponse(raw_json=content)

    def _configured(self) -> bool:
        return (
            self.settings.llm_enabled
            and self.settings.llm_provider == "openai_compatible"
            and bool(self.settings.llm_api_key)
            and bool(self.settings.llm_model)
            and _is_https_url(self.settings.llm_base_url)
        )

    def _endpoint(self) -> str:
        base_url = self.settings.llm_base_url.rstrip("/")
        return (
            base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
        )

    def _request_payload(self, raw_text: str) -> dict[str, object]:
        taxonomy = [
            {"rule_id": rule_id, "definition": definition}
            for rule_id, definition in TAXONOMY_DEFINITIONS
        ]
        instruction = (
            "你是保险营销语义 Claim Parser，不作违法结论。只输出一个严格 JSON 对象，不要 Markdown。"
            "输入仅含原始营销文本、既有12类语义定义和输出 schema；不得索取或使用监管知识、"
            "Citation、EvidenceLink、Finding ID、severity 或最终合规结论。"
            "对每个可能相关的语义主张输出 claim。source_quote 必须从"
            " original_marketing_text 逐字复制为连续片段，不得改写、补字、纠错或规范化；"
            "若同一短语重复，应扩展为能唯一定位的完整原文片段，无法唯一定位则不要输出。"
            "actor/audience/beneficiary 必须按实际角色填写。"
            "面向消费者的投保赠品 beneficiary=consumer；"
            "销售人员业绩奖励 beneficiary=salesperson；无法判断受益人时 beneficiary=unknown 且"
            "role_ambiguity=true。否定、限定、风险教育、监管禁止、引用或批判性语境仍可作为候选输出，"
            "但必须如实设置 negated、educational_or_prohibitive_context、role_ambiguity、"
            "semantic_ambiguity 和 conditionality，绝不能把它们改写成肯定营销主张。"
            "confidence 表示 candidate_rule 与原文主张语义的把握，不表示违法概率。"
        )
        return {
            "model": self.settings.llm_model,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "original_marketing_text": raw_text,
                            "taxonomy": taxonomy,
                            "output_schema": SemanticClaimOutput.model_json_schema(),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }


@dataclass(frozen=True)
class SemanticParserOutcome:
    candidates: tuple[FindingCandidate, ...]
    diagnostics: dict[str, object]


class SemanticClaimParser:
    """Fail-closed parser/validator/fusion boundary before RiskFinding persistence."""

    def __init__(
        self,
        ruleset: MarketingRuleSet | None = None,
        settings: Settings | None = None,
        provider: SemanticParserProvider | None = None,
    ) -> None:
        self.ruleset = ruleset or load_ruleset()
        self.rules = {rule.rule_id: rule for rule in self.ruleset.rules}
        self.settings = settings or get_settings()
        if self.settings.semantic_parser_enabled and set(self.rules) != {
            rule_id for rule_id, _ in TAXONOMY_DEFINITIONS
        }:
            raise SemanticParserError("taxonomy_ruleset_mismatch")
        self.provider = provider or OpenAICompatibleSemanticParserProvider(self.settings)

    def supplement(
        self,
        *,
        raw_text: str,
        material_sha256: str,
        segments: list[SegmentCandidate],
        deterministic: list[FindingCandidate],
    ) -> SemanticParserOutcome:
        diagnostics: dict[str, object] = {
            "version": SEMANTIC_PARSER_VERSION,
            "enabled": self.settings.semantic_parser_enabled,
            "status": "disabled",
            "confidence_threshold": SEMANTIC_PARSER_CONFIDENCE_THRESHOLD,
            "provider_calls": 0,
            "retries": 0,
            "model_candidates": 0,
            "semantic_supplements": 0,
            "rejected_candidates": 0,
            "reject_reasons": {},
            "deterministic_plus_semantic": 0,
            "hallucinated_quote_accepted": 0,
        }
        if not self.settings.semantic_parser_enabled:
            return SemanticParserOutcome((), diagnostics)

        output: SemanticClaimOutput | None = None
        failure_code: str | None = None
        provider_calls = 0
        retries = 0
        for attempt in range(2):
            try:
                response = self.provider.generate(raw_text)
                provider_calls += response.provider_calls
                output = SemanticClaimOutput.model_validate_json(response.raw_json)
                break
            except ValidationError:
                failure_code = "schema_invalid"
                if attempt == 0:
                    retries = 1
                    continue
            except SemanticParserError as exc:
                provider_calls += exc.provider_calls
                failure_code = exc.code
                if attempt == 0 and exc.retryable:
                    retries = 1
                    continue
            except Exception:
                failure_code = "provider_internal_error"
            break

        diagnostics["provider_calls"] = provider_calls
        diagnostics["retries"] = retries

        if output is None:
            diagnostics["status"] = "failed"
            diagnostics["failure_code"] = failure_code or "provider_failed"
            return SemanticParserOutcome((), diagnostics)

        accepted, validation = self._validate_and_fuse(
            output=output,
            raw_text=raw_text,
            material_sha256=material_sha256,
            segments=segments,
            deterministic=deterministic,
        )
        diagnostics.update(validation)
        diagnostics["status"] = "completed"
        return SemanticParserOutcome(tuple(accepted), diagnostics)

    def _validate_and_fuse(
        self,
        *,
        output: SemanticClaimOutput,
        raw_text: str,
        material_sha256: str,
        segments: list[SegmentCandidate],
        deterministic: list[FindingCandidate],
    ) -> tuple[list[FindingCandidate], dict[str, object]]:
        accepted: list[FindingCandidate] = []
        reasons: Counter[str] = Counter()
        deterministic_plus_semantic = 0
        for claim in output.claims:
            resolved = _resolve_unique_quote(raw_text, claim.source_quote)
            if resolved is None:
                reasons["quote_not_found"] += 1
                continue
            if resolved == (-1, -1):
                reasons["quote_ambiguous"] += 1
                continue
            start, end = resolved
            containing = sorted(
                (
                    segment
                    for segment in segments
                    if segment.raw_start_offset <= start and end <= segment.raw_end_offset
                ),
                key=lambda segment: segment.ordinal,
            )
            if not containing or raw_text[start:end] != claim.source_quote:
                reasons["quote_not_found"] += 1
                continue
            if claim.negated:
                reasons["negation"] += 1
                continue
            if claim.educational_or_prohibitive_context:
                reasons["educational_context"] += 1
                continue
            if claim.role_ambiguity:
                reasons["role_ambiguity"] += 1
                continue
            if claim.semantic_ambiguity:
                reasons["semantic_ambiguity"] += 1
                continue
            if _is_internal_sales_incentive_context(
                raw_text,
                claim.candidate_rule,
                start,
                end,
            ):
                reasons["role_ambiguity"] += 1
                continue
            if claim.confidence < SEMANTIC_PARSER_CONFIDENCE_THRESHOLD:
                reasons["confidence"] += 1
                continue
            rule = self.rules[claim.candidate_rule]
            if claim_is_excepted_at_raw_span(raw_text, start, end, rule):
                reasons["negation"] += 1
                continue
            if claim.candidate_rule == "extra_contractual_benefit" and not (
                claim.beneficiary == "consumer" and claim.audience in {"consumer", "general_public"}
            ):
                reasons["role_ambiguity"] += 1
                continue
            if _overlaps_rule(deterministic, claim.candidate_rule, start, end):
                reasons["duplicate"] += 1
                deterministic_plus_semantic += 1
                continue
            if _overlaps_rule(accepted, claim.candidate_rule, start, end):
                reasons["duplicate"] += 1
                continue
            normalized = normalize_marketing_text(claim.source_quote).text
            payload = {
                "version": SEMANTIC_PARSER_VERSION,
                "material_sha256": material_sha256,
                "ruleset_sha256": self.ruleset.sha256,
                "rule_id": rule.rule_id,
                "raw_start_offset": start,
                "raw_end_offset": end,
                "normalized_match": normalized,
            }
            accepted.append(
                FindingCandidate(
                    segment_ordinal=containing[0].ordinal,
                    rule_id=rule.rule_id,
                    category=rule.category,
                    severity=rule.severity,
                    signal_strength=rule.signal_strength,
                    matched_text=claim.source_quote,
                    raw_start_offset=start,
                    raw_end_offset=end,
                    normalized_match=normalized,
                    explanation=rule.explanation_template,
                    review_question=rule.review_question_template,
                    remediation=rule.institution_remediation_template,
                    consumer_notice=rule.consumer_notice_template,
                    finding_sha256=_sha(payload),
                )
            )
        accepted.sort(
            key=lambda item: (
                item.raw_start_offset,
                item.raw_end_offset,
                item.rule_id,
                item.finding_sha256,
            )
        )
        rejected = sum(reasons.values())
        return accepted, {
            "model_candidates": len(output.claims),
            "semantic_supplements": len(accepted),
            "rejected_candidates": rejected,
            "reject_reasons": dict(sorted(reasons.items())),
            "deterministic_plus_semantic": deterministic_plus_semantic,
            "hallucinated_quote_accepted": 0,
        }


def _resolve_unique_quote(raw_text: str, quote: str) -> tuple[int, int] | None:
    first = raw_text.find(quote)
    if first < 0:
        return None
    if raw_text.find(quote, first + 1) >= 0:
        return (-1, -1)
    return first, first + len(quote)


def _overlaps_rule(candidates: list[FindingCandidate], rule_id: str, start: int, end: int) -> bool:
    return any(
        item.rule_id == rule_id
        and max(item.raw_start_offset, start) < min(item.raw_end_offset, end)
        for item in candidates
    )


def _is_internal_sales_incentive_context(
    raw_text: str,
    rule_id: str,
    start: int,
    end: int,
) -> bool:
    """Fail closed when gifts/promotions are embedded in staff-performance context."""
    if rule_id not in {"extra_contractual_benefit", "false_promotion_or_prize"}:
        return False
    internal_markers = (
        "佣金",
        "销售业绩",
        "业绩达到",
        "业绩累计",
        "销售指标",
        "考核指标",
        "季度任务",
        "月度任务",
    )
    context = raw_text[max(0, start - 48) : min(len(raw_text), end + 24)]
    return any(marker in context for marker in internal_markers)


def _sha(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_https_url(value: str) -> bool:
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        return False
    return url.scheme == "https" and bool(url.host) and not bool(url.username or url.password)
