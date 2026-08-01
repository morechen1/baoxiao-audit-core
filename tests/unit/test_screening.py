from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.dependencies import get_db
from app.core.exceptions import ScreeningError
from app.main import app
from app.models import (
    FindingEvidenceLink,
    KnowledgeChunk,
    MarketingMaterial,
    MaterialSegment,
    RiskFinding,
    ScreeningRun,
    SourceDocument,
)
from app.services.knowledge import KnowledgeIndexService, SearchResult
from app.services.screening import (
    DeterministicComplianceRuleEngine,
    DeterministicScreeningService,
    MarketingRuleSet,
    load_ruleset,
    normalize_marketing_text,
    segment_marketing_text,
)
from app.services.screening.evidence import FindingEvidenceAssembler

FIXTURE = Path(__file__).parents[1] / "fixtures" / "constructed_screening_eval_v1" / "samples.json"


def _engine(text: str):
    material_sha = "1" * 64
    segments = segment_marketing_text(text, material_sha)
    return DeterministicComplianceRuleEngine(load_ruleset()).run(
        raw_text=text,
        material_sha256=material_sha,
        segments=segments,
    )


def test_normalization_nfkc_unicode_whitespace_and_raw_offset() -> None:
    raw = "Ａ保证\n  收益％"
    normalized = normalize_marketing_text(raw)
    assert normalized.text == "a保证 收益%"
    start = normalized.text.index("保证")
    end = len(normalized.text) - 1
    raw_start, raw_end = normalized.raw_span(start, end)
    assert raw[raw_start:raw_end] == "保证\n  收益"


def test_segmentation_and_sha_are_deterministic() -> None:
    raw = "短标题\n\n保证收益。第二段没有风险提示；第三段。"
    first = segment_marketing_text(raw, "2" * 64)
    second = segment_marketing_text(raw, "2" * 64)
    assert first == second
    assert first[0].text.startswith("短标题")
    assert all(raw[item.raw_start_offset : item.raw_end_offset] == item.text for item in first)


def test_ruleset_schema_sha_and_order_are_strict() -> None:
    ruleset = load_ruleset()
    assert len(ruleset.rules) == 12
    assert len(ruleset.sha256) == 64
    assert [rule.rule_id for rule in ruleset.rules] == sorted(
        rule.rule_id for rule in ruleset.rules
    )
    payload = ruleset.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        MarketingRuleSet.model_validate(payload)


@pytest.mark.parametrize(
    ("text", "rule_id"),
    [
        ("保证 收益", "guaranteed_return_or_principal"),
        ("零风险", "no_risk_or_no_loss"),
        ("结算利率固定", "misleading_interest_or_yield"),
        ("监管\n推荐", "regulatory_endorsement"),
        ("立即投保即可中奖", "false_promotion_or_prize"),
        ("购买保险赠送现金", "extra_contractual_benefit"),
        ("保险就是存款", "product_nature_confusion"),
        ("收益最高", "absolute_or_superlative_claim"),
        ("排名第一", "improper_comparison_or_ranking"),
        ("随时退保没有损失", "surrender_or_cash_value_misstatement"),
        ("没有等待期", "waiting_or_cooling_period_misstatement"),
        ("什么情况都赔", "concealment_or_minimization_of_exclusions"),
    ],
)
def test_all_twelve_rule_categories_match(text: str, rule_id: str) -> None:
    assert rule_id in {finding.rule_id for finding in _engine(text)}


@pytest.mark.parametrize(
    "text",
    [
        "本产品不保证收益，实际结果以合同为准。",
        "请保证材料真实性并签字。",
        "普通商场优惠，不含投保或保险购买。",
        "监管备案不等于监管保证。",
    ],
)
def test_exception_patterns_fail_closed_without_false_positive(text: str) -> None:
    assert _engine(text) == []


def test_cross_line_match_returns_exact_raw_span() -> None:
    text = "开场：监管\n推荐本产品。"
    finding = next(value for value in _engine(text) if value.rule_id == "regulatory_endorsement")
    assert text[finding.raw_start_offset : finding.raw_end_offset] == "监管\n推荐"
    assert finding.matched_text == "监管\n推荐"


def test_overlap_merge_and_finding_sha_are_stable() -> None:
    text = "保本保息，保证收益。"
    first = _engine(text)
    second = _engine(text)
    assert [value.finding_sha256 for value in first] == [value.finding_sha256 for value in second]
    assert len({value.finding_sha256 for value in first}) == len(first)


def test_constructed_fixture_exact_rules_and_spans() -> None:
    samples = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(samples) == 30
    assert sum(bool(sample["expected_rule_ids"]) for sample in samples) == 18
    for sample in samples:
        findings = _engine(sample["text"])
        actual_rules = sorted({finding.rule_id for finding in findings})
        assert actual_rules == sorted(sample["expected_rule_ids"]), sample["sample_id"]
        actual_spans = [
            {
                "rule_id": finding.rule_id,
                "start": finding.raw_start_offset,
                "end": finding.raw_end_offset,
                "text": finding.matched_text,
            }
            for finding in findings
        ]
        assert actual_spans == sample["expected_spans"], sample["sample_id"]
        assert not set(sample["forbidden_rule_ids"]) & set(actual_rules)


def test_empty_index_screening_is_atomic_and_evidence_insufficient(session) -> None:
    service = DeterministicScreeningService()
    run = service.run(
        session,
        title="测试营销话术",
        material_type="sales_script",
        raw_text="保证收益，零风险。",
        source_label="unit_test",
        is_constructed_evaluation=True,
    )
    assert run.status == "completed"
    assert run.finding_count == 2
    assert run.insufficient_evidence_count == 2
    assert session.query(SourceDocument).count() == 0
    assert session.query(KnowledgeChunk).count() == 0
    assert session.query(FindingEvidenceLink).count() == 0
    assert session.query(RiskFinding).count() == 2


def test_rerun_same_input_has_identical_findings_and_does_not_duplicate_material(session) -> None:
    service = DeterministicScreeningService()
    kwargs = {
        "title": "重复筛查",
        "material_type": "advertisement",
        "raw_text": "监管推荐，保证收益。",
        "source_label": "unit_test",
    }
    first = service.run(session, **kwargs)
    second = service.run(session, **kwargs)
    first_hashes = [value.finding_sha256 for value in first.findings]
    second_hashes = [value.finding_sha256 for value in second.findings]
    assert first_hashes == second_hashes
    assert first.run_payload_sha256 == second.run_payload_sha256
    assert session.query(MarketingMaterial).count() == 1
    assert session.query(ScreeningRun).count() == 2


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"raw_text": ""}, "screening_material_empty"),
        ({"raw_text": "x" * 100_001}, "screening_material_too_large"),
        ({"material_type": "pdf"}, "screening_material_type_invalid"),
    ],
)
def test_input_boundaries_reject_without_partial_rows(session, kwargs, code: str) -> None:
    values = {
        "title": "边界测试",
        "material_type": "other",
        "raw_text": "正常文本",
        "source_label": "unit_test",
    }
    values.update(kwargs)
    with pytest.raises(Exception, match=code):
        DeterministicScreeningService().run(session, **values)
    assert session.query(MarketingMaterial).count() == 0
    assert session.query(MaterialSegment).count() == 0
    assert session.query(ScreeningRun).count() == 0


def test_invalid_trusted_index_fails_before_any_persistence(session, monkeypatch) -> None:
    monkeypatch.setattr(
        KnowledgeIndexService,
        "verify_chunks",
        lambda self, value: SimpleNamespace(valid=False),
    )
    with pytest.raises(ScreeningError, match="screening_trusted_index_invalid"):
        DeterministicScreeningService().run(
            session,
            title="可信索引失败",
            material_type="sales_script",
            raw_text="保证收益",
            source_label="unit_test",
        )
    assert session.query(MarketingMaterial).count() == 0
    assert session.query(ScreeningRun).count() == 0


def test_evidence_link_failure_rolls_back_material_run_and_findings(session, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise ScreeningError("screening_evidence_retrieval_failed")

    monkeypatch.setattr(FindingEvidenceAssembler, "assemble", fail)
    with pytest.raises(ScreeningError, match="screening_evidence_retrieval_failed"):
        DeterministicScreeningService().run(
            session,
            title="证据失败",
            material_type="sales_script",
            raw_text="保证收益",
            source_label="unit_test",
        )
    assert session.query(MarketingMaterial).count() == 0
    assert session.query(MaterialSegment).count() == 0
    assert session.query(ScreeningRun).count() == 0
    assert session.query(RiskFinding).count() == 0


def test_evidence_selection_keeps_required_types_deduplicated_and_bounded() -> None:
    def result(identity: str, record_type: str, document_id: int, score: float) -> SearchResult:
        return SearchResult(
            score=score,
            rank=1,
            record_type=record_type,
            pilot_id="TEST",
            source_document_id=document_id,
            structured_record_id=1,
            portable_record_key="a" * 64,
            chunk_kind="test",
            chunk_ordinal=0,
            title="测试可信证据",
            snippet="测试",
            matched_terms=["测试"],
            authority="测试机关",
            relevant_date=None,
            evidence_quality="A",
            authenticity_status="verified_public",
            review_status="approved",
            source_locator={"source_url": "https://example.test"},
            evidence_references=[{"quote": "测试"}],
            source_url="https://example.test",
            chunk_content_sha256=identity,
            chunk_identity_sha256=identity,
        )

    regulation = result("1" * 64, "regulation", 1, 10.0)
    second_regulation = result("2" * 64, "regulation", 1, 9.0)
    third_same_document = result("3" * 64, "regulation", 1, 8.0)
    product = result("4" * 64, "product_document", 2, 1.0)
    selected = FindingEvidenceAssembler._select(
        [
            ("normative_basis", regulation),
            ("normative_basis", regulation),
            ("normative_basis", second_regulation),
            ("normative_basis", third_same_document),
            ("product_term_context", product),
        ],
        ("normative_basis", "product_term_context"),
    )
    assert {support_type for support_type, _ in selected} == {
        "normative_basis",
        "product_term_context",
    }
    assert len({value.chunk_identity_sha256 for _, value in selected}) == len(selected)
    assert sum(value.source_document_id == 1 for _, value in selected) == 2


def test_ruleset_cannot_name_regulatory_case_as_preferred_evidence() -> None:
    payload = load_ruleset().model_dump(mode="json")
    payload["rules"][0]["preferred_record_types"].append("regulatory_case")
    with pytest.raises(ValidationError):
        MarketingRuleSet.model_validate(payload)


def test_institution_and_consumer_reports_are_stable_and_read_only(session) -> None:
    service = DeterministicScreeningService()
    run = service.run(
        session,
        title="报告测试",
        material_type="sales_script",
        raw_text="随时退保没有损失。",
        source_label="unit_test",
    )
    before = (session.query(RiskFinding).count(), session.query(FindingEvidenceLink).count())
    institution = service.institution_report(session, run.id)
    consumer = service.consumer_notice(session, run.id)
    assert institution == service.institution_report(session, run.id)
    assert consumer == service.consumer_notice(session, run.id)
    assert institution["manual_review_required"] is True
    assert "不构成违法认定" in institution["disclaimer"]
    assert (
        session.query(RiskFinding).count(),
        session.query(FindingEvidenceLink).count(),
    ) == before


def test_screening_api_and_safe_json_output(session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).post(
            "/api/v1/screenings",
            json={
                "title": "<script>alert(1)</script>",
                "material_type": "social_media",
                "raw_text": "<script>保证收益</script>",
                "source_label": "user_submission",
            },
        )
        assert response.status_code == 200
        run_id = response.json()["screening_run_id"]
        report = TestClient(app).get(f"/api/v1/screenings/{run_id}/institution-report")
    finally:
        app.dependency_overrides.clear()
    assert report.status_code == 200
    assert report.json()["material_title"] == "<script>alert(1)</script>"
    assert report.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("raw_text", "", "screening_material_empty"),
        ("material_type", "pdf", "screening_material_type_invalid"),
        ("title", "x" * 301, "screening_title_invalid"),
    ],
)
def test_screening_api_returns_stable_boundary_error_codes(
    session, field: str, value: str, code: str
) -> None:
    request = {
        "title": "API边界",
        "material_type": "other",
        "raw_text": "普通文本",
        "source_label": "unit_test",
    }
    request[field] = value
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).post("/api/v1/screenings", json=request)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
