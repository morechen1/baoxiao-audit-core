from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.evaluation.core import (
    EvaluationRunner,
    ExposureAuditor,
    ManifestError,
    ReportStore,
    compute_metrics,
    seal_manifest,
    validate_manifest,
)


def manifest(
    track: str = "constructed", cases: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "schema_version": "evaluation_manifest_v1",
        "track": track,
        "version": f"{track}_test_v1",
        "status": "DRAFT",
        "cases": cases or [],
        "provenance": [],
    }


def test_manifest_requires_seal_and_detects_sha_or_parent_drift() -> None:
    draft = manifest(cases=[{"case_id": "C1", "expected_rule_ids": [], "text": "文本"}])
    with pytest.raises(ManifestError, match="not_sealed"):
        validate_manifest(draft, require_sealed=True)
    reviewed = {**draft, "status": "HUMAN_REVIEWED"}
    with pytest.raises(ManifestError, match="not_sealed"):
        validate_manifest(reviewed, require_sealed=True)
    sealed = seal_manifest(draft, actor="test", note="seal", parent_sha256=None)
    assert validate_manifest(sealed, require_sealed=True)["status"] == "SEALED"
    drifted = {**sealed, "cases": [{"case_id": "C1", "expected_rule_ids": ["x"], "text": "文本"}]}
    with pytest.raises(ManifestError, match="sha_drift"):
        validate_manifest(drifted)
    bad_parent = {**sealed, "provenance": [{**sealed["provenance"][0], "parent_sha256": "wrong"}]}
    with pytest.raises(ManifestError, match="parent_mismatch"):
        validate_manifest(bad_parent)


def test_multilabel_metrics_keep_zero_denominators_and_strict_rates() -> None:
    metrics = compute_metrics(
        [
            {"expected": ["a", "b"], "predicted": ["a", "c"]},
            {"expected": [], "predicted": ["c"]},
            {"expected": [], "predicted": []},
        ],
        label_universe={"a", "b", "c", "never"},
    )
    assert metrics["counts"] == {
        "tp": 1,
        "fp": 2,
        "fn": 1,
        "exact": 1,
        "positive_cases": 1,
        "zero_label_cases": 2,
        "pair_total": 12,
    }
    assert metrics["micro_precision"] == pytest.approx(1 / 3)
    assert metrics["micro_recall"] == pytest.approx(1 / 2)
    assert metrics["zero_label_false_alarm_rate"] == pytest.approx(1 / 2)
    assert metrics["fnr"] == pytest.approx(1 / 2)
    assert metrics["per_category"]["never"]["precision"] is None
    assert metrics["per_category"]["never"]["recall"] is None


@dataclass
class Finding:
    rule_id: str


@dataclass
class FakeRun:
    id: int
    findings: list[Finding]
    status: str = "completed"
    ruleset_sha256: str = "rules"
    run_payload_sha256: str = "payload"
    error_code: str | None = None


class FakeScreening:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def run(self, _session: object, **_kwargs: object) -> FakeRun:
        if self.fail:
            raise RuntimeError("screening_failed")
        return FakeRun(id=7, findings=[Finding("guaranteed_return"), Finding("guaranteed_return")])


def test_runner_deduplicates_rule_ids_and_marks_batch_invalid_on_failure() -> None:
    cases = [
        {
            "case_id": "C1",
            "material_type": "sales_script",
            "text": "保证收益",
            "expected_rule_ids": ["guaranteed_return"],
            "difficulty": "explicit",
        }
    ]
    sealed = seal_manifest(manifest(cases=cases), actor="test", note="seal", parent_sha256=None)
    settings = Settings(database_url="sqlite:///evaluation-test.db")
    success = EvaluationRunner(settings, service_factory=lambda: FakeScreening())
    report = success.run_constructed(object(), sealed)
    assert report["status"] == "COMPLETED"
    assert report["per_case"][0]["predicted"] == ["guaranteed_return"]
    assert report["metrics"]["counts"]["tp"] == 1
    failed = EvaluationRunner(settings, service_factory=lambda: FakeScreening(fail=True))
    invalid = failed.run_constructed(object(), sealed)
    assert invalid["status"] == "INVALID"
    assert invalid["metrics"] is None


def test_provisional_runner_does_not_bypass_formal_sealed_gate() -> None:
    draft = manifest(
        cases=[
            {
                "case_id": "C1",
                "material_type": "sales_script",
                "text": "保证收益",
                "expected_rule_ids": ["guaranteed_return"],
            }
        ]
    )
    draft["content_sha256"] = "candidate-only"
    runner = EvaluationRunner(
        Settings(database_url="sqlite:///evaluation-test.db"),
        service_factory=lambda: FakeScreening(),
    )
    report = runner.run_constructed_provisional(object(), draft)
    assert report["status"] == "PROVISIONAL"
    assert report["provisional"] is True
    with pytest.raises(ManifestError, match="not_sealed"):
        runner.run_constructed(object(), draft)


def test_external_threshold_and_exposure_exclusion() -> None:
    cases = [
        {
            "case_id": f"O{i}",
            "material_type": "sales_script",
            "relevant_excerpt": "保证收益",
            "expected_rule_ids": ["guaranteed_return"],
            "scope_status": "in_scope",
            "canonical_url": f"https://official.example/{i}",
        }
        for i in range(30)
    ]
    cases[0]["contamination_status"] = "external_test_candidate"
    sealed = seal_manifest(
        manifest("external", cases), actor="test", note="seal", parent_sha256=None
    )
    runner = EvaluationRunner(
        Settings(database_url="sqlite:///evaluation-test.db"),
        service_factory=lambda: FakeScreening(),
    )
    report = runner.run_external(
        object(), sealed, ExposureAuditor({"coverage_status": "disclosed_partial", "records": []})
    )
    assert report["status"] == "INSUFFICIENT"
    assert report["independent_in_scope_count"] == 29
    assert report["metrics"] is None
    assert report["per_case"][0]["contamination_status"] == "exposed"


def test_external_provisional_keeps_out_of_scope_rows_and_is_not_formal() -> None:
    draft = manifest(
        "external",
        [
            {
                "case_id": "O1",
                "material_type": "sales_script",
                "relevant_excerpt": "保证收益",
                "expected_rule_ids": ["guaranteed_return"],
                "scope_status": "in_scope",
                "canonical_url": "https://official.example/1",
            },
            {
                "case_id": "O2",
                "material_type": "other",
                "relevant_excerpt": "不纳入评测范围",
                "expected_rule_ids": [],
                "scope_status": "out_of_scope",
                "canonical_url": "https://official.example/2",
            },
        ],
    )
    draft["content_sha256"] = "candidate-only"
    runner = EvaluationRunner(
        Settings(database_url="sqlite:///evaluation-test.db"),
        service_factory=lambda: FakeScreening(),
    )
    report = runner.run_external_provisional(
        object(), draft, ExposureAuditor({"coverage_status": "disclosed_partial", "records": []})
    )
    assert report["status"] == "PROVISIONAL"
    assert report["provisional"] is True
    assert report["in_scope_count"] == 1
    assert report["out_of_scope_count"] == 1
    assert report["sample_counts"]["total"] == 2


def test_atomic_report_read_detects_sha_drift(tmp_path: Path) -> None:
    store = ReportStore(tmp_path)
    path = store.write("constructed", {"status": "COMPLETED", "dataset_sha256": "abc"})
    assert store.read("constructed")["status"] == "COMPLETED"
    path.write_text('{"status":"COMPLETED","report_sha256":"wrong"}', encoding="utf-8")
    assert store.read("constructed")["status"] == "INVALID"
