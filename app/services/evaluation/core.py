"""Small, file-backed evaluation protocol; it never changes screening behaviour."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.services.screening import DeterministicScreeningService, load_ruleset

MANIFEST_SCHEMA_VERSION = "evaluation_manifest_v1"
REPORT_SCHEMA_VERSION = "evaluation_report_v1"
RUNNER_VERSION = "evaluation_runner_v1"
METRIC_SCHEMA_VERSION = "multilabel_metrics_v1"
SEALED = "SEALED"
_STATUSES = {"DRAFT", "HUMAN_REVIEWED", SEALED}
_run_lock = Lock()


class ManifestError(ValueError):
    """A benchmark artifact is malformed, mutable, or not ready to run."""


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _manifest_content(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in manifest.items() if key not in {"content_sha256", "provenance"}
    }


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return canonical_sha256(_manifest_content(manifest))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    return (
        None
        if precision is None or recall is None or precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # Exported builds intentionally have no .git directory.
        return "unavailable"


def validate_manifest(manifest: dict[str, Any], *, require_sealed: bool = True) -> dict[str, Any]:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError("evaluation_manifest_schema_invalid")
    if manifest.get("track") not in {"constructed", "external"}:
        raise ManifestError("evaluation_manifest_track_invalid")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ManifestError("evaluation_manifest_version_missing")
    if manifest.get("status") not in _STATUSES:
        raise ManifestError("evaluation_manifest_status_invalid")
    if not isinstance(manifest.get("cases"), list):
        raise ManifestError("evaluation_manifest_cases_invalid")
    case_ids = [row.get("case_id") for row in manifest["cases"] if isinstance(row, dict)]
    if len(case_ids) != len(manifest["cases"]) or any(
        not isinstance(value, str) or not value for value in case_ids
    ):
        raise ManifestError("evaluation_manifest_case_id_invalid")
    if len(set(case_ids)) != len(case_ids):
        raise ManifestError("evaluation_manifest_case_id_duplicate")
    actual_sha = manifest_sha256(manifest)
    if manifest.get("status") == SEALED:
        if manifest.get("content_sha256") != actual_sha:
            raise ManifestError("evaluation_manifest_sha_drift")
        ledger = manifest.get("provenance")
        if not isinstance(ledger, list) or not ledger:
            raise ManifestError("evaluation_manifest_provenance_missing")
        previous: str | None = None
        for event in ledger:
            if not isinstance(event, dict) or set(
                (
                    "event",
                    "version",
                    "content_sha256",
                    "parent_sha256",
                    "actor",
                    "timestamp",
                    "note",
                )
            ) - set(event):
                raise ManifestError("evaluation_manifest_provenance_invalid")
            if event["parent_sha256"] != previous:
                raise ManifestError("evaluation_manifest_provenance_parent_mismatch")
            previous = event["content_sha256"]
        if ledger[-1]["event"] != SEALED or ledger[-1]["content_sha256"] != actual_sha:
            raise ManifestError("evaluation_manifest_provenance_seal_mismatch")
    if require_sealed and manifest.get("status") != SEALED:
        raise ManifestError("evaluation_manifest_not_sealed")
    return manifest


def seal_manifest(
    manifest: dict[str, Any], *, actor: str, note: str, parent_sha256: str | None = None
) -> dict[str, Any]:
    """Return a new sealed version; callers must write it to a new artifact path."""
    if manifest.get("status") == SEALED:
        raise ManifestError("evaluation_manifest_sealed_immutable")
    updated = dict(manifest)
    updated["status"] = SEALED
    content_sha = manifest_sha256(updated)
    ledger = list(updated.get("provenance", []))
    expected_parent = ledger[-1]["content_sha256"] if ledger else None
    if parent_sha256 != expected_parent:
        raise ManifestError("evaluation_manifest_provenance_parent_mismatch")
    ledger.append(
        {
            "event": SEALED,
            "version": updated.get("version"),
            "content_sha256": content_sha,
            "parent_sha256": parent_sha256,
            "actor": actor,
            "timestamp": _now(),
            "note": note,
        }
    )
    updated["content_sha256"] = content_sha
    updated["provenance"] = ledger
    return validate_manifest(updated)


def load_manifest(path: Path, *, require_sealed: bool = False) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("evaluation_manifest_unreadable") from exc
    if not isinstance(value, dict):
        raise ManifestError("evaluation_manifest_root_invalid")
    return validate_manifest(value, require_sealed=require_sealed)


def write_manifest_new_version(path: Path, manifest: dict[str, Any]) -> None:
    """Do not permit a SEALED artifact to be changed in place."""
    if path.exists() and load_manifest(path).get("status") == SEALED:
        raise ManifestError("evaluation_manifest_sealed_immutable")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def compute_metrics(
    rows: list[dict[str, Any]], *, label_universe: set[str] | None = None
) -> dict[str, Any]:
    """Strict multilabel metrics: categories are never silently dropped."""
    labels = set(label_universe or set())
    for row in rows:
        labels.update(row["expected"])
        labels.update(row["predicted"])
    labels = set(sorted(labels))
    total_tp = total_fp = total_fn = 0
    exact = positive_cases = positive_hits = zero_cases = zero_false_alarms = 0
    per_label: dict[str, dict[str, Any]] = {
        label: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "support": 0} for label in labels
    }
    for row in rows:
        expected, predicted = set(row["expected"]), set(row["predicted"])
        exact += int(expected == predicted)
        positive_cases += int(bool(expected))
        positive_hits += int(bool(expected & predicted))
        zero_cases += int(not expected)
        zero_false_alarms += int(not expected and bool(predicted))
        total_tp += len(expected & predicted)
        total_fp += len(predicted - expected)
        total_fn += len(expected - predicted)
        for label in labels:
            bucket = per_label[label]
            bucket["tp"] += int(label in expected and label in predicted)
            bucket["fp"] += int(label not in expected and label in predicted)
            bucket["fn"] += int(label in expected and label not in predicted)
            bucket["tn"] += int(label not in expected and label not in predicted)
            bucket["support"] += int(label in expected)
    micro_p, micro_r = _ratio(total_tp, total_tp + total_fp), _ratio(total_tp, total_tp + total_fn)
    for bucket in per_label.values():
        bucket["precision"] = _ratio(bucket["tp"], bucket["tp"] + bucket["fp"])
        bucket["recall"] = _ratio(bucket["tp"], bucket["tp"] + bucket["fn"])
        bucket["f1"] = _f1(bucket["precision"], bucket["recall"])

    def macro(name: str) -> float | None:
        values = [bucket[name] for bucket in per_label.values() if bucket[name] is not None]
        return None if not values else sum(values) / len(values)

    pair_total = len(rows) * len(labels)
    return {
        "schema_version": METRIC_SCHEMA_VERSION,
        "sample_count": len(rows),
        "label_count": len(labels),
        "micro_precision": micro_p,
        "micro_recall": micro_r,
        "micro_f1": _f1(micro_p, micro_r),
        "macro_precision": macro("precision"),
        "macro_recall": macro("recall"),
        "macro_f1": macro("f1"),
        "exact_set_match": _ratio(exact, len(rows)),
        "positive_case_hit_rate": _ratio(positive_hits, positive_cases),
        "zero_label_false_alarm_rate": _ratio(zero_false_alarms, zero_cases),
        "fnr": _ratio(total_fn, total_tp + total_fn),
        "case_label_pair_fpr": _ratio(
            total_fp, sum(item["tn"] + item["fp"] for item in per_label.values())
        ),
        "counts": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "exact": exact,
            "positive_cases": positive_cases,
            "zero_label_cases": zero_cases,
            "pair_total": pair_total,
        },
        "per_category": per_label,
    }


def stratify(rows: list[dict[str, Any]], labels: set[str]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        difficulty = row.get("difficulty", "unknown")
        groups.setdefault(difficulty, []).append(row)
        groups.setdefault("multi_label" if len(row["expected"]) > 1 else "single_label", []).append(
            row
        )
        groups.setdefault(f"material_type:{row.get('material_type', 'unknown')}", []).append(row)
    return {
        name: compute_metrics(group, label_universe=labels)
        for name, group in sorted(groups.items())
    }


class ExposureAuditor:
    """Deliberately exact audit: URLs and excerpts, not fuzzy NLP."""

    def __init__(self, inventory: dict[str, Any], project_root: Path | None = None) -> None:
        self.inventory = inventory
        self.sha256 = canonical_sha256(inventory)
        self.coverage_status = str(inventory.get("coverage_status", "missing"))
        self.records = [row for row in inventory.get("records", []) if isinstance(row, dict)]
        self.project_root = project_root

    def audit(self, case: dict[str, Any]) -> dict[str, Any]:
        if case.get("contamination_status") == "external_test_candidate":
            return {"status": "exposed", "reason": "known_external_test_candidate"}
        excerpt = str(case.get("relevant_excerpt", ""))
        excerpt_sha = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        normalized = _normalize(excerpt)
        for row in self.records:
            urls = {str(row.get("canonical_url", "")), str(row.get("final_url", ""))} - {""}
            if urls & {str(case.get("canonical_url", "")), str(case.get("final_url", ""))}:
                return {"status": "exposed", "reason": "url_match"}
            if row.get("excerpt_sha256") == excerpt_sha or row.get(
                "source_document_sha256"
            ) == case.get("source_document_sha256"):
                return {"status": "exposed", "reason": "sha_match"}
            other = _normalize(str(row.get("relevant_excerpt", "")))
            if (
                normalized
                and other
                and (normalized == other or normalized in other or other in normalized)
            ):
                return {"status": "exposed", "reason": "excerpt_exact_or_containment"}
        if self.project_root and normalized:
            for root_name in ("data", "tests", "docs", "scripts", "app"):
                root = self.project_root / root_name
                if not root.exists():
                    continue
                for path in root.rglob("*"):
                    if not path.is_file() or path.stat().st_size > 1_000_000:
                        continue
                    try:
                        content = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
                    if (
                        str(case.get("canonical_url", ""))
                        and str(case.get("canonical_url")) in content
                    ):
                        return {"status": "exposed", "reason": "project_canonical_url_match"}
                    candidate = _normalize(content)
                    if normalized in candidate:
                        return {"status": "exposed", "reason": "project_excerpt_containment"}
        return {"status": "not_found", "reason": None}


class ReportStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def write(self, track: str, report: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        report = dict(report)
        report.pop("report_sha256", None)
        report["report_sha256"] = canonical_sha256(report)
        destination = self.directory / f"latest_{track}.json"
        fd, temporary = tempfile.mkstemp(prefix=f".{track}-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination

    def read(self, track: str, *, manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
        path = self.directory / f"latest_{track}.json"
        if not path.exists():
            return None
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "INVALID", "reason": "evaluation_report_unreadable"}
        if not isinstance(report, dict):
            return {"status": "INVALID", "reason": "evaluation_report_root_invalid"}
        digest = report.pop("report_sha256", None)
        report["report_sha256"] = digest
        if digest != canonical_sha256(
            {key: value for key, value in report.items() if key != "report_sha256"}
        ):
            report["status"] = "INVALID"
            report["reason"] = "evaluation_report_sha_drift"
            return report
        if manifest and report.get("dataset_sha256") != manifest.get("content_sha256"):
            report["status"] = "STALE"
            report["reason"] = "evaluation_report_dataset_sha_mismatch"
        return report


class EvaluationRunner:
    def __init__(
        self,
        settings: Settings | None = None,
        service_factory: Callable[[], DeterministicScreeningService] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.service_factory = service_factory or (
            lambda: DeterministicScreeningService(settings=self.settings)
        )
        self.ruleset = load_ruleset()

    def _binding(self) -> dict[str, Any]:
        url = make_url(self.settings.database_url)
        return {
            "tested_git_commit": _git_commit(),
            "ruleset_sha256": self.ruleset.sha256,
            "database_identity_sha256": canonical_sha256(url.render_as_string(hide_password=True)),
            "evaluation_epoch": _now(),
        }

    def run_constructed(self, session: Session, manifest: dict[str, Any]) -> dict[str, Any]:
        validate_manifest(manifest, require_sealed=True)
        if manifest["track"] != "constructed":
            raise ManifestError("evaluation_manifest_track_mismatch")
        return self._run(session, manifest, headline_cases=manifest["cases"], exposure=None)

    def run_external(
        self, session: Session, manifest: dict[str, Any], auditor: ExposureAuditor
    ) -> dict[str, Any]:
        validate_manifest(manifest, require_sealed=True)
        if manifest["track"] != "external":
            raise ManifestError("evaluation_manifest_track_mismatch")
        report = self._run(session, manifest, headline_cases=[], exposure=auditor)
        eligible = [row for row in report["per_case"] if row.get("eligible_for_headline")]
        report["in_scope_count"] = sum(
            row.get("scope_status") == "in_scope" for row in report["per_case"]
        )
        report["out_of_scope_count"] = len(report["per_case"]) - report["in_scope_count"]
        report["independent_in_scope_count"] = len(eligible)
        labels = {rule.rule_id for rule in self.ruleset.rules}
        report["external_metrics"] = (
            compute_metrics(eligible, label_universe=labels)
            if len(eligible) >= 30 and report["status"] == "COMPLETED"
            else None
        )
        if len(eligible) < 30:
            report["metrics"] = None
            report["strata"] = None
        report["status"] = (
            "COMPLETED"
            if len(eligible) >= 30 and report["status"] == "COMPLETED"
            else "INSUFFICIENT"
            if report["status"] == "COMPLETED"
            else report["status"]
        )
        report["independence_label"] = (
            "Independent"
            if auditor.coverage_status == "complete"
            else "INDEPENDENCE NOT FULLY VERIFIED"
        )
        return report

    def _run(
        self,
        session: Session,
        manifest: dict[str, Any],
        headline_cases: list[dict[str, Any]],
        exposure: ExposureAuditor | None,
    ) -> dict[str, Any]:
        per_case: list[dict[str, Any]] = []
        failed = False
        service = self.service_factory()
        for case in manifest["cases"]:
            item: dict[str, Any] = {
                "case_id": case["case_id"],
                "expected": sorted(set(case.get("expected_rule_ids", []))),
                "predicted": [],
                "difficulty": case.get("difficulty", "unknown"),
                "material_type": case.get("material_type", "unknown"),
                "input_sha256": canonical_sha256(
                    {
                        "text": case.get("text", case.get("relevant_excerpt", "")),
                        "material_type": case.get("material_type"),
                    }
                ),
                "scope_status": case.get("scope_status", "in_scope"),
            }
            if exposure is not None:
                contamination = exposure.audit(case)
                item["contamination_status"] = contamination["status"]
                item["contamination_reason"] = contamination["reason"]
                item["eligible_for_headline"] = (
                    item["scope_status"] == "in_scope" and contamination["status"] == "not_found"
                )
            try:
                raw_text = str(case.get("text", case.get("relevant_excerpt", "")))
                run = service.run(
                    session,
                    title=f"评测 {case['case_id']}",
                    material_type=str(case.get("material_type", "other")),
                    raw_text=raw_text,
                    source_label=f"evaluation:{manifest['track']}:{manifest['version']}",
                    external_reference=case["case_id"],
                    is_constructed_evaluation=manifest["track"] == "constructed",
                )
                if run.status != "completed":
                    raise RuntimeError(run.error_code or "screening_not_completed")
                item.update(
                    {
                        "predicted": sorted({finding.rule_id for finding in run.findings}),
                        "tp_labels": sorted(
                            set(item["expected"]) & {finding.rule_id for finding in run.findings}
                        ),
                        "fp_labels": sorted(
                            {finding.rule_id for finding in run.findings} - set(item["expected"])
                        ),
                        "fn_labels": sorted(
                            set(item["expected"]) - {finding.rule_id for finding in run.findings}
                        ),
                        "exact_match": set(item["expected"])
                        == {finding.rule_id for finding in run.findings},
                        "screening_run_id": run.id,
                        "ruleset_sha256": run.ruleset_sha256,
                        "run_payload_sha256": run.run_payload_sha256,
                    }
                )
            except Exception as exc:
                failed = True
                item["error"] = str(exc)
            per_case.append(item)
        labels = {rule.rule_id for rule in self.ruleset.rules}
        measured = [
            row
            for row in per_case
            if "error" not in row and (exposure is None or row.get("eligible_for_headline", False))
        ]
        binding = self._binding()
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "track": manifest["track"],
            "dataset_version": manifest["version"],
            "dataset_sha256": manifest["content_sha256"],
            "provenance_sha256": canonical_sha256(manifest.get("provenance", [])),
            "exposure_inventory_sha256": exposure.sha256 if exposure else None,
            "started_at": binding["evaluation_epoch"],
            "completed_at": _now(),
            "status": "INVALID" if failed else "COMPLETED",
            "sample_counts": {
                "total": len(per_case),
                "evaluated": len(measured),
                "failed": sum("error" in row for row in per_case),
            },
            "metrics": compute_metrics(measured, label_universe=labels) if not failed else None,
            "strata": stratify(measured, labels) if not failed else None,
            "per_case": per_case,
            "runner_version": RUNNER_VERSION,
            "metric_schema_version": METRIC_SCHEMA_VERSION,
            **binding,
        }

    @staticmethod
    def acquire_single_flight() -> bool:
        return _run_lock.acquire(blocking=False)

    @staticmethod
    def release_single_flight() -> None:
        _run_lock.release()
