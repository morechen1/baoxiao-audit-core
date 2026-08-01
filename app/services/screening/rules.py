from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.exceptions import ScreeningError

SupportType = Literal["normative_basis", "enforcement_example", "product_term_context"]


class EvidenceMatcher(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_chunk_kinds: tuple[str, ...] = Field(min_length=1)
    required_any_patterns: tuple[str, ...] = Field(min_length=1)
    required_all_pattern_groups: tuple[tuple[str, ...], ...] = ()
    required_all_pattern_groups_scope: Literal["evidence_text", "local_clause"] = "evidence_text"
    required_evidence_fields: tuple[str, ...] = Field(min_length=1)

    @field_validator("required_any_patterns")
    @classmethod
    def patterns_compile(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        try:
            for value in values:
                re.compile(value)
        except re.error as exc:
            raise ValueError("invalid evidence matcher regex") from exc
        return values

    @field_validator("required_all_pattern_groups")
    @classmethod
    def groups_compile(cls, values: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
        try:
            for group in values:
                if not group:
                    raise ValueError("evidence matcher group cannot be empty")
                for pattern in group:
                    re.compile(pattern)
        except re.error as exc:
            raise ValueError("invalid evidence matcher regex") from exc
        return values


class MarketingRiskRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,119}$")
    version: str = Field(min_length=1, max_length=40)
    category: str = Field(min_length=1, max_length=120)
    severity: Literal["high", "medium", "low"]
    signal_strength: Literal["strong", "medium", "weak"]
    positive_patterns: tuple[str, ...] = Field(min_length=1)
    exception_patterns: tuple[str, ...] = ()
    required_context_patterns: tuple[str, ...] = ()
    context_scope: Literal["local_clause"] = "local_clause"
    context_max_distance: int = Field(default=80, ge=0, le=240)
    required_context_allow_format_newline: bool = False
    exception_scope: Literal["local_clause"] = "local_clause"
    claim_negation_scope_version: Literal["marketing_claim_negation_scope_v1"] = (
        "marketing_claim_negation_scope_v1"
    )
    adversative_boundaries: tuple[str, ...] = (
        "实际销售人员仍称",
        "销售人员仍称",
        "营销人员承诺",
        "另一个产品",
        "另一个账户",
        "但",
        "但是",
        "然而",
        "另一个",
        "另有",
        "同时",
    )
    retrieval_queries: tuple[str, ...] = Field(min_length=1)
    preferred_record_types: tuple[Literal["regulation", "penalty", "product_document"], ...]
    evidence_requirements: tuple[SupportType, ...]
    evidence_matchers: dict[SupportType, EvidenceMatcher]
    explanation_template: str = Field(min_length=1)
    review_question_template: str = Field(min_length=1)
    institution_remediation_template: str = Field(min_length=1)
    consumer_notice_template: str = Field(min_length=1)

    @field_validator("positive_patterns", "exception_patterns", "required_context_patterns")
    @classmethod
    def patterns_compile(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        try:
            for value in values:
                re.compile(value)
        except re.error as exc:
            raise ValueError("invalid regex") from exc
        return values

    @model_validator(mode="after")
    def evidence_matchers_cover_requirements(self) -> MarketingRiskRule:
        if set(self.evidence_matchers) != set(self.evidence_requirements):
            raise ValueError("evidence matchers must exactly cover requirements")
        return self


class MarketingRuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ruleset_version: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    rules: tuple[MarketingRiskRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_and_ordered(self) -> MarketingRuleSet:
        identifiers = [rule.rule_id for rule in self.rules]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate rule_id")
        if identifiers != sorted(identifiers):
            raise ValueError("rules must be sorted by rule_id")
        return self

    @property
    def sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@lru_cache(maxsize=1)
def load_ruleset() -> MarketingRuleSet:
    path = Path(__file__).parents[2] / "rules" / "insurance_marketing_rules_v1.json"
    try:
        return MarketingRuleSet.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScreeningError("screening_ruleset_invalid") from exc
