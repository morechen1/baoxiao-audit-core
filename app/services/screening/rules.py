from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.exceptions import ScreeningError


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
    retrieval_queries: tuple[str, ...] = Field(min_length=1)
    preferred_record_types: tuple[Literal["regulation", "penalty", "product_document"], ...]
    evidence_requirements: tuple[
        Literal["normative_basis", "enforcement_example", "product_term_context"], ...
    ]
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
