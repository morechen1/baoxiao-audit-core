from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from app.api.dependencies import get_db
from app.cli.main import app as cli_app
from app.core.exceptions import ExplanationError
from app.main import app as api_app
from app.models import (
    ExplanationArtifact,
    ExplanationRun,
    FindingEvidenceLink,
    KnowledgeChunk,
    MarketingMaterial,
    MaterialSegment,
    RiskFinding,
    ScreeningRun,
    SourceDocument,
)
from app.services.explanation.context import (
    BuiltContext,
    ControlledRAGContextBuilder,
    EvidenceBinding,
)
from app.services.explanation.prompts import (
    canonical_sha256,
    load_prompt,
    load_prompt_registry,
    prompt_sha256,
)
from app.services.explanation.providers import (
    DeterministicFixtureProvider,
    DisabledExternalProvider,
    ProviderGenerationError,
    provider_configuration_sha256,
)
from app.services.explanation.schemas import (
    ControlledEvidence,
    ControlledFinding,
    ControlledRAGContext,
    ExplanationProviderRequest,
    InstitutionExplanationV1,
    PromptDefinition,
)
from app.services.explanation.service import ControlledExplanationService
from app.services.explanation.validators import (
    ControlledExplanationValidator,
    UnsupportedClaimDetectorV1,
)
from app.services.screening.evidence import FindingEvidenceAssembler

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "controlled_rag_eval_v1" / "responses.json"
EVAL_SAMPLES = cast(
    list[dict[str, Any]],
    json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["samples"],
)


def _built_context(id_offset: int = 0) -> BuiltContext:
    evidence_one = ControlledEvidence(
        citation_key="E001",
        support_type="normative_basis",
        source_title="保险销售行为管理办法",
        source_url="https://example.test/regulation",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        quote="不得利用监管机构名义对保险产品作引人误解的宣传",
        source_locator={"article_number": "第十七条"},
        evidence_references=[{"field_name": "article_text", "quote": "不得引人误解"}],
        context_scope="not_applicable",
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
    )
    evidence_two = ControlledEvidence(
        citation_key="E002",
        support_type="product_term_context",
        source_title="示例保险条款",
        source_url="https://example.test/product",
        pilot_id="PROD-001",
        record_type="product_document",
        chunk_kind="exclusions",
        quote="本合同责任免除事项以正式合同约定为准",
        source_locator={"field_name": "exclusions"},
        evidence_references=[{"field_name": "exclusions", "quote": "责任免除"}],
        context_scope="illustrative_not_material_specific",
        chunk_identity_sha256="c" * 64,
        chunk_content_sha256="d" * 64,
    )
    payload = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={
            "title": "构造材料",
            "material_type": "advertisement",
            "input_sha256": "1" * 64,
            "source_label": "constructed_fixture",
        },
        findings=[
            ControlledFinding(
                finding_key="F001",
                rule_id="regulatory_endorsement",
                category="监管背书",
                severity="high",
                signal_strength="strong",
                matched_text="监管推荐",
                raw_start_offset=0,
                raw_end_offset=4,
                deterministic_explanation="该表达可能造成监管背书误解。",
                review_question="是否存在监管背书暗示？",
                evidence_status="partially_supported",
                evidence=[evidence_one],
            ),
            ControlledFinding(
                finding_key="F002",
                rule_id="concealment_or_minimization_of_exclusions",
                category="免责弱化",
                severity="high",
                signal_strength="strong",
                matched_text="没有免责",
                raw_start_offset=5,
                raw_end_offset=9,
                deterministic_explanation="该表达可能弱化责任免除。",
                review_question="是否完整提示责任免除？",
                evidence_status="supported",
                evidence=[evidence_two],
            ),
        ],
    )
    bindings = {
        "E001": EvidenceBinding(
            "F001",
            10 + id_offset,
            20 + id_offset,
            "E001",
            evidence_one.quote,
            evidence_one.chunk_identity_sha256,
            evidence_one.chunk_content_sha256,
            evidence_one.source_url,
            evidence_one.source_locator,
        ),
        "E002": EvidenceBinding(
            "F002",
            11 + id_offset,
            21 + id_offset,
            "E002",
            evidence_two.quote,
            evidence_two.chunk_identity_sha256,
            evidence_two.chunk_content_sha256,
            evidence_two.source_url,
            evidence_two.source_locator,
        ),
    }
    return BuiltContext(payload, canonical_sha256(payload.model_dump(mode="json")), bindings)


def _valid_payload(audience: str) -> tuple[dict[str, Any], PromptDefinition, BuiltContext]:
    prompt = load_prompt(audience)
    built = _built_context()
    response = DeterministicFixtureProvider().generate(
        ExplanationProviderRequest(audience=audience, prompt=prompt, context=built.payload)
    )
    return json.loads(response.raw_json), prompt, built


def _rows(payload: dict[str, Any], audience: str) -> list[dict[str, Any]]:
    key = "finding_explanations" if audience == "institution" else "risk_explanations"
    return cast(list[dict[str, Any]], payload[key])


def _narrative_key(audience: str) -> str:
    return "explanation" if audience == "institution" else "plain_language_explanation"


def _mutate(payload: dict[str, Any], audience: str, scenario: str, prompt: PromptDefinition) -> str:
    rows = _rows(payload, audience)
    first = rows[0]
    second = rows[1]
    narrative = _narrative_key(audience)
    if scenario in {
        "valid",
        "historical_prompt",
        "historical_context",
        "cross_database",
        "deterministic_rerun",
        "all_sources_distinguished",
    }:
        pass
    elif scenario == "continuous_short_quote":
        first["citations"][0]["cited_quote"] = first["citations"][0]["cited_quote"][2:8]
    elif scenario == "unknown_finding":
        first["finding_key"] = "F999"
    elif scenario in {"unknown_citation", "unselected_chunk", "regulatory_case"}:
        first["citations"][0]["citation_key"] = "E999"
    elif scenario == "wrong_finding":
        first["citations"][0] = deepcopy(second["citations"][0])
    elif scenario == "tampered_quote":
        first["citations"][0]["cited_quote"] = "不存在的篡改引文"
    elif scenario == "joined_quote":
        quote = first["citations"][0]["cited_quote"]
        first["citations"][0]["cited_quote"] = quote[:4] + quote[-4:]
    elif scenario == "new_risk":
        extra = deepcopy(first)
        extra["finding_key"] = "F999"
        rows.append(extra)
    elif scenario == "modified_severity":
        first["severity"] = "critical"
    elif scenario == "modified_matched_text":
        first["matched_text"] = "模型新增文本"
    elif scenario == "legal_illegal":
        first[narrative] = "该材料已违法。"
    elif scenario == "legal_fraud":
        first[narrative] = "该材料构成欺诈。"
    elif scenario == "certain_penalty":
        first[narrative] = "该主体必将受到处罚。"
    elif scenario == "advice_buy":
        first[narrative] = "建议购买。"
    elif scenario == "advice_surrender":
        first[narrative] = "建议立即退保。"
    elif scenario == "guaranteed_return":
        first[narrative] = "本解释保证收益。"
    elif scenario == "guaranteed_claim":
        first[narrative] = "保证赔付。"
    elif scenario in {"missing_uncertainty", "insufficient_hidden"}:
        first[narrative] = "该风险已经得到确定证明。"
        if audience == "institution":
            first["evidence_assessment"] = "证据充分。"
    elif scenario == "missing_disclaimer":
        payload["disclaimer"] = ""
    elif scenario == "missing_illustrative":
        for key, value in list(second.items()):
            if isinstance(value, str):
                second[key] = value.replace(prompt.illustrative_product_disclaimer, "")
    elif scenario == "no_citations":
        first["citations"] = []
    elif scenario == "missing_field":
        payload.pop("executive_summary" if audience == "institution" else "overall_notice")
    elif scenario == "extra_field":
        first["unexpected"] = True
    elif scenario == "oversized":
        first[narrative] = "甲" * 4001
    elif scenario == "html_script":
        first[narrative] = "<script>alert(1)</script>"
    elif scenario == "unicode_citation":
        first["citations"][0]["citation_key"] = "E００１"
    elif scenario == "duplicate_citation":
        first["citations"].append(deepcopy(first["citations"][0]))
    elif scenario == "forged_url":
        first["citations"][0]["source_url"] = "https://evil.test"
    elif scenario == "forged_locator":
        first["citations"][0]["source_locator"] = {"page": 999}
    elif scenario == "database_id_citation":
        first["citations"][0]["citation_key"] = "1"
    elif scenario == "markdown":
        return "```json\n{}\n```"
    elif scenario == "empty":
        return ""
    else:
        raise AssertionError(f"unsupported scenario: {scenario}")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@pytest.mark.parametrize("sample", EVAL_SAMPLES, ids=lambda value: value["id"])
def test_controlled_rag_constructed_response_corpus(sample: dict[str, Any]) -> None:
    assert sample["constructed"] is True
    audience = sample["audience"]
    scenario = sample["scenario"]
    expected = sample["expected"]
    prompt = load_prompt(audience)
    built = _built_context()
    request = ExplanationProviderRequest(audience=audience, prompt=prompt, context=built.payload)
    if scenario in {"provider_timeout", "provider_exception"}:
        provider_scenario = "timeout" if scenario == "provider_timeout" else "exception"
        with pytest.raises(ProviderGenerationError, match=expected):
            DeterministicFixtureProvider(provider_scenario).generate(request)
        return
    payload, _, _ = _valid_payload(audience)
    raw = _mutate(payload, audience, scenario, prompt)
    if expected == "passed":
        validated = ControlledExplanationValidator().validate(raw, prompt, built)
        assert validated.output["schema_version"] == prompt.output_schema_version
    else:
        with pytest.raises(ExplanationError) as exc_info:
            ControlledExplanationValidator().validate(raw, prompt, built)
        assert str(exc_info.value) == expected


def test_fixture_corpus_has_required_valid_invalid_balance() -> None:
    assert len(EVAL_SAMPLES) == 47
    assert sum(item["expected"] == "passed" for item in EVAL_SAMPLES) == 12
    assert sum(item["expected"] != "passed" for item in EVAL_SAMPLES) == 35


def test_prompt_registry_has_one_strict_version_per_audience() -> None:
    registry = load_prompt_registry()
    assert {item.audience for item in registry.prompts} == {"institution", "consumer"}
    assert len({item.prompt_version for item in registry.prompts}) == 2


@pytest.mark.parametrize("audience", ["institution", "consumer"])
def test_prompt_sha_is_canonical_and_stable(audience: str) -> None:
    prompt = load_prompt(audience)
    assert prompt_sha256(prompt) == prompt_sha256(prompt.model_copy(deep=True))
    assert len(prompt_sha256(prompt)) == 64


def test_prompt_schema_rejects_unknown_fields() -> None:
    raw = load_prompt("institution").model_dump(mode="json")
    raw["secret_header"] = "Bearer secret"
    with pytest.raises(ValidationError):
        PromptDefinition.model_validate(raw)


def test_prompt_snapshots_contain_no_token_or_password() -> None:
    text = json.dumps(load_prompt_registry().model_dump(mode="json"), ensure_ascii=False).lower()
    assert "api_key" not in text
    assert "authorization" not in text
    assert "password" not in text


def test_context_and_sha_do_not_depend_on_database_primary_keys() -> None:
    first = _built_context(0)
    second = _built_context(100_000)
    assert first.payload == second.payload
    assert first.payload_sha256 == second.payload_sha256
    assert first.bindings["E001"].finding_id != second.bindings["E001"].finding_id


def test_context_contains_no_database_ids_paths_or_regulatory_cases() -> None:
    raw = json.dumps(_built_context().payload.model_dump(mode="json"), ensure_ascii=False)
    assert "finding_id" not in raw
    assert "knowledge_chunk_id" not in raw
    assert "/Users/" not in raw
    assert "regulatory_case" not in raw


def test_fixture_provider_is_deterministic() -> None:
    prompt = load_prompt("institution")
    built = _built_context()
    request = ExplanationProviderRequest(
        audience="institution", prompt=prompt, context=built.payload
    )
    provider = DeterministicFixtureProvider()
    assert provider.generate(request) == provider.generate(request)
    assert provider_configuration_sha256(provider) == provider_configuration_sha256(provider)


def test_disabled_external_provider_fails_with_public_code() -> None:
    prompt = load_prompt("consumer")
    request = ExplanationProviderRequest(
        audience="consumer", prompt=prompt, context=_built_context().payload
    )
    with pytest.raises(ProviderGenerationError, match="explanation_provider_not_configured"):
        DisabledExternalProvider().generate(request)


@pytest.mark.parametrize(
    "text",
    [
        "可能存在风险信号",
        "需要进一步核验",
        "现有证据显示该表述需要复核",
        "当前证据不足以作出结论",
        "该处罚案例可作为相似执法参考",
    ],
)
def test_unsupported_claim_detector_allows_cautious_language(text: str) -> None:
    payload, prompt, _ = _valid_payload("institution")
    payload["finding_explanations"][0]["explanation"] = text
    output = ControlledExplanationValidator().validate(
        json.dumps(payload, ensure_ascii=False), prompt, _built_context()
    )
    assert (
        UnsupportedClaimDetectorV1().detect(InstitutionExplanationV1.model_validate(output.output))
        is None
    )
    assert UnsupportedClaimDetectorV1.version == "unsupported_claim_detector_v1"


def _persist_screening_graph(session: Any) -> tuple[int, BuiltContext]:
    document = SourceDocument(
        data_type="regulation",
        source_url="https://example.test/regulation",
        final_url="https://example.test/regulation",
        source_title="保险销售行为管理办法",
        raw_file_path="raw/regulation.json",
        raw_text="不得利用监管机构名义对保险产品作引人误解的宣传",
        sha256="2" * 64,
        collection_status="collected",
        parse_status="parsed",
        final_review_status="approved",
        authenticity_type="verified_public",
        knowledge_index_status="indexed",
    )
    session.add(document)
    session.flush()
    chunk = KnowledgeChunk(
        source_document_id=document.id,
        record_type="regulation",
        structured_record_id=None,
        pilot_id="REG-001",
        portable_record_key="3" * 64,
        chunk_kind="article_text",
        chunk_ordinal=0,
        title="保险销售行为管理办法",
        text=document.raw_text,
        normalized_text=document.raw_text,
        lexical_tokens="不得 利用 监管 机构 名义",
        authority="金融监管总局",
        authority_filter_text="金融监管总局",
        relevant_date=None,
        source_url=document.source_url,
        source_locator_json={"article_number": "第十七条"},
        evidence_reference_json=[{"field_name": "article_text", "quote": document.raw_text}],
        evidence_quality="A",
        authenticity_status="verified_public",
        review_status="approved",
        source_payload_hash="4" * 64,
        chunk_content_sha256="b" * 64,
        chunk_identity_sha256="a" * 64,
        tokenizer_version="test",
        chunker_version="test",
        ranking_version="test",
        is_active=True,
    )
    material = MarketingMaterial(
        title="构造材料",
        material_type="advertisement",
        raw_text="监管推荐",
        normalized_text="监管推荐",
        normalization_version="test",
        input_sha256="1" * 64,
        source_label="constructed_fixture",
        is_constructed_evaluation=True,
    )
    session.add_all([chunk, material])
    session.flush()
    segment = MaterialSegment(
        material_id=material.id,
        ordinal=0,
        text="监管推荐",
        normalized_text="监管推荐",
        raw_start_offset=0,
        raw_end_offset=4,
        segment_sha256="5" * 64,
        segmenter_version="test",
    )
    session.add(segment)
    session.flush()
    screening = ScreeningRun(
        material_id=material.id,
        ruleset_version="test",
        ruleset_sha256="6" * 64,
        ruleset_snapshot_json={},
        ruleset_snapshot_sha256="6" * 64,
        retrieval_version="test",
        trusted_index_payload_hash="f" * 64,
        status="completed",
        finding_count=1,
        insufficient_evidence_count=0,
        run_payload_sha256="e" * 64,
        evidence_evaluation_summary_json={},
    )
    session.add(screening)
    session.flush()
    finding = RiskFinding(
        screening_run_id=screening.id,
        segment_id=segment.id,
        rule_id="regulatory_endorsement",
        rule_version="1",
        category="监管背书",
        severity="high",
        signal_strength="strong",
        matched_text="监管推荐",
        raw_start_offset=0,
        raw_end_offset=4,
        normalized_match="监管推荐",
        explanation="该表达可能造成监管背书误解。",
        review_question="是否存在监管背书暗示？",
        remediation_template="人工复核",
        consumer_notice_template="核对来源",
        rule_snapshot_json={},
        rule_snapshot_sha256="7" * 64,
        evidence_status="partially_supported",
        finding_sha256="8" * 64,
    )
    session.add(finding)
    session.flush()
    link = FindingEvidenceLink(
        finding_id=finding.id,
        knowledge_chunk_id=chunk.id,
        support_type="normative_basis",
        retrieval_rank=1,
        retrieval_score=10.0,
        chunk_identity_sha256=chunk.chunk_identity_sha256,
        chunk_content_sha256=chunk.chunk_content_sha256,
        source_document_snapshot_json={
            "source_document_id": document.id,
            "record_type": "regulation",
            "chunk_kind": "article_text",
            "pilot_id": "REG-001",
            "title": document.source_title,
            "source_url": document.source_url,
            "authenticity_status": "verified_public",
            "review_status": "approved",
        },
        source_locator_snapshot_json=chunk.source_locator_json,
        evidence_references_snapshot_json=chunk.evidence_reference_json,
        support_evaluation_version="test",
        support_evaluation_passed=True,
        matched_support_patterns=[],
        actual_matched_substrings=[],
        matched_pattern_groups=[],
        matched_evidence_fields=["article_text"],
        support_reason="semantic_evidence_match",
        semantic_support_score=1.0,
        semantic_support_reason="test",
        context_scope="not_applicable",
    )
    session.add(link)
    session.commit()
    context = _built_context().payload.model_copy(
        update={"findings": [_built_context().payload.findings[0]]}
    )
    binding = EvidenceBinding(
        "F001",
        finding.id,
        link.id,
        "E001",
        context.findings[0].evidence[0].quote,
        link.chunk_identity_sha256,
        link.chunk_content_sha256,
        str(document.source_url),
        link.source_locator_snapshot_json,
    )
    built = BuiltContext(
        context,
        canonical_sha256(context.model_dump(mode="json")),
        {"E001": binding},
    )
    return screening.id, built


def test_context_builder_reads_only_selected_trusted_evidence(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, _ = _persist_screening_graph(session)
    monkeypatch.setattr(
        FindingEvidenceAssembler,
        "verify_trusted_index",
        lambda _self, _session: SimpleNamespace(payload_hash="f" * 64),
    )
    built = ControlledRAGContextBuilder().build(session, screening_id)
    assert built.payload.findings[0].finding_key == "F001"
    assert built.payload.findings[0].evidence[0].citation_key == "E001"
    assert built.payload.findings[0].evidence[0].record_type == "regulation"
    assert (
        built.payload_sha256
        == ControlledRAGContextBuilder().build(session, screening_id).payload_sha256
    )


def test_completed_service_persists_artifact_and_citations_without_raw_response(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    run = service.create(
        session,
        screening_run_id=screening_id,
        audience="institution",
        provider_name="deterministic_fixture",
    )
    assert run.status == "completed"
    assert run.validation_status == "passed"
    artifact = session.query(ExplanationArtifact).one()
    assert artifact.artifact_sha256
    assert len(artifact.citations) == 1
    assert not hasattr(artifact, "raw_provider_response")


def test_rejected_service_persists_run_without_artifact(session: Any, monkeypatch: Any) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    with pytest.raises(ExplanationError, match="explanation_unknown_citation_key"):
        service.create(
            session,
            screening_run_id=screening_id,
            audience="institution",
            provider_name="deterministic_fixture",
            provider=DeterministicFixtureProvider("unknown_citation"),
        )
    run = session.query(ExplanationRun).one()
    assert (run.status, run.validation_status) == ("rejected", "rejected_invalid_citation")
    assert session.query(ExplanationArtifact).count() == 0


def test_provider_failure_persists_failed_run_without_secret_or_artifact(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    with pytest.raises(ExplanationError, match="explanation_provider_not_configured"):
        service.create(
            session,
            screening_run_id=screening_id,
            audience="consumer",
            provider_name="external",
        )
    run = session.query(ExplanationRun).one()
    assert run.status == "failed"
    assert run.provider_configuration_json == {"enabled": False}
    assert session.query(ExplanationArtifact).count() == 0


def test_retry_creates_new_run_and_preserves_rejected_history(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    with pytest.raises(ExplanationError):
        service.create(
            session,
            screening_run_id=screening_id,
            audience="institution",
            provider_name="deterministic_fixture",
            provider=DeterministicFixtureProvider("unknown_citation"),
        )
    first = session.query(ExplanationRun).one()
    second = service.create(
        session,
        screening_run_id=screening_id,
        audience="institution",
        provider_name="deterministic_fixture",
        retry_of_id=first.id,
    )
    assert first.status == "rejected"
    assert second.status == "completed"
    assert second.retry_of_id == first.id
    second.retry_of_id = None
    session.commit()


def test_artifact_reads_are_provider_free_and_reproducible(session: Any, monkeypatch: Any) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    run = service.create(
        session,
        screening_run_id=screening_id,
        audience="consumer",
        provider_name="deterministic_fixture",
    )
    first = service.artifact(session, run.id)
    second = service.artifact(session, run.id)
    assert first == second
    assert service.citations(session, run.id)[0]["citation_key"] == "E001"


def test_explanation_api_uses_shared_service(session: Any, monkeypatch: Any) -> None:
    screening_id, built = _persist_screening_graph(session)
    monkeypatch.setattr(
        ControlledRAGContextBuilder,
        "build",
        lambda _self, _session, _run_id: built,
    )
    api_app.dependency_overrides[get_db] = lambda: session
    try:
        client = TestClient(api_app, raise_server_exceptions=False)
        created = client.post(
            f"/api/v1/screenings/{screening_id}/explanations",
            json={"audience": "institution", "provider": "deterministic_fixture"},
        )
        assert created.status_code == 200
        run_id = created.json()["explanation_run_id"]
        assert client.get(f"/api/v1/explanations/{run_id}").json()["status"] == "completed"
        assert client.get(f"/api/v1/explanations/{run_id}/artifact").json()["artifact_sha256"]
        assert len(client.get(f"/api/v1/explanations/{run_id}/citations").json()) == 1
    finally:
        api_app.dependency_overrides.clear()


def test_explanation_cli_lists_versioned_prompt_hashes() -> None:
    result = CliRunner().invoke(cli_app, ["explanation", "prompts"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {item["audience"] for item in payload} == {"institution", "consumer"}
    assert all(len(item["prompt_sha256"]) == 64 for item in payload)
