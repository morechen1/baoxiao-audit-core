# ruff: noqa: E501
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.models import FindingEvidenceLink, RiskFinding
from app.services.screening import load_ruleset
from app.services.screening.hybrid import _semantic_run_payload_hash
from app.services.screening.semantic import (
    OpenAICompatibleSemanticScreeningProvider,
    SemanticCitation,
    SemanticOutputValidator,
    SemanticScreeningError,
    TrustedRAGSemanticScreeningService,
    _literal_occurrences,
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
        "citation_key": "S001",
        "cited_quote": "保险营销不得承诺保证收益、保本保息或无风险。",
        "confidence": "high",
        "uncertainty": "none",
        "claim_polarity": "affirmative_marketing_claim",
    }
    candidate.update(overrides)
    return json.dumps({"candidates": [candidate]}, ensure_ascii=False)


def _validator() -> SemanticOutputValidator:
    ruleset = load_ruleset()
    return SemanticOutputValidator({rule.rule_id: rule for rule in ruleset.rules})


def _segments(raw: str = RAW) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=1, raw_start_offset=0, raw_end_offset=len(raw))]


def _validate(
    raw_json: str,
    *,
    raw_text: str = RAW,
    deterministic: set[tuple[str, int, int]] | None = None,
    segments: list[SimpleNamespace] | None = None,
) -> list[object]:
    return _validator().validate(
        raw_json=raw_json,
        raw_text=raw_text,
        citations=_citations(),
        deterministic=deterministic or set(),
        segments=segments or _segments(raw_text),
    )


def test_semantic_validator_accepts_only_exact_high_confidence_affirmative_candidate() -> None:
    accepted = _validate(_payload())
    assert [item.candidate.rule_id for item in accepted] == ["guaranteed_return_or_principal"]
    assert accepted[0].start_offset == START
    assert accepted[0].end_offset == START + len(MATCHED)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"rule_id": "invented_risk"}, "taxonomy_rejected"),
        ({"cited_quote": "伪造引用"}, "citation_quote_invalid"),
        ({"citation_key": "S002"}, "citation_key_invalid"),
        ({"matched_text": "本金安全"}, "matched_text_not_found"),
        ({"confidence": "low"}, "confidence_rejected"),
        ({"claim_polarity": "negative_or_warning"}, "polarity_rejected"),
        ({"uncertainty": "limited"}, "uncertainty_rejected"),
    ],
)
def test_semantic_validator_fails_closed_for_invalid_candidate(
    overrides: dict[str, object], code: str
) -> None:
    with pytest.raises(SemanticScreeningError, match=code):
        _validate(_payload(**overrides))


def test_semantic_validator_rejects_bad_schema_span_and_deterministic_duplicate() -> None:
    with pytest.raises(SemanticScreeningError, match="schema_invalid"):
        _validate("not json")
    with pytest.raises(SemanticScreeningError, match="schema_invalid"):
        _validate(_payload(start_offset=START))
    with pytest.raises(SemanticScreeningError, match="deterministic_duplicate"):
        _validate(
            _payload(),
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
    with pytest.raises(SemanticScreeningError, match="non_affirmative_context"):
        _validate(_payload(matched_text=matched), raw_text=raw)


def test_semantic_validator_rejects_not_found_and_ambiguous_matched_text_fail_closed() -> None:
    with pytest.raises(SemanticScreeningError, match="matched_text_not_found"):
        _validate(_payload(matched_text="不存在的宣传"))
    ambiguous = "本金绝对安全；请注意，本金绝对安全并非合同承诺。"
    with pytest.raises(SemanticScreeningError, match="matched_text_ambiguous"):
        _validate(_payload(), raw_text=ambiguous)


def test_literal_occurrence_resolver_includes_overlapping_python_str_matches() -> None:
    assert _literal_occurrences("aaaa", "aa") == [0, 1, 2]


def test_semantic_validator_requires_exactly_one_containing_segment() -> None:
    with pytest.raises(SemanticScreeningError, match="span_segment_not_found"):
        _validate(_payload(), segments=[SimpleNamespace(id=1, raw_start_offset=0, raw_end_offset=2)])
    with pytest.raises(SemanticScreeningError, match="span_segment_ambiguous"):
        _validate(
            _payload(),
            segments=[
                SimpleNamespace(id=1, raw_start_offset=0, raw_end_offset=len(RAW)),
                SimpleNamespace(id=2, raw_start_offset=0, raw_end_offset=len(RAW)),
            ],
        )


def test_semantic_validator_rejects_semantic_duplicate_and_records_safe_diagnostic() -> None:
    raw_json = json.dumps({"candidates": [json.loads(_payload())["candidates"][0]] * 2})
    with pytest.raises(SemanticScreeningError, match="semantic_duplicate") as exc_info:
        _validate(raw_json)
    diagnostic = exc_info.value.diagnostic
    assert diagnostic["candidate_index"] == 1
    assert diagnostic["resolved_start_offset"] == START
    assert "raw_text" not in diagnostic
    assert "matched_text" not in diagnostic
    assert len(str(diagnostic["raw_text_sha256"])) == 64


def test_semantic_validator_rejects_extra_provider_owned_offset_and_evidence_fields() -> None:
    with pytest.raises(SemanticScreeningError, match="schema_invalid"):
        _validate(_payload(start_offset=START))
    with pytest.raises(SemanticScreeningError, match="schema_invalid"):
        _validate(_payload(evidence_assessment="不得由模型提供"))


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
    system_message = captured["messages"][0]["content"]
    schema = json.loads(captured["messages"][1]["content"])["output_schema"]
    assert "JSON" in system_message
    assert "offset" in system_message
    assert "start_offset" not in schema["$defs"]["SemanticCandidate"]["properties"]
    assert "end_offset" not in schema["$defs"]["SemanticCandidate"]["properties"]
    assert "evidence_assessment" not in schema["$defs"]["SemanticCandidate"]["properties"]


def test_semantic_payload_hash_binds_sorted_semantic_finding_projection() -> None:
    first = SimpleNamespace(
        finding_sha256="a" * 64,
        rule_id="guaranteed_return_or_principal",
        raw_start_offset=3,
        raw_end_offset=9,
        evidence_status="supported",
    )
    second = SimpleNamespace(
        finding_sha256="b" * 64,
        rule_id="no_risk_or_no_loss",
        raw_start_offset=1,
        raw_end_offset=2,
        evidence_status="supported",
    )
    assert _semantic_run_payload_hash("c" * 64, [first, second]) == _semantic_run_payload_hash(
        "c" * 64, [second, first]
    )
    assert _semantic_run_payload_hash("c" * 64, [first]) != _semantic_run_payload_hash(
        "d" * 64, [first]
    )


def test_semantic_enrich_returns_only_successfully_persisted_supplements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Provider:
        def generate(self, **_: object) -> str:
            return _payload()

    class RecordingSession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, value: object) -> None:
            self.added.append(value)

        def flush(self) -> None:
            for value in self.added:
                if isinstance(value, RiskFinding) and value.id is None:
                    value.id = 1

    chunk = SimpleNamespace(
        id=1,
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        source_document_id=1,
        record_type="regulation",
        chunk_kind="article",
        pilot_id=None,
        title="可信监管依据",
        source_url="https://example.test/rule",
        authenticity_status="verified_public",
        review_status="approved",
        source_locator_json={},
        evidence_reference_json=[],
    )
    citation = SemanticCitation(
        "S001", chunk, "保险营销不得承诺保证收益、保本保息或无风险。", 1, 1.0
    )
    service = TrustedRAGSemanticScreeningService(
        settings=Settings(semantic_screening_enabled=True), provider=Provider()
    )
    monkeypatch.setattr(service, "_trusted_citations", lambda *_: {"S001": citation})
    run = SimpleNamespace(
        id=1,
        findings=[],
        material=SimpleNamespace(raw_text=RAW, segments=[SimpleNamespace(id=1, ordinal=0, raw_start_offset=0, raw_end_offset=len(RAW))]),
    )
    session = RecordingSession()

    assert service.enrich(session, run) == 1
    assert sum(isinstance(item, RiskFinding) for item in session.added) == 1
    assert sum(isinstance(item, FindingEvidenceLink) for item in session.added) == 1
