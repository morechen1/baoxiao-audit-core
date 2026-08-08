# ruff: noqa: E501
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.services.screening import load_ruleset
from app.services.screening.semantic import (
    OpenAICompatibleSemanticScreeningProvider,
    SemanticCitation,
    SemanticOutputValidator,
    SemanticScreeningError,
)

RAW = "现在投保，本金绝对安全，收益稳定可达8%。"
MATCHED = "本金绝对安全"
START = RAW.index(MATCHED)


def _citations() -> dict[str, SemanticCitation]:
    return {
        "S001": SemanticCitation(
            "S001", SimpleNamespace(), "保险营销不得承诺保证收益、保本保息或无风险。", 1, 1.0
        )
    }


def _payload(**overrides: object) -> str:
    candidate = {
        "rule_id": "guaranteed_return_or_principal",
        "matched_text": MATCHED,
        "start_offset": START,
        "end_offset": START + len(MATCHED),
        "citation_key": "S001",
        "cited_quote": "保险营销不得承诺保证收益、保本保息或无风险。",
        "evidence_assessment": "该监管依据直接约束所述保本营销主张。",
        "confidence": "high",
        "uncertainty": "none",
        "claim_polarity": "affirmative_marketing_claim",
    }
    candidate.update(overrides)
    return json.dumps({"candidates": [candidate]}, ensure_ascii=False)


def _validator() -> SemanticOutputValidator:
    ruleset = load_ruleset()
    return SemanticOutputValidator({rule.rule_id: rule for rule in ruleset.rules})


def test_semantic_validator_accepts_only_exact_high_confidence_affirmative_candidate() -> None:
    accepted = _validator().validate(
        raw_json=_payload(), raw_text=RAW, citations=_citations(), deterministic=set()
    )
    assert [item.rule_id for item in accepted] == ["guaranteed_return_or_principal"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"rule_id": "invented_risk"},
        {"cited_quote": "伪造引用"},
        {"citation_key": "S002"},
        {"matched_text": "本金安全", "end_offset": START + len("本金安全")},
        {"confidence": "low"},
        {"claim_polarity": "negative_or_warning"},
        {"uncertainty": "limited"},
    ],
)
def test_semantic_validator_fails_closed_for_invalid_candidate(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(SemanticScreeningError, match="validation_rejected"):
        _validator().validate(
            raw_json=_payload(**overrides),
            raw_text=RAW,
            citations=_citations(),
            deterministic=set(),
        )


def test_semantic_validator_rejects_bad_schema_span_and_deterministic_duplicate() -> None:
    with pytest.raises(SemanticScreeningError, match="schema_invalid"):
        _validator().validate(
            raw_json="not json", raw_text=RAW, citations=_citations(), deterministic=set()
        )
    with pytest.raises(SemanticScreeningError, match="validation_rejected"):
        _validator().validate(
            raw_json=_payload(start_offset=0, end_offset=len(MATCHED)),
            raw_text=RAW,
            citations=_citations(),
            deterministic=set(),
        )
    with pytest.raises(SemanticScreeningError, match="validation_rejected"):
        _validator().validate(
            raw_json=_payload(),
            raw_text=RAW,
            citations=_citations(),
            deterministic={("guaranteed_return_or_principal", START, START + len(MATCHED))},
        )


@pytest.mark.parametrize(
    ("raw", "matched"),
    [
        ("本产品不保证本金，也不承诺固定收益。", "保证本金"),
        ("不得承诺保证收益。", "保证收益"),
        ("保险产品不是银行存款。", "银行存款"),
        ("退保可能产生损失。", "退保"),
    ],
)
def test_semantic_validator_rejects_negative_or_corrective_context(raw: str, matched: str) -> None:
    start = raw.index(matched)
    with pytest.raises(SemanticScreeningError, match="validation_rejected"):
        _validator().validate(
            raw_json=_payload(
                matched_text=matched,
                start_offset=start,
                end_offset=start + len(matched),
            ),
            raw_text=raw,
            citations=_citations(),
            deterministic=set(),
        )


def test_semantic_provider_uses_deepseek_non_thinking_json_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]}
        )

    provider = OpenAICompatibleSemanticScreeningProvider(
        Settings(
            llm_enabled=True,
            llm_provider="openai_compatible",
            llm_base_url="https://api.deepseek.com",
            llm_api_key="test-secret",
            llm_model="deepseek-v4-flash",
        ),
        transport=httpx.MockTransport(handler),
    )
    assert (
        provider.generate(raw_text=RAW, taxonomy=[{"rule_id": "x", "category": "y"}], citations=[])
        == '{"candidates": []}'
    )
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["max_tokens"] == 4096
