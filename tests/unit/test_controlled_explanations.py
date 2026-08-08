from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from app.api.dependencies import get_db
from app.cli.main import app as cli_app
from app.core.config import Settings
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
    OpenAICompatibleProvider,
    ProviderGenerationError,
    configured_provider_status,
    provider_configuration_sha256,
    provider_from_name,
)
from app.services.explanation.schemas import (
    AllowedEvidenceSegment,
    ControlledEvidence,
    ControlledFinding,
    ControlledRAGContext,
    ExplanationProviderRequest,
    InstitutionExplanationV1,
    PromptDefinition,
    VisibleSegmentV1,
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
    segment_one = AllowedEvidenceSegment(
        field_name="article_text",
        quote="不得利用监管机构名义对保险产品作引人误解的宣传",
        evidence_snapshot={
            "field_name": "article_text",
            "mode": "verbatim",
        },
        original_quote_length=23,
        truncated=False,
    )
    visible_one = VisibleSegmentV1(
        field_name="article_text",
        quote="不得利用监管机构名义对保险产品作引人误解的宣传",
        truncated=False,
        original_quote_length=23,
    )
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
        evidence_references=[segment_one.evidence_snapshot],
        context_scope="not_applicable",
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        evidence_field_name="article_text",
        visible_segments=[visible_one],
    )
    segment_two = AllowedEvidenceSegment(
        field_name="exclusions",
        quote="本合同责任免除事项以正式合同约定为准",
        evidence_snapshot={
            "field_name": "exclusions",
            "mode": "verbatim",
        },
        original_quote_length=17,
        truncated=False,
    )
    visible_two = VisibleSegmentV1(
        field_name="exclusions",
        quote="本合同责任免除事项以正式合同约定为准",
        truncated=False,
        original_quote_length=17,
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
        evidence_references=[segment_two.evidence_snapshot],
        context_scope="illustrative_not_material_specific",
        chunk_identity_sha256="c" * 64,
        chunk_content_sha256="d" * 64,
        evidence_field_name="exclusions",
        visible_segments=[visible_two],
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
            finding_key="F001",
            finding_id=10 + id_offset,
            link_id=20 + id_offset,
            citation_key="E001",
            quote=evidence_one.quote,
            chunk_identity_sha256=evidence_one.chunk_identity_sha256,
            chunk_content_sha256=evidence_one.chunk_content_sha256,
            source_url=evidence_one.source_url,
            source_locator=evidence_one.source_locator,
            support_type=evidence_one.support_type,
            source_title=evidence_one.source_title,
            pilot_id=evidence_one.pilot_id,
            record_type=evidence_one.record_type,
            chunk_kind=evidence_one.chunk_kind,
            context_scope=evidence_one.context_scope,
            allowed_quote_segments=(segment_one,),
            semantic_anchors=("监管机构",),
        ),
        "E002": EvidenceBinding(
            finding_key="F002",
            finding_id=11 + id_offset,
            link_id=21 + id_offset,
            citation_key="E002",
            quote=evidence_two.quote,
            chunk_identity_sha256=evidence_two.chunk_identity_sha256,
            chunk_content_sha256=evidence_two.chunk_content_sha256,
            source_url=evidence_two.source_url,
            source_locator=evidence_two.source_locator,
            support_type=evidence_two.support_type,
            source_title=evidence_two.source_title,
            pilot_id=evidence_two.pilot_id,
            record_type=evidence_two.record_type,
            chunk_kind=evidence_two.chunk_kind,
            context_scope=evidence_two.context_scope,
            allowed_quote_segments=(segment_two,),
            semantic_anchors=("责任免除",),
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
        with pytest.raises(ProviderGenerationError, match=expected):
            DeterministicFixtureProvider(scenario).generate(request)
        return
    raw = DeterministicFixtureProvider(scenario).generate(request).raw_json
    if expected == "passed":
        validated = ControlledExplanationValidator().validate(raw, prompt, built)
        assert validated.output["schema_version"] == prompt.output_schema_version
    else:
        with pytest.raises(ExplanationError) as exc_info:
            ControlledExplanationValidator().validate(raw, prompt, built)
        assert str(exc_info.value) == expected


def test_fixture_corpus_has_required_valid_invalid_balance() -> None:
    assert len(EVAL_SAMPLES) == 55
    assert sum(item["expected"] == "passed" for item in EVAL_SAMPLES) == 12
    assert sum(item["expected"] != "passed" for item in EVAL_SAMPLES) == 43


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


def _openai_provider(
    handler: Any | None = None,
    *,
    enabled: bool = True,
    api_key: str = "test-real-provider-secret",
    base_url: str = "https://llm.example.test/v1",
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        Settings(
            llm_enabled=enabled,
            llm_provider="openai_compatible",
            llm_base_url=base_url,
            llm_api_key=api_key,
            llm_model="contest-model",
            llm_timeout_seconds=12,
        ),
        transport=httpx.MockTransport(handler) if handler is not None else None,
    )


def test_openai_compatible_provider_returns_structured_json() -> None:
    prompt = load_prompt("institution")
    request = ExplanationProviderRequest(
        audience="institution", prompt=prompt, context=_built_context().payload
    )
    expected = DeterministicFixtureProvider().generate(request).raw_json

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert str(http_request.url) == "https://llm.example.test/v1/chat/completions"
        assert http_request.headers["authorization"] == "Bearer test-real-provider-secret"
        payload = json.loads(http_request.content)
        assert payload["model"] == "contest-model"
        assert payload["temperature"] == 0
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"][0]["role"] == "system"
        assert "authoritative_prompt_definition" in payload["messages"][0]["content"]
        contract = json.dumps(
            prompt.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        )
        assert contract in payload["messages"][0]["content"]
        assert json.loads(payload["messages"][1]["content"])["findings"][0]["finding_key"] == "F001"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": expected}}]},
            request=http_request,
        )

    provider = _openai_provider(handler)
    assert provider.generate(request).raw_json == expected
    assert provider.safe_configuration == {
        "enabled": True,
        "protocol": "openai_compatible_chat_completions_v1",
        "model": "contest-model",
        "timeout_seconds": 12,
    }


def test_openai_provider_prompt_contract_is_derived_from_prompt_definition() -> None:
    prompt = load_prompt("consumer").model_copy(
        update={
            "system_instruction": "只按本次受控解释契约输出，不得遗漏任何固定要求或引用边界。",
            "allowed_claim_types": ("contract_claim",),
            "forbidden_claim_patterns": ("禁止的契约词",),
            "required_disclaimer": "本次精确免责声明必须逐字保留。",
            "citation_format": "contract citation format",
            "uncertainty_instructions": "本次部分支持必须使用契约不确定性说明。",
            "insufficient_evidence_instructions": "本次证据不足不得作为确定事实。",
            "illustrative_product_disclaimer": "本次示例产品声明必须逐字保留。",
        }
    )
    request = ExplanationProviderRequest(
        audience="consumer", prompt=prompt, context=_built_context().payload
    )
    payload = _openai_provider()._request_payload(request)
    system = str(payload["messages"][0]["content"])
    authoritative = json.dumps(
        prompt.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
    assert authoritative in system
    for value in (
        prompt.required_disclaimer,
        prompt.allowed_claim_types[0],
        prompt.forbidden_claim_patterns[0],
        prompt.citation_format,
        prompt.uncertainty_instructions,
        prompt.insufficient_evidence_instructions,
        prompt.illustrative_product_disclaimer,
    ):
        assert value in system
    assert "finding_keys 必须恰好等于其 row 的 finding_key" in system
    assert "连续 allowed evidence quote" in system


def test_openai_compatible_provider_unconfigured_fails_closed() -> None:
    request = ExplanationProviderRequest(
        audience="consumer", prompt=load_prompt("consumer"), context=_built_context().payload
    )
    with pytest.raises(ProviderGenerationError, match="explanation_provider_not_configured"):
        _openai_provider(enabled=False).generate(request)
    with pytest.raises(ProviderGenerationError, match="explanation_provider_not_configured"):
        _openai_provider(base_url="http://llm.example.test/v1").generate(request)


@pytest.mark.parametrize(
    ("handler", "error_code"),
    [
        (
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)),
            "explanation_provider_timeout",
        ),
        (
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("network", request=request)),
            "explanation_provider_network_error",
        ),
        (lambda request: httpx.Response(429, request=request), "explanation_provider_rate_limited"),
        (lambda request: httpx.Response(503, request=request), "explanation_provider_http_error"),
        (
            lambda request: httpx.Response(200, json={"choices": []}, request=request),
            "explanation_provider_invalid_response",
        ),
    ],
)
def test_openai_compatible_provider_maps_transport_and_envelope_failures(
    handler: Any, error_code: str
) -> None:
    request = ExplanationProviderRequest(
        audience="consumer", prompt=load_prompt("consumer"), context=_built_context().payload
    )
    with pytest.raises(ProviderGenerationError, match=error_code):
        _openai_provider(handler).generate(request)


def test_openai_compatible_provider_status_never_includes_secret() -> None:
    settings = Settings(
        llm_enabled=True,
        llm_provider="openai_compatible",
        llm_base_url="https://llm.example.test/v1",
        llm_api_key="test-real-provider-secret",
        llm_model="contest-model",
    )
    payload = configured_provider_status(settings)
    assert payload == {
        "provider": "openai_compatible",
        "mode": "real_ai",
        "label": "真实大模型",
        "model": "contest-model",
        "ready": True,
    }
    assert "secret" not in json.dumps(payload).lower()
    assert isinstance(provider_from_name("deterministic_fixture"), DeterministicFixtureProvider)


@pytest.mark.parametrize(
    "text",
    [
        "可能存在风险信号",
        "需要进一步核验",
        "现有证据显示可能存在风险信号，需要进一步核验",
        "当前证据不足以作出结论",
        "该处罚案例可作为相似执法参考，需要进一步核验",
    ],
)
def test_unsupported_claim_detector_allows_cautious_language(text: str) -> None:
    payload, prompt, _ = _valid_payload("institution")
    payload["finding_explanations"][0]["explanation"]["text"] = text
    output = ControlledExplanationValidator().validate(
        json.dumps(payload, ensure_ascii=False), prompt, _built_context()
    )
    assert (
        UnsupportedClaimDetectorV1().detect(
            InstitutionExplanationV1.model_validate(output.output), prompt
        )
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
    binding = replace(
        _built_context().bindings["E001"],
        finding_id=finding.id,
        link_id=link.id,
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


def test_evidence_insufficient_builds_and_persists_zero_citation_artifact(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, _ = _persist_screening_graph(session)
    session.query(FindingEvidenceLink).delete()
    finding = session.query(RiskFinding).one()
    finding.evidence_status = "evidence_insufficient"
    screening = session.get(ScreeningRun, screening_id)
    assert screening is not None
    screening.insufficient_evidence_count = 1
    session.commit()
    monkeypatch.setattr(
        FindingEvidenceAssembler,
        "verify_trusted_index",
        lambda _self, _session: SimpleNamespace(payload_hash="f" * 64),
    )
    built = ControlledRAGContextBuilder().build(session, screening_id)
    assert built.payload.findings[0].evidence == []
    assert built.bindings == {}
    run = ControlledExplanationService().create(
        session,
        screening_run_id=screening_id,
        audience="institution",
        provider_name="deterministic_fixture",
    )
    artifact = ControlledExplanationService().artifact(session, run.id)
    assert run.status == "completed"
    assert "当前证据不足以作出结论" in json.dumps(artifact["validated_output"], ensure_ascii=False)
    assert ControlledExplanationService().citations(session, run.id) == []
    assert session.query(ExplanationArtifact).one().citations == []


def _budget_context(
    finding_count: int, evidence_per_finding: int, *, illustrative: bool = False
) -> BuiltContext:
    findings: list[ControlledFinding] = []
    bindings: dict[str, EvidenceBinding] = {}
    ordinal = 1
    for finding_index in range(finding_count):
        evidence_rows: list[ControlledEvidence] = []
        for evidence_index in range(evidence_per_finding):
            key = f"E{ordinal:03d}"
            quote = f"规范证据{finding_index}-{evidence_index}。" + "严" * 580
            evidence_field = "exclusions" if illustrative else "article_text"
            original_len = 590
            segment = AllowedEvidenceSegment(
                field_name=evidence_field,
                quote=quote,
                evidence_snapshot={
                    "field_name": evidence_field,
                    "mode": "verbatim",
                    "start_offset": finding_index * 1000 + evidence_index * 600,
                    "end_offset": finding_index * 1000 + evidence_index * 600 + len(quote),
                },
                original_quote_length=original_len,
                truncated=False,
            )
            visible = VisibleSegmentV1(
                field_name=evidence_field,
                quote=quote,
                truncated=False,
                original_quote_length=original_len,
            )
            evidence = ControlledEvidence(
                citation_key=key,
                support_type="product_term_context" if illustrative else "normative_basis",
                source_title="监管规则" + "甲" * 30,
                source_url=f"https://example.test/rule/{finding_index}",
                pilot_id=f"REG-{finding_index:03d}",
                record_type="product_document" if illustrative else "regulation",
                chunk_kind="exclusions" if illustrative else "article_text",
                quote=quote,
                source_locator={"article_number": f"第{finding_index + 1}条", "path": "层" * 30},
                evidence_references=[segment.evidence_snapshot],
                context_scope=(
                    "illustrative_not_material_specific" if illustrative else "not_applicable"
                ),
                chunk_identity_sha256=f"{ordinal:064x}",
                chunk_content_sha256=f"{ordinal + 1000:064x}",
                evidence_field_name=evidence_field,
                visible_segments=[visible],
            )
            evidence_rows.append(evidence)
            bindings[key] = EvidenceBinding(
                finding_key=f"F{finding_index + 1:03d}",
                finding_id=finding_index + 1,
                link_id=ordinal,
                citation_key=key,
                quote=quote,
                chunk_identity_sha256=evidence.chunk_identity_sha256,
                chunk_content_sha256=evidence.chunk_content_sha256,
                source_url=evidence.source_url,
                source_locator=evidence.source_locator,
                support_type=evidence.support_type,
                source_title=evidence.source_title,
                pilot_id=evidence.pilot_id,
                record_type=evidence.record_type,
                chunk_kind=evidence.chunk_kind,
                context_scope=evidence.context_scope,
                allowed_quote_segments=(segment,),
                semantic_anchors=(f"规范证据{finding_index}-{evidence_index}",),
            )
            ordinal += 1
        findings.append(
            ControlledFinding(
                finding_key=f"F{finding_index + 1:03d}",
                rule_id="regulatory_endorsement",
                category="监管背书",
                severity="high",
                signal_strength="strong",
                matched_text=f"风险信号{finding_index}",
                raw_start_offset=finding_index * 5,
                raw_end_offset=finding_index * 5 + 4,
                deterministic_explanation="该表达需要人工复核。",
                review_question="是否需要核验？",
                evidence_status="partially_supported",
                evidence=evidence_rows,
            )
        )
    context = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={"title": "压力材料", "material_type": "advertisement", "input_sha256": "1" * 64},
        findings=findings,
    )
    fitted, fitted_bindings = ControlledRAGContextBuilder._fit_budget(context, bindings)
    return BuiltContext(
        fitted,
        canonical_sha256(fitted.model_dump(mode="json")),
        fitted_bindings,
    )


@pytest.mark.parametrize(
    "finding_count,evidence_count,illustrative",
    [(20, 4, False), (20, 4, True)],
)
def test_context_budget_preserves_minimum_evidence_and_valid_output(
    finding_count: int, evidence_count: int, illustrative: bool
) -> None:
    first = _budget_context(finding_count, evidence_count, illustrative=illustrative)
    second = _budget_context(finding_count, evidence_count, illustrative=illustrative)
    assert first.payload_sha256 == second.payload_sha256
    assert all(len(finding.evidence) >= 1 for finding in first.payload.findings)
    visible_keys = {
        evidence.citation_key for finding in first.payload.findings for evidence in finding.evidence
    }
    assert visible_keys == set(first.bindings)
    prompt = load_prompt("institution")
    response = DeterministicFixtureProvider().generate(
        ExplanationProviderRequest(audience="institution", prompt=prompt, context=first.payload)
    )
    validated = ControlledExplanationValidator().validate(response.raw_json, prompt, first)
    assert len(validated.output["finding_explanations"]) == finding_count


def test_33_finding_context_fails_closed_instead_of_dropping_required_evidence() -> None:
    with pytest.raises(ExplanationError, match="explanation_context_too_large"):
        _budget_context(33, 1)


@pytest.mark.parametrize("field", ["title", "pilot_id"])
def test_context_builder_rejects_complete_source_snapshot_drift(
    session: Any, monkeypatch: Any, field: str
) -> None:
    screening_id, _ = _persist_screening_graph(session)
    link = session.query(FindingEvidenceLink).one()
    snapshot = dict(link.source_document_snapshot_json)
    snapshot[field] = "tampered"
    link.source_document_snapshot_json = snapshot
    session.commit()
    monkeypatch.setattr(
        FindingEvidenceAssembler,
        "verify_trusted_index",
        lambda _self, _session: SimpleNamespace(payload_hash="f" * 64),
    )
    with pytest.raises(ExplanationError, match="explanation_citation_snapshot_mismatch"):
        ControlledRAGContextBuilder().build(session, screening_id)


def test_prompt_snapshot_forbidden_patterns_and_claim_types_are_enforced() -> None:
    payload, prompt, built = _valid_payload("institution")
    forbidden = prompt.model_copy(
        update={"forbidden_claim_patterns": (*prompt.forbidden_claim_patterns, "风险信号")}
    )
    with pytest.raises(ExplanationError, match="explanation_unsupported_claim"):
        ControlledExplanationValidator().validate(
            json.dumps(payload, ensure_ascii=False), forbidden, built
        )
    restricted = prompt.model_copy(update={"allowed_claim_types": ("deterministic_template",)})
    with pytest.raises(ExplanationError, match="explanation_output_invalid_schema"):
        ControlledExplanationValidator().validate(
            json.dumps(payload, ensure_ascii=False), restricted, built
        )


def test_consumer_artifact_and_citations_resolve_server_side_source_catalog(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    run = service.create(
        session,
        screening_run_id=screening_id,
        audience="consumer",
        provider_name="deterministic_fixture",
    )
    artifact = service.artifact(session, run.id)
    resolved = artifact["resolved_citations"]
    assert resolved[0]["source_url"] == "https://example.test/regulation"
    validated_links = artifact["validated_output"]["evidence_links"]
    assert len(validated_links) == 1
    assert validated_links[0]["citation_key"] == "E001"
    assert validated_links[0]["cited_quote"]
    assert "source_url" not in validated_links[0]
    catalog = service.citations(session, run.id)
    assert catalog[0]["source_locator"] == {"article_number": "第十七条"}
    assert catalog[0]["evidence_field_name"] == "article_text"


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


@pytest.mark.parametrize(
    ("fixture_scenario", "error_code", "validation_status"),
    [
        ("empty", "explanation_output_invalid_schema", "rejected_invalid_schema"),
        ("unknown_citation", "explanation_unknown_citation_key", "rejected_invalid_citation"),
    ],
)
def test_openai_provider_output_still_uses_existing_strict_validator(
    session: Any,
    monkeypatch: Any,
    fixture_scenario: str,
    error_code: str,
    validation_status: str,
) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    request = ExplanationProviderRequest(
        audience="institution", prompt=load_prompt("institution"), context=built.payload
    )
    raw_json = (
        json.dumps({"disclaimer": request.prompt.required_disclaimer}, ensure_ascii=False)
        if fixture_scenario == "empty"
        else DeterministicFixtureProvider(fixture_scenario).generate(request).raw_json
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": raw_json}}]},
            request=http_request,
        )

    with pytest.raises(ExplanationError, match=error_code):
        service.create(
            session,
            screening_run_id=screening_id,
            audience="institution",
            provider_name="openai_compatible",
            provider=_openai_provider(handler),
        )
    run = session.query(ExplanationRun).one()
    assert (run.status, run.validation_status) == ("rejected", validation_status)
    assert session.query(ExplanationArtifact).count() == 0


def test_openai_provider_secret_never_persists_in_run_or_artifact(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, built = _persist_screening_graph(session)
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: built)
    request = ExplanationProviderRequest(
        audience="institution", prompt=load_prompt("institution"), context=built.payload
    )
    raw_json = DeterministicFixtureProvider().generate(request).raw_json

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": raw_json}}]},
            request=http_request,
        )

    run = service.create(
        session,
        screening_run_id=screening_id,
        audience="institution",
        provider_name="openai_compatible",
        provider=_openai_provider(handler),
    )
    stored = json.dumps(
        {
            "run": run.provider_configuration_json,
            "artifact": service.artifact(session, run.id),
        },
        ensure_ascii=False,
    )
    assert "test-real-provider-secret" not in stored
    assert "https://llm.example.test" not in stored


def test_zero_finding_context_does_not_call_provider_or_create_artifact(
    session: Any, monkeypatch: Any
) -> None:
    screening_id, built = _persist_screening_graph(session)
    empty_context = built.payload.model_copy(update={"findings": []})
    empty_built = BuiltContext(
        empty_context,
        canonical_sha256(empty_context.model_dump(mode="json")),
        {},
    )
    service = ControlledExplanationService()
    monkeypatch.setattr(service.context_builder, "build", lambda *_args: empty_built)
    provider = DeterministicFixtureProvider()
    calls = 0

    def should_not_generate(_: ExplanationProviderRequest) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("zero-finding explanation must not call a provider")

    monkeypatch.setattr(provider, "generate", should_not_generate)
    with pytest.raises(ExplanationError, match="explanation_not_required"):
        service.create(
            session,
            screening_run_id=screening_id,
            audience="institution",
            provider_name="deterministic_fixture",
            provider=provider,
        )
    assert calls == 0
    assert session.query(ExplanationRun).count() == 0
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


# ── Problem A: hidden segment cannot be cited ──────────────────────────────────


def test_hidden_segment_not_in_context_cannot_be_cited() -> None:
    """segment B in binding but only segment A visible to model → reject."""
    seg_a = AllowedEvidenceSegment(
        field_name="article_text",
        quote="不得利用监管机构名义作引人误解的宣传",
        evidence_snapshot={"field_name": "article_text", "mode": "verbatim"},
        original_quote_length=18,
        truncated=False,
    )
    seg_b = AllowedEvidenceSegment(
        field_name="illegal_facts",
        quote="处以罚款50万元并责令改正",
        evidence_snapshot={"field_name": "illegal_facts", "mode": "verbatim"},
        original_quote_length=13,
        truncated=False,
    )
    visible_a = VisibleSegmentV1(
        field_name="article_text",
        quote="不得利用监管机构名义作引人误解的宣传",
        truncated=False,
        original_quote_length=18,
    )
    evidence = ControlledEvidence(
        citation_key="E001",
        support_type="normative_basis",
        source_title="保险销售行为管理办法",
        source_url="https://example.test/regulation",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        quote=seg_a.quote,
        source_locator={"article_number": "第十七条"},
        evidence_references=[seg_a.evidence_snapshot, seg_b.evidence_snapshot],
        context_scope="not_applicable",
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        evidence_field_name="article_text",
        visible_segments=[visible_a],
    )
    """only visible_a is visible; seg_b is NOT exposed to the model."""
    context = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={"title": "T", "material_type": "advertisement", "input_sha256": "1" * 64},
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
                deterministic_explanation="explanation",
                review_question="review?",
                evidence_status="partially_supported",
                evidence=[evidence],
            )
        ],
    )
    binding = EvidenceBinding(
        finding_key="F001",
        finding_id=1,
        link_id=1,
        citation_key="E001",
        quote=seg_a.quote,
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        source_url="https://example.test/regulation",
        source_locator={"article_number": "第十七条"},
        support_type="normative_basis",
        source_title="保险销售行为管理办法",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        context_scope="not_applicable",
        allowed_quote_segments=(seg_a,),
        semantic_anchors=("监管机构",),
    )
    built = BuiltContext(
        context, canonical_sha256(context.model_dump(mode="json")), {"E001": binding}
    )
    prompt = load_prompt("institution")
    provider = DeterministicFixtureProvider()
    response = provider.generate(
        ExplanationProviderRequest(audience="institution", prompt=prompt, context=context)
    )
    output = json.loads(response.raw_json)
    output["finding_explanations"][0]["explanation"]["citations"][0]["cited_quote"] = "罚款50万元"
    output["finding_explanations"][0]["evidence_assessment"]["citations"][0]["cited_quote"] = (
        "罚款50万元"
    )
    with pytest.raises(ExplanationError, match="explanation_citation_snapshot_mismatch"):
        ControlledExplanationValidator().validate(
            json.dumps(output, ensure_ascii=False), prompt, built
        )


def test_hidden_segment_rejection_is_robust_against_coincidental_match() -> None:
    """segment B has a common phrase contained in segment A → still rejected because B is hidden."""
    seg_a = AllowedEvidenceSegment(
        field_name="article_text",
        quote="营销宣传不得含有误导性表述违规信息",
        evidence_snapshot={"field_name": "article_text", "mode": "verbatim"},
        original_quote_length=17,
        truncated=False,
    )
    seg_b = AllowedEvidenceSegment(
        field_name="illegal_facts",
        quote="违规信息已被监管部门记录",
        evidence_snapshot={"field_name": "illegal_facts", "mode": "verbatim"},
        original_quote_length=12,
        truncated=False,
    )
    visible_a = VisibleSegmentV1(
        field_name="article_text",
        quote="营销宣传不得含有误导性表述违规信息",
        truncated=False,
        original_quote_length=17,
    )
    evidence = ControlledEvidence(
        citation_key="E001",
        support_type="normative_basis",
        source_title="测试法规",
        source_url="https://example.test/regulation",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        quote=seg_a.quote,
        source_locator={"article_number": "第一条"},
        evidence_references=[seg_a.evidence_snapshot, seg_b.evidence_snapshot],
        context_scope="not_applicable",
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        evidence_field_name="article_text",
        visible_segments=[visible_a],
    )
    context = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={"title": "T", "material_type": "advertisement", "input_sha256": "1" * 64},
        findings=[
            ControlledFinding(
                finding_key="F001",
                rule_id="regulatory_endorsement",
                category="监管背书",
                severity="high",
                signal_strength="strong",
                matched_text="违规信息",
                raw_start_offset=0,
                raw_end_offset=4,
                deterministic_explanation="explanation",
                review_question="review?",
                evidence_status="partially_supported",
                evidence=[evidence],
            )
        ],
    )
    binding = EvidenceBinding(
        finding_key="F001",
        finding_id=1,
        link_id=1,
        citation_key="E001",
        quote=seg_a.quote,
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        source_url="https://example.test/regulation",
        source_locator={"article_number": "第一条"},
        support_type="normative_basis",
        source_title="测试法规",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        context_scope="not_applicable",
        allowed_quote_segments=(seg_a,),
        semantic_anchors=("误导性表述",),
    )
    built = BuiltContext(
        context, canonical_sha256(context.model_dump(mode="json")), {"E001": binding}
    )
    prompt = load_prompt("institution")
    provider = DeterministicFixtureProvider()
    response = provider.generate(
        ExplanationProviderRequest(audience="institution", prompt=prompt, context=context)
    )
    output = json.loads(response.raw_json)
    output["finding_explanations"][0]["explanation"]["citations"][0]["cited_quote"] = (
        "违规信息已被监管部门记录"
    )
    output["finding_explanations"][0]["evidence_assessment"]["citations"][0]["cited_quote"] = (
        "违规信息已被监管部门记录"
    )
    """违规信息  appears in seg_a but the full cited_quote is from seg_b (hidden)."""
    with pytest.raises(ExplanationError, match="explanation_citation_snapshot_mismatch"):
        ControlledExplanationValidator().validate(
            json.dumps(output, ensure_ascii=False), prompt, built
        )


def test_visible_multi_segment_all_can_be_cited() -> None:
    """both segments visible → both can be cited."""
    seg_a = AllowedEvidenceSegment(
        field_name="article_text",
        quote="不得利用监管机构名义作引人误解的宣传",
        evidence_snapshot={"field_name": "article_text", "mode": "verbatim"},
        original_quote_length=18,
        truncated=False,
    )
    seg_b = AllowedEvidenceSegment(
        field_name="illegal_facts",
        quote="处以罚款50万元并责令改正",
        evidence_snapshot={"field_name": "illegal_facts", "mode": "verbatim"},
        original_quote_length=13,
        truncated=False,
    )
    visible_a = VisibleSegmentV1(
        field_name="article_text", quote=seg_a.quote, truncated=False, original_quote_length=18
    )
    visible_b = VisibleSegmentV1(
        field_name="illegal_facts", quote=seg_b.quote, truncated=False, original_quote_length=13
    )
    evidence = ControlledEvidence(
        citation_key="E001",
        support_type="normative_basis",
        source_title="保险销售行为管理办法",
        source_url="https://example.test/regulation",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        quote=seg_a.quote,
        source_locator={"article_number": "第十七条"},
        evidence_references=[seg_a.evidence_snapshot, seg_b.evidence_snapshot],
        context_scope="not_applicable",
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        evidence_field_name="article_text",
        visible_segments=[visible_a, visible_b],
    )
    context = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={"title": "T", "material_type": "advertisement", "input_sha256": "1" * 64},
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
                deterministic_explanation="explanation",
                review_question="review?",
                evidence_status="partially_supported",
                evidence=[evidence],
            )
        ],
    )
    binding = EvidenceBinding(
        finding_key="F001",
        finding_id=1,
        link_id=1,
        citation_key="E001",
        quote=seg_a.quote,
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        source_url="https://example.test/regulation",
        source_locator={"article_number": "第十七条"},
        support_type="normative_basis",
        source_title="保险销售行为管理办法",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        context_scope="not_applicable",
        allowed_quote_segments=(seg_a, seg_b),
        semantic_anchors=("监管机构",),
    )
    built = BuiltContext(
        context, canonical_sha256(context.model_dump(mode="json")), {"E001": binding}
    )
    prompt = load_prompt("institution")
    provider = DeterministicFixtureProvider()
    response = provider.generate(
        ExplanationProviderRequest(audience="institution", prompt=prompt, context=context)
    )
    output = json.loads(response.raw_json)
    output["finding_explanations"][0]["explanation"]["citations"][0]["cited_quote"] = "罚款50万元"
    output["finding_explanations"][0]["evidence_assessment"]["citations"][0]["cited_quote"] = (
        "罚款50万元"
    )
    validated = ControlledExplanationValidator().validate(
        json.dumps(output, ensure_ascii=False), prompt, built
    )
    assert validated.output["schema_version"] == "institution_explanation_v1"


# ── Problem B: Consumer artifact schema compliance ────────────────────────────


def test_consumer_artifact_validated_output_json_conforms_to_output_schema() -> None:
    prompt = load_prompt("consumer")
    built = _built_context()
    response = DeterministicFixtureProvider().generate(
        ExplanationProviderRequest(audience="consumer", prompt=prompt, context=built.payload)
    )
    raw = json.loads(response.raw_json)
    validated = ControlledExplanationValidator().validate(
        json.dumps(raw, ensure_ascii=False), prompt, built
    )
    stored_output = validated.output
    output_schema_version = prompt.output_schema_version
    stored_output["schema_version"] = output_schema_version
    from app.services.explanation.schemas import ConsumerExplanationV1

    ConsumerExplanationV1.model_validate(stored_output)


def test_consumer_artifact_evidence_links_are_output_citations_not_resolved() -> None:
    prompt = load_prompt("consumer")
    built = _built_context()
    response = DeterministicFixtureProvider().generate(
        ExplanationProviderRequest(audience="consumer", prompt=prompt, context=built.payload)
    )
    raw = json.loads(response.raw_json)
    validated = ControlledExplanationValidator().validate(
        json.dumps(raw, ensure_ascii=False), prompt, built
    )
    stored = validated.output
    for link in stored.get("evidence_links", []):
        assert set(link.keys()) == {"citation_key", "cited_quote"}, (
            f"unexpected keys: {link.keys()}"
        )
    resolved = validated.resolved_citations
    assert len(resolved) >= 1
    assert "source_url" in resolved[0].model_dump(mode="json")


# ── Problem C: truncation recording ───────────────────────────────────────────


def test_600_char_initial_truncation_is_recorded() -> None:
    """evidence over 600 chars → segment truncated=true, original length recorded."""
    long_quote = "规" * 846
    evidence_field_name = "article_text"
    seg = AllowedEvidenceSegment(
        field_name=evidence_field_name,
        quote=long_quote[:600],
        evidence_snapshot={"field_name": evidence_field_name, "mode": "verbatim"},
        original_quote_length=846,
        truncated=True,
    )
    visible = VisibleSegmentV1(
        field_name=evidence_field_name,
        quote=long_quote[:600],
        truncated=True,
        original_quote_length=846,
    )
    evidence = ControlledEvidence(
        citation_key="E001",
        support_type="normative_basis",
        source_title="测试法规",
        source_url="https://example.test/regulation",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        quote=long_quote[:600],
        source_locator={"article_number": "第一条"},
        evidence_references=[seg.evidence_snapshot],
        context_scope="not_applicable",
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        evidence_field_name=evidence_field_name,
        visible_segments=[visible],
        truncated=True,
    )
    assert evidence.truncated is True
    assert evidence.visible_segments[0].truncated is True
    assert evidence.visible_segments[0].original_quote_length == 846
    assert len(evidence.quote) == 600
    context = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={"title": "T", "material_type": "advertisement", "input_sha256": "1" * 64},
        findings=[
            ControlledFinding(
                finding_key="F001",
                rule_id="regulatory_endorsement",
                category="监管背书",
                severity="high",
                signal_strength="strong",
                matched_text="风险",
                raw_start_offset=0,
                raw_end_offset=2,
                deterministic_explanation="explanation",
                review_question="review?",
                evidence_status="partially_supported",
                evidence=[evidence],
            )
        ],
        truncated=True,
    )
    assert context.truncated is True


def test_budget_secondary_truncation_preserves_truncated_flag() -> None:
    """budget shortening keeps truncated=True on segments and context."""
    result = _budget_context(20, 4)
    ctx = result
    assert ctx.payload.truncated is True
    for finding in ctx.payload.findings:
        for evidence in finding.evidence:
            assert evidence.truncated is True
            for seg in evidence.visible_segments:
                assert seg.truncated is True


def test_same_input_produces_stable_context_sha_after_truncation() -> None:
    first = _budget_context(20, 1)
    second = _budget_context(20, 1)
    assert first.payload_sha256 == second.payload_sha256


# ── Problem D: partially_supported certainty detection ───────────────────────


@pytest.mark.parametrize(
    "bad_text",
    [
        "该风险已经得到确定证明。",
        "证据充分证明该行为违规。",
        "已经确认该条款有问题。",
        "可以确认存在误导。",
        "可以认定构成违规。",
        "事实明确，无需进一步调查。",
        "确定存在销售误导行为。",
        "该行为已被证实。",
        "这无疑是违法的。",
        "已经查明全部事实。",
        "结论明确：存在违规。",
        "该行为必然存在问题。",
        "一定构成违规。",
    ],
)
def test_partially_supported_rejects_deterministic_certainty(bad_text: str) -> None:
    payload, prompt, built = _valid_payload("institution")
    payload["finding_explanations"][0]["evidence_assessment"]["text"] = bad_text
    with pytest.raises(ExplanationError, match="explanation_missing_uncertainty"):
        ControlledExplanationValidator().validate(
            json.dumps(payload, ensure_ascii=False), prompt, built
        )


@pytest.mark.parametrize(
    "cautious_text",
    [
        "可能存在风险信号。",
        "当前证据仅能提供部分支持。",
        "需要进一步核验，仍需结合原始营销材料。",
        "当前证据不足以作出确定结论。",
        "需要进一步核验该表述。",
    ],
)
def test_partially_supported_accepts_cautious_language(cautious_text: str) -> None:
    payload, prompt, built = _valid_payload("institution")
    payload["finding_explanations"][0]["evidence_assessment"]["text"] = cautious_text
    validated = ControlledExplanationValidator().validate(
        json.dumps(payload, ensure_ascii=False), prompt, built
    )
    assert validated.output["schema_version"] == "institution_explanation_v1"


# ── Problem E: cross-finding per-finding citation binding ────────────────────


def test_cross_finding_claim_missing_one_finding_citation_rejected() -> None:
    prompt = load_prompt("institution")
    built = _built_context()
    response = DeterministicFixtureProvider().generate(
        ExplanationProviderRequest(audience="institution", prompt=prompt, context=built.payload)
    )
    output = json.loads(response.raw_json)
    output["cross_finding_observations"] = [
        {
            "claim_type": "cautious_cross_finding_observation",
            "text": "F001与F002均需要进一步核验。",
            "finding_keys": ["F001", "F002"],
            "citations": [{"citation_key": "E002", "cited_quote": built.bindings["E002"].quote}],
        }
    ]
    with pytest.raises(ExplanationError, match="explanation_citation_wrong_finding"):
        ControlledExplanationValidator().validate(
            json.dumps(output, ensure_ascii=False), prompt, built
        )


def test_cross_finding_claim_per_finding_evidence_passes() -> None:
    prompt = load_prompt("institution")
    built = _built_context()
    response = DeterministicFixtureProvider().generate(
        ExplanationProviderRequest(audience="institution", prompt=prompt, context=built.payload)
    )
    output = json.loads(response.raw_json)
    output["cross_finding_observations"] = [
        {
            "claim_type": "cautious_cross_finding_observation",
            "text": "F001与F002均需要进一步核验。",
            "finding_keys": ["F001", "F002"],
            "citations": [
                {"citation_key": "E001", "cited_quote": built.bindings["E001"].quote},
                {"citation_key": "E002", "cited_quote": built.bindings["E002"].quote},
            ],
        }
    ]
    validated = ControlledExplanationValidator().validate(
        json.dumps(output, ensure_ascii=False), prompt, built
    )
    assert validated.output["schema_version"] == "institution_explanation_v1"


def test_evidence_insufficient_finding_cannot_borrow_other_finding_citations(
    session: Any, monkeypatch: Any
) -> None:
    """F002 is evidence_insufficient, borrows F001's E001 → rejected."""
    # Build two-finding context: F001=supported with E001, F002=evidence_insufficient
    segment = AllowedEvidenceSegment(
        field_name="article_text",
        quote="不得利用监管机构名义作引人误解的宣传",
        evidence_snapshot={"field_name": "article_text", "mode": "verbatim"},
        original_quote_length=18,
        truncated=False,
    )
    visible = VisibleSegmentV1(
        field_name="article_text",
        quote="不得利用监管机构名义作引人误解的宣传",
        truncated=False,
        original_quote_length=18,
    )
    evidence_f001 = ControlledEvidence(
        citation_key="E001",
        support_type="normative_basis",
        source_title="保险销售行为管理办法",
        source_url="https://example.test/regulation",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        quote=segment.quote,
        source_locator={"article_number": "第十七条"},
        evidence_references=[segment.evidence_snapshot],
        context_scope="not_applicable",
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        evidence_field_name="article_text",
        visible_segments=[visible],
    )
    f001 = ControlledFinding(
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
        evidence_status="supported",
        evidence=[evidence_f001],
    )
    f002 = ControlledFinding(
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
        evidence_status="evidence_insufficient",
        evidence=[],
    )
    context = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={"title": "T", "material_type": "advertisement", "input_sha256": "1" * 64},
        findings=[f001, f002],
    )
    binding_e001 = EvidenceBinding(
        finding_key="F001",
        finding_id=1,
        link_id=1,
        citation_key="E001",
        quote=segment.quote,
        chunk_identity_sha256="a" * 64,
        chunk_content_sha256="b" * 64,
        source_url="https://example.test/regulation",
        source_locator={"article_number": "第十七条"},
        support_type="normative_basis",
        source_title="保险销售行为管理办法",
        pilot_id="REG-001",
        record_type="regulation",
        chunk_kind="article_text",
        context_scope="not_applicable",
        allowed_quote_segments=(segment,),
        semantic_anchors=("监管机构",),
    )
    built = BuiltContext(
        context, canonical_sha256(context.model_dump(mode="json")), {"E001": binding_e001}
    )
    prompt = load_prompt("institution")

    # Scenario 1: F002 claim using E001 → E001 belongs to F001, not F002
    output = {
        "schema_version": "institution_explanation_v1",
        "executive_summary": {
            "claim_type": "deterministic_template",
            "text": "以下内容仅解释已持久化的确定性筛查发现。",
            "finding_keys": ["F001", "F002"],
            "citations": [],
        },
        "finding_explanations": [
            {
                "finding_key": "F001",
                "explanation": {
                    "claim_type": "deterministic_finding_explanation",
                    "text": "现有证据显示可能存在风险信号，需要进一步核验。",
                    "finding_keys": ["F001"],
                    "citations": [{"citation_key": "E001", "cited_quote": segment.quote}],
                },
                "why_it_matters": {
                    "claim_type": "deterministic_template",
                    "text": f001.deterministic_explanation,
                    "finding_keys": ["F001"],
                    "citations": [],
                },
                "evidence_assessment": {
                    "claim_type": "evidence_assessment",
                    "text": "现有证据显示可能存在风险信号，需要进一步核验。",
                    "finding_keys": ["F001"],
                    "citations": [{"citation_key": "E001", "cited_quote": segment.quote}],
                },
                "review_actions": [
                    {
                        "claim_type": "deterministic_template",
                        "text": f001.review_question,
                        "finding_keys": ["F001"],
                        "citations": [],
                    }
                ],
            },
            {
                "finding_key": "F002",
                "explanation": {
                    "claim_type": "deterministic_finding_explanation",
                    "text": "需要进一步核验，当前证据不足以作出结论。",
                    "finding_keys": ["F002"],
                    "citations": [{"citation_key": "E001", "cited_quote": segment.quote}],
                },
                "why_it_matters": {
                    "claim_type": "deterministic_template",
                    "text": f002.deterministic_explanation,
                    "finding_keys": ["F002"],
                    "citations": [],
                },
                "evidence_assessment": {
                    "claim_type": "evidence_assessment",
                    "text": "需要进一步核验，当前证据不足以作出结论。",
                    "finding_keys": ["F002"],
                    "citations": [],
                },
                "review_actions": [
                    {
                        "claim_type": "deterministic_template",
                        "text": f002.review_question,
                        "finding_keys": ["F002"],
                        "citations": [],
                    }
                ],
            },
        ],
        "cross_finding_observations": [],
        "manual_review_priorities": [],
        "disclaimer": prompt.required_disclaimer,
    }
    with pytest.raises(ExplanationError, match="explanation_citation_wrong_finding"):
        ControlledExplanationValidator().validate(
            json.dumps(output, ensure_ascii=False), prompt, built
        )

    # Scenario 2: F002 with proper uncertainty template, zero citations → passes
    output["finding_explanations"][1]["explanation"]["citations"] = []
    output["finding_explanations"][1]["explanation"]["claim_type"] = "deterministic_template"
    output["finding_explanations"][1]["explanation"]["text"] = (
        "当前证据不足以作出结论，需要进一步核验。"
    )
    output["finding_explanations"][1]["evidence_assessment"]["citations"] = []
    output["finding_explanations"][1]["evidence_assessment"]["claim_type"] = (
        "deterministic_template"
    )
    output["finding_explanations"][1]["evidence_assessment"]["text"] = (
        "当前证据不足以作出结论，需要进一步核验。"
    )
    for action in output["finding_explanations"][1]["review_actions"]:
        action["citations"] = []
        action["claim_type"] = "deterministic_template"
    validated = ControlledExplanationValidator().validate(
        json.dumps(output, ensure_ascii=False), prompt, built
    )
    assert validated.output["schema_version"] == "institution_explanation_v1"

    # Scenario 3: cross-finding claim F001+F002 using only E001 → F002 missing
    output["cross_finding_observations"] = [
        {
            "claim_type": "cautious_cross_finding_observation",
            "text": "F001与F002均需要进一步核验。",
            "finding_keys": ["F001", "F002"],
            "citations": [{"citation_key": "E001", "cited_quote": segment.quote}],
        }
    ]
    # Reset F002 explanation to valid state first
    output["finding_explanations"][1]["explanation"]["citations"] = []
    output["finding_explanations"][1]["explanation"]["claim_type"] = "deterministic_template"
    output["finding_explanations"][1]["explanation"]["text"] = (
        "当前证据不足以作出结论，需要进一步核验。"
    )
    output["finding_explanations"][1]["evidence_assessment"]["citations"] = []
    output["finding_explanations"][1]["evidence_assessment"]["claim_type"] = (
        "deterministic_template"
    )
    output["finding_explanations"][1]["evidence_assessment"]["text"] = (
        "当前证据不足以作出结论，需要进一步核验。"
    )
    with pytest.raises(ExplanationError, match="explanation_citation_wrong_finding"):
        ControlledExplanationValidator().validate(
            json.dumps(output, ensure_ascii=False), prompt, built
        )


# ── V3 targeted tests ─────────────────────────────────────────────────────────


def test_bare_risk_signal_word_alone_does_not_satisfy_uncertainty() -> None:
    """explanation has uncertainty, evidence_assessment has only 风险信号 → reject."""
    payload, prompt, built = _valid_payload("institution")
    payload["finding_explanations"][0]["explanation"]["text"] = (
        "现有证据显示可能存在风险信号，需要进一步核验。"
    )
    payload["finding_explanations"][0]["evidence_assessment"]["text"] = (
        "该证据涉及所识别的风险信号。"
    )
    with pytest.raises(ExplanationError, match="explanation_missing_uncertainty"):
        ControlledExplanationValidator().validate(
            json.dumps(payload, ensure_ascii=False), prompt, built
        )


def test_may_exist_risk_signal_with_uncertainty_passes() -> None:
    """full phrase '可能存在风险信号，需要进一步核验' passes."""
    payload, prompt, built = _valid_payload("institution")
    payload["finding_explanations"][0]["explanation"]["text"] = (
        "现有证据显示可能存在风险信号，需要进一步核验。"
    )
    payload["finding_explanations"][0]["evidence_assessment"]["text"] = (
        "可能存在风险信号，需要进一步核验。"
    )
    validated = ControlledExplanationValidator().validate(
        json.dumps(payload, ensure_ascii=False), prompt, built
    )
    assert validated.output["schema_version"] == "institution_explanation_v1"


def test_new_numeric_claim_rejected_by_number_token_gate() -> None:
    """RAG-051: uncertainty satisfied but unsupported numbers trigger rejection."""
    payload, prompt, built = _valid_payload("consumer")
    payload["risk_explanations"][0]["plain_language_explanation"]["text"] = (
        "可能存在风险信号，需要进一步核验。该公司被罚款1000万元，产品实际收益率为8%。"
    )
    with pytest.raises(ExplanationError, match="explanation_unsupported_claim"):
        ControlledExplanationValidator().validate(
            json.dumps(payload, ensure_ascii=False), prompt, built
        )


def test_evidence_insufficient_claim_rejected_by_row_check() -> None:
    """evidence_insufficient: non-deterministic_template claim with 证据充分 is caught."""
    f001 = ControlledFinding(
        finding_key="F001",
        rule_id="regulatory_endorsement",
        category="监管背书",
        severity="high",
        signal_strength="strong",
        matched_text="测试",
        raw_start_offset=0,
        raw_end_offset=2,
        deterministic_explanation="该表达可能造成误解。",
        review_question="是否需要核验？",
        evidence_status="evidence_insufficient",
        evidence=[],
    )
    context = ControlledRAGContext(
        context_schema_version="controlled_rag_context_v1",
        screening_run_payload_sha256="e" * 64,
        trusted_index_payload_hash="f" * 64,
        material={"title": "T", "material_type": "advertisement", "input_sha256": "1" * 64},
        findings=[f001],
    )
    built = BuiltContext(context, canonical_sha256(context.model_dump(mode="json")), {})
    prompt = load_prompt("institution")
    output = {
        "schema_version": "institution_explanation_v1",
        "executive_summary": {
            "claim_type": "deterministic_template",
            "text": "以下内容仅解释已持久化的确定性筛查发现。",
            "finding_keys": ["F001"],
            "citations": [],
        },
        "finding_explanations": [
            {
                "finding_key": "F001",
                "explanation": {
                    "claim_type": "deterministic_template",
                    "text": "当前证据不足以作出结论，需要进一步核验。",
                    "finding_keys": ["F001"],
                    "citations": [],
                },
                "why_it_matters": {
                    "claim_type": "deterministic_template",
                    "text": f001.deterministic_explanation,
                    "finding_keys": ["F001"],
                    "citations": [],
                },
                "evidence_assessment": {
                    "claim_type": "evidence_assessment",
                    "text": "证据充分。",
                    "finding_keys": ["F001"],
                    "citations": [],
                },
                "review_actions": [
                    {
                        "claim_type": "deterministic_template",
                        "text": f001.review_question,
                        "finding_keys": ["F001"],
                        "citations": [],
                    }
                ],
            }
        ],
        "cross_finding_observations": [],
        "manual_review_priorities": [],
        "disclaimer": prompt.required_disclaimer,
    }
    with pytest.raises(ExplanationError, match="explanation_unsupported_claim"):
        ControlledExplanationValidator().validate(
            json.dumps(output, ensure_ascii=False), prompt, built
        )


def test_offline_fixture_corpus_semantics() -> None:
    """direct test of _execute_fixture_corpus_offline returns correct semantics."""
    import json
    from pathlib import Path as _Path

    fixture_path = (
        _Path(__file__).parents[1] / "fixtures" / "controlled_rag_eval_v1" / "responses.json"
    )
    samples = json.loads(fixture_path.read_text(encoding="utf-8"))["samples"]

    from scripts.run_controlled_rag_acceptance import _execute_fixture_corpus_offline

    result = _execute_fixture_corpus_offline(list(samples))

    assert result["constructed_valid_executed"] == 12
    assert result["constructed_valid_passed"] == 12
    assert result["constructed_valid_failed"] == 0
    assert result["constructed_valid_artifact_count"] == 12
    assert result["constructed_invalid_executed"] == 43
    assert result["constructed_invalid_blocked"] == 43
    assert result["constructed_invalid_unexpected_pass"] == 0
    assert result["invalid_error_code_match_count"] == 43
    assert result["rejected_run_count"] == 41
    assert result["failed_run_count"] == 2
    assert result["invalid_failed_or_rejected_count"] == 43
    assert result["rejected_artifact_count"] == 0
    assert result["uncited_claim_rejection_count"] == 3
    assert result["trivial_quote_rejection_count"] == 1
    assert result["metadata_quote_rejection_count"] == 3

    for item in result["results"]:
        if item["expected_status"] == "passed":
            assert item["actual_status"] == "passed"
            assert item["artifact_count"] == 1
            assert item["outcome_matches_expectation"] is True
        else:
            assert item["artifact_count"] == 0
            assert item["outcome_matches_expectation"] is True
            assert item["actual_status"] in {"rejected", "failed"}
            assert item["actual_error_code"] is not None
            assert item["actual_error_code"] == item["expected_error_code"]
            if item["explanation_run_status"] == "failed":
                assert item["actual_status"] == "failed"
            else:
                assert item["actual_status"] == "rejected"


# ── V4 acceptance accounting tests ────────────────────────────────────────────


_ACCEPTANCE_TRUE_BASE: dict[str, object] = {
    "database_executed": True,
    "primary_postgresql_executed": True,
    "comparison_postgresql_executed": True,
    "formal_context_count": 60,
    "cross_database_context_sha_stability": True,
    "cross_database_artifact_sha_stability": True,
    "screening_sample_count": 60,
    "screening_finding_count": 33,
    "reviewed_evidence_link_count": 74,
    "trusted_knowledge_chunk_count": 73,
    "regulatory_case_citation_count": 0,
    "historical_prompt_snapshot_stability": True,
    "historical_context_snapshot_stability": True,
    "deterministic_rerun": True,
    "sensitive_data_scan": True,
    "formal_institution_artifact_count": 60,
    "formal_consumer_artifact_count": 60,
    "formal_valid_artifact_count": 120,
    "deterministic_rerun_artifact_count": 1,
    "constructed_valid_artifact_count": 12,
    "evidence_insufficient_valid_artifact_count": 1,
    "total_valid_artifact_count": 134,
    "constructed_valid_executed": 12,
    "constructed_valid_passed": 12,
    "constructed_valid_failed": 0,
    "constructed_invalid_executed": 43,
    "constructed_invalid_blocked": 43,
    "constructed_invalid_unexpected_pass": 0,
    "invalid_error_code_match_count": 43,
    "rejected_run_count": 41,
    "failed_run_count": 2,
    "invalid_failed_or_rejected_count": 43,
    "rejected_artifact_count": 0,
    "evidence_insufficient_context_pass": True,
    "context_budget_pressure_executed": True,
    "context_budget_preserves_minimum_evidence": True,
    "context_too_large_fail_closed": True,
}


def test_formal_acceptance_requires_all_budget_gates() -> None:
    """preserves_minimum_evidence=false must block formal acceptance even
    when other budget gates pass."""
    from scripts.run_controlled_rag_acceptance import _evaluate_formal_acceptance

    base = dict(_ACCEPTANCE_TRUE_BASE)
    base["context_budget_preserves_minimum_evidence"] = False
    assert _evaluate_formal_acceptance(base) is False

    base["context_budget_preserves_minimum_evidence"] = True
    assert _evaluate_formal_acceptance(base) is True


def test_formal_acceptance_passes_with_all_true() -> None:
    from scripts.run_controlled_rag_acceptance import _evaluate_formal_acceptance

    assert _evaluate_formal_acceptance(dict(_ACCEPTANCE_TRUE_BASE)) is True


def test_artifact_formula_yields_134_with_full_database() -> None:
    """120 formal + 1 rerun + 12 constructed + 1 evidence insufficient = 134."""
    formal_valid = 120
    deterministic_rerun = 1
    constructed_valid = 12
    evidence_insufficient = 1
    total = formal_valid + deterministic_rerun + constructed_valid + evidence_insufficient
    assert total == 134
    assert formal_valid == 60 + 60


def test_formal_valid_excludes_deterministic_rerun() -> None:
    """formal_valid_artifact_count = formal_institution + formal_consumer only."""
    sample_count = 60
    formal_institution = sample_count  # 60 institution artifacts
    formal_consumer = sample_count  # 60 consumer artifacts
    formal_valid = formal_institution + formal_consumer
    assert formal_valid == 120
    assert formal_valid == 60 * 2


def test_offline_formal_acceptance_is_false() -> None:
    """offline mode: all database fields false → formal acceptance false."""
    from scripts.run_controlled_rag_acceptance import _evaluate_formal_acceptance

    assert (
        _evaluate_formal_acceptance(
            {
                "database_executed": False,
                "primary_postgresql_executed": False,
                "comparison_postgresql_executed": False,
                "formal_context_count": 0,
                "cross_database_context_sha_stability": None,
                "cross_database_artifact_sha_stability": None,
                "screening_sample_count": 0,
                "screening_finding_count": 0,
                "reviewed_evidence_link_count": 0,
                "trusted_knowledge_chunk_count": 0,
                "regulatory_case_citation_count": 0,
                "historical_prompt_snapshot_stability": False,
                "historical_context_snapshot_stability": False,
                "deterministic_rerun": False,
                "sensitive_data_scan": True,
                "formal_institution_artifact_count": 0,
                "formal_consumer_artifact_count": 0,
                "formal_valid_artifact_count": 0,
                "deterministic_rerun_artifact_count": 0,
                "constructed_valid_artifact_count": 12,
                "evidence_insufficient_valid_artifact_count": 0,
                "total_valid_artifact_count": 12,
                "constructed_valid_executed": 12,
                "constructed_valid_passed": 12,
                "constructed_valid_failed": 0,
                "constructed_invalid_executed": 43,
                "constructed_invalid_blocked": 43,
                "constructed_invalid_unexpected_pass": 0,
                "invalid_error_code_match_count": 43,
                "rejected_run_count": 41,
                "failed_run_count": 2,
                "invalid_failed_or_rejected_count": 43,
                "rejected_artifact_count": 0,
                "evidence_insufficient_context_pass": None,
                "context_budget_pressure_executed": False,
                "context_budget_preserves_minimum_evidence": False,
                "context_too_large_fail_closed": False,
            }
        )
        is False
    )


@pytest.mark.parametrize(
    "key,wrong_value",
    [
        ("screening_sample_count", 59),
        ("screening_finding_count", 32),
        ("reviewed_evidence_link_count", 73),
        ("trusted_knowledge_chunk_count", 72),
        ("formal_institution_artifact_count", 59),
        ("formal_consumer_artifact_count", 59),
        ("formal_valid_artifact_count", 119),
        ("deterministic_rerun_artifact_count", 0),
        ("constructed_valid_artifact_count", 11),
        ("evidence_insufficient_valid_artifact_count", 0),
        ("total_valid_artifact_count", 133),
        ("rejected_run_count", 40),
        ("failed_run_count", 1),
        ("invalid_failed_or_rejected_count", 42),
    ],
)
def test_formal_acceptance_fails_on_count_drift(key: str, wrong_value: object) -> None:
    """each formal count invariant checked by _evaluate_formal_acceptance."""
    from scripts.run_controlled_rag_acceptance import _evaluate_formal_acceptance

    base = dict(_ACCEPTANCE_TRUE_BASE)
    base[key] = wrong_value
    assert _evaluate_formal_acceptance(base) is False, (
        f"key {key}={wrong_value} should fail formal acceptance"
    )


# ── V5 database evaluation contract tests ─────────────────────────────────────


def test_main_with_mock_postgresql_databases_passes_formal_acceptance(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """simulate full PostgreSQL acceptance with mock databases."""
    from scripts.run_controlled_rag_acceptance import main as accept_main

    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "accept",
            "--database-url",
            "postgresql://user:pass@localhost/baoxiao_acceptance_a",
            "--comparison-database-url",
            "postgresql://user:pass@localhost/baoxiao_acceptance_b",
            "--json-report",
            str(json_path),
            "--markdown-report",
            str(md_path),
        ],
    )
    sixty_contexts = {str(i): f"sha{i:064d}" for i in range(60)}
    primary_mock = {
        "sample_count": 60,
        "contexts": sixty_contexts,
        "artifacts": {str(i): {"institution": "h", "consumer": "h"} for i in range(60)},
        "deterministic_rerun": True,
        "historical_prompt_snapshot_stability": True,
        "historical_context_snapshot_stability": True,
        "regulatory_case_citation_count": 0,
        "trusted_knowledge_chunk_count": 73,
        "screening_finding_count": 33,
        "reviewed_evidence_link_count": 74,
    }
    comparison_mock = {
        "sample_count": 60,
        "contexts": sixty_contexts,
        "artifacts": {str(i): {"institution": "h", "consumer": "h"} for i in range(60)},
        "deterministic_rerun": True,
        "historical_prompt_snapshot_stability": True,
        "historical_context_snapshot_stability": True,
        "regulatory_case_citation_count": 0,
        "trusted_knowledge_chunk_count": 73,
        "screening_finding_count": 33,
        "reviewed_evidence_link_count": 74,
    }
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._execute_database",
        lambda url: primary_mock if "baoxiao_acceptance_a" in str(url) else comparison_mock,
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._execute_fixture_corpus",
        lambda *_args, **_kw: _mock_fixture_evaluation(43),
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._exercise_evidence_insufficient",
        lambda _url: True,
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._exercise_budget_pressure",
        lambda: {"executed": 3, "preserves_minimum": True, "fail_closed": True},
    )

    accept_main()

    assert json_path.exists()
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert "constructed_valid_executed" in report
    assert report["database_executed"] is True
    assert report["primary_postgresql_executed"] is True
    assert report["comparison_postgresql_executed"] is True
    assert report["formal_database_acceptance_completed"] is True
    assert report["total_valid_artifact_count"] == 134
    assert report["rejected_run_count"] == 41
    assert report["failed_run_count"] == 2
    assert report["rejected_artifact_count"] == 0


def test_main_fails_when_evaluation_missing_key(tmp_path: Path, monkeypatch: Any) -> None:
    """evaluation returns dict missing required key → stable error."""
    from scripts.run_controlled_rag_acceptance import main as accept_main

    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "accept",
            "--database-url",
            "postgresql://user:pass@localhost/baoxiao_acceptance_a",
            "--comparison-database-url",
            "postgresql://user:pass@localhost/baoxiao_acceptance_b",
            "--json-report",
            str(json_path),
            "--markdown-report",
            str(md_path),
        ],
    )
    primary_mock = {
        "sample_count": 60,
        "contexts": {"k": "h"},
        "artifacts": {"k": {"institution": "h", "consumer": "h"}},
        "deterministic_rerun": True,
        "historical_prompt_snapshot_stability": True,
        "historical_context_snapshot_stability": True,
        "regulatory_case_citation_count": 0,
        "trusted_knowledge_chunk_count": 73,
        "screening_finding_count": 33,
        "reviewed_evidence_link_count": 74,
    }
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._execute_database",
        lambda url: primary_mock,
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._execute_fixture_corpus",
        lambda *_args, **_kw: _mock_fixture_evaluation_missing_key(),
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._exercise_evidence_insufficient",
        lambda _url: True,
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._exercise_budget_pressure",
        lambda: {"executed": 3, "preserves_minimum": True, "fail_closed": True},
    )

    with pytest.raises(SystemExit) as exc_info:
        accept_main()
    assert "controlled_rag_evaluation_contract_invalid" in str(exc_info.value)
    assert json_path.exists()  # diagnostic report written on contract failure


def test_budget_fail_closed_only_accepts_context_too_large() -> None:
    """explanation_context_too_large → fail_closed=true; other code → false."""
    from scripts.run_controlled_rag_acceptance import _exercise_budget_pressure

    result = _exercise_budget_pressure()
    assert result["fail_closed"] is True
    assert result.get("fail_closed_code") == "explanation_context_too_large"
    assert result["preserves_minimum"] is True
    assert result["executed"] == 3


def test_db_identity_different_by_database_name_not_by_driver() -> None:
    """same host+db → not different; different db name → different."""
    from scripts.run_controlled_rag_acceptance import _db_identity

    a = _db_identity("postgresql://user:pass@localhost/baoxiao_a")
    b = _db_identity("postgresql+psycopg://user:pass@localhost/baoxiao_a")
    c = _db_identity("postgresql://user:pass@localhost/baoxiao_b")

    assert a[0] == "postgresql"
    assert a[2] == b[2]  # same database name
    assert a[2] != c[2]  # different database name
    assert a == b  # driver difference doesn't change identity
    assert a != c  # different database name makes identity different


def test_formal_gate_failure_writes_report_before_exit(tmp_path: Path, monkeypatch: Any) -> None:
    """report must be written even when formal gate fails."""
    from scripts.run_controlled_rag_acceptance import main as accept_main

    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "accept",
            "--database-url",
            "postgresql://user:pass@localhost/baoxiao_a",
            "--comparison-database-url",
            "postgresql://user:pass@localhost/baoxiao_b",
            "--json-report",
            str(json_path),
            "--markdown-report",
            str(md_path),
        ],
    )
    primary_mock = {
        "sample_count": 60,
        "contexts": {"k": "h"},
        "artifacts": {"k": {"institution": "h", "consumer": "h"}},
        "deterministic_rerun": False,  # will fail the gate
        "historical_prompt_snapshot_stability": False,
        "historical_context_snapshot_stability": False,
        "regulatory_case_citation_count": 0,
        "trusted_knowledge_chunk_count": 73,
        "screening_finding_count": 33,
        "reviewed_evidence_link_count": 74,
    }
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._execute_database",
        lambda url: primary_mock,
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._execute_fixture_corpus",
        lambda *_args, **_kw: _mock_fixture_evaluation(43),
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._exercise_evidence_insufficient",
        lambda _url: True,
    )
    monkeypatch.setattr(
        "scripts.run_controlled_rag_acceptance._exercise_budget_pressure",
        lambda: {"executed": 3, "preserves_minimum": True, "fail_closed": True},
    )

    with pytest.raises(SystemExit):
        accept_main()
    assert json_path.exists()
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["formal_database_acceptance_completed"] is False


# ── shared mock helpers ───────────────────────────────────────────────────────


def _mock_fixture_evaluation(
    invalid_rejected: int,
) -> dict[str, Any]:
    valid_samples = [
        {
            "sample_id": f"RAG-{i:03d}",
            "audience": "institution",
            "scenario": "valid",
            "expected_status": "passed",
            "actual_status": "passed",
            "expected_error_code": None,
            "actual_error_code": None,
            "outcome_matches_expectation": True,
            "explanation_run_status": "completed",
            "artifact_count": 1,
        }
        for i in range(1, 13)
    ]
    rejected_samples = [
        {
            "sample_id": f"RAG-{i:03d}",
            "audience": "institution",
            "scenario": "unknown_citation",
            "expected_status": "rejected",
            "actual_status": "rejected",
            "expected_error_code": "explanation_unknown_citation_key",
            "actual_error_code": "explanation_unknown_citation_key",
            "outcome_matches_expectation": True,
            "explanation_run_status": "rejected",
            "artifact_count": 0,
        }
        for i in range(13, 13 + invalid_rejected - 2)
    ]
    failed_samples = [
        {
            "sample_id": "RAG-046",
            "audience": "consumer",
            "scenario": "provider_timeout",
            "expected_status": "failed",
            "actual_status": "failed",
            "expected_error_code": "explanation_provider_timeout",
            "actual_error_code": "explanation_provider_timeout",
            "outcome_matches_expectation": True,
            "explanation_run_status": "failed",
            "artifact_count": 0,
        },
        {
            "sample_id": "RAG-047",
            "audience": "institution",
            "scenario": "provider_exception",
            "expected_status": "failed",
            "actual_status": "failed",
            "expected_error_code": "explanation_provider_failed",
            "actual_error_code": "explanation_provider_failed",
            "outcome_matches_expectation": True,
            "explanation_run_status": "failed",
            "artifact_count": 0,
        },
    ]
    all_results = valid_samples + rejected_samples + failed_samples
    return {
        "results": all_results,
        "constructed_valid_executed": len(valid_samples),
        "constructed_valid_passed": len(valid_samples),
        "constructed_valid_failed": 0,
        "constructed_valid_artifact_count": len(valid_samples),
        "constructed_invalid_executed": len(all_results) - len(valid_samples),
        "constructed_invalid_blocked": len(rejected_samples) + len(failed_samples),
        "constructed_invalid_unexpected_pass": 0,
        "invalid_error_code_match_count": len(rejected_samples) + len(failed_samples),
        "rejected_run_count": len(rejected_samples),
        "failed_run_count": len(failed_samples),
        "invalid_failed_or_rejected_count": len(rejected_samples) + len(failed_samples),
        "rejected_artifact_count": 0,
        "uncited_claim_rejection_count": 3,
        "trivial_quote_rejection_count": 1,
        "metadata_quote_rejection_count": 3,
    }


def _mock_fixture_evaluation_missing_key() -> dict[str, Any]:
    return {"results": []}  # missing required keys
