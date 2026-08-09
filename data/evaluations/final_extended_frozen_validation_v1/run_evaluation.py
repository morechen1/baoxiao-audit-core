from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import ExplanationRun
from app.services.explanation import ControlledExplanationService
from app.services.screening import (
    DeterministicComplianceRuleEngine,
    DeterministicScreeningService,
    HybridScreeningService,
    OpenAICompatibleSemanticParserProvider,
    SemanticClaimParser,
    load_ruleset,
    segment_marketing_text,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATASET = HERE / "final_extended_frozen_validation_v1.jsonl"
MANIFEST = HERE / "manifest.json"
PREDICTIONS = HERE / "final_validation_predictions_v1.jsonl"
SUMMARY = HERE / "final_validation_summary_v1.json"
RAG_REPORT = HERE / "final_validation_rag_smoke_v1.json"
SHORT_CIRCUIT_REPORT = HERE / "final_validation_short_circuit_v1.json"
TOTAL_PROVIDER_BUDGET = 180
MAX_WORKERS = 4


class TimingProvider:
    def __init__(self, settings: Any) -> None:
        self.inner = OpenAICompatibleSemanticParserProvider(settings)
        self.latencies_ms: list[float] = []

    def generate(self, raw_text: str) -> Any:
        started = time.perf_counter()
        try:
            return self.inner.generate(raw_text)
        finally:
            self.latencies_ms.append(round((time.perf_counter() - started) * 1000, 3))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def assert_algorithm_frozen(system_head: str) -> None:
    paths = ["app/services", "app/core", "app/models", "migrations", "data/rules"]
    result = subprocess.run(
        ["git", "diff", "--quiet", system_head, "--", *paths], cwd=ROOT, check=False
    )
    if result.returncode != 0:
        raise RuntimeError("algorithm_tree_changed_after_freeze")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_predictions(records: list[dict[str, Any]]) -> None:
    ordered = sorted(records, key=lambda item: item["case_id"])
    PREDICTIONS.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in ordered),
        encoding="utf-8",
    )


def _material_sha(case: dict[str, Any]) -> str:
    payload = {
        "title": f"Extended Validation {case['case_id']}",
        "material_type": "advertisement",
        "raw_text": case["text"],
        "source_label": "final_extended_frozen_validation_v1",
        "external_reference": case["case_id"],
        "is_constructed_evaluation": True,
        "normalization_version": "marketing_text_normalization_v1",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    timing = TimingProvider(settings)
    ruleset = load_ruleset()
    parser = SemanticClaimParser(ruleset=ruleset, settings=settings, provider=timing)
    deterministic_service = DeterministicScreeningService(
        ruleset=ruleset, settings=settings, semantic_parser=parser
    )
    service = HybridScreeningService(settings=settings, deterministic=deterministic_service)
    deterministic = DeterministicComplianceRuleEngine(ruleset).run(
        raw_text=case["text"],
        material_sha256=_material_sha(case),
        segments=segment_marketing_text(case["text"], _material_sha(case)),
    )
    deterministic_identities = {
        (item.rule_id, item.raw_start_offset, item.raw_end_offset) for item in deterministic
    }
    started = time.perf_counter()
    session = SessionLocal()
    try:
        run = service.run(
            session,
            title=f"Extended Validation {case['case_id']}",
            material_type="advertisement",
            raw_text=case["text"],
            source_label="final_extended_frozen_validation_v1",
            external_reference=case["case_id"],
            is_constructed_evaluation=True,
        )
        report = service.show(session, run.id)
    finally:
        session.close()
    diagnostics = report.get("evidence_evaluation_summary", {}).get("semantic_parser", {})
    findings = []
    semantic_findings = 0
    exact_semantic_quotes = 0
    for finding in report["findings"]:
        identity = (finding["rule_id"], finding["raw_start_offset"], finding["raw_end_offset"])
        provenance = "deterministic" if identity in deterministic_identities else "semantic"
        exact_quote = (
            case["text"][finding["raw_start_offset"] : finding["raw_end_offset"]]
            == finding["matched_text"]
        )
        if provenance == "semantic":
            semantic_findings += 1
            exact_semantic_quotes += int(exact_quote)
        findings.append(
            {
                "finding_key": finding["finding_key"],
                "rule_id": finding["rule_id"],
                "matched_text": finding["matched_text"],
                "raw_start_offset": finding["raw_start_offset"],
                "raw_end_offset": finding["raw_end_offset"],
                "severity": finding["severity"],
                "evidence_status": finding["evidence_status"],
                "evidence_link_count": len(finding.get("evidence", [])),
                "detection_provenance": provenance,
                "exact_source_quote": exact_quote,
            }
        )
    return {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "stratum": case["stratum"],
        "expected_rules": sorted(case["expected_rules"]),
        "predicted_rules": sorted({item["rule_id"] for item in findings}),
        "accepted_findings": findings,
        "accepted_semantic_findings": semantic_findings,
        "accepted_exact_semantic_quotes": exact_semantic_quotes,
        "semantic_candidate_count": int(diagnostics.get("model_candidates", 0)),
        "rejected_candidate_count": int(diagnostics.get("rejected_candidates", 0)),
        "reject_reasons": diagnostics.get("reject_reasons", {}),
        "provider_status": diagnostics.get("status", "missing"),
        "provider_failure_code": diagnostics.get("failure_code"),
        "parser_provider_calls": int(diagnostics.get("provider_calls", 0)),
        "parser_latency_ms": round(sum(timing.latencies_ms), 3),
        "parser_attempt_latencies_ms": timing.latencies_ms,
        "retry_count": int(diagnostics.get("retries", 0)),
        "hallucinated_quote_accepted": int(diagnostics.get("hallucinated_quote_accepted", 0)),
        "screening_run_id": report["screening_run_id"],
        "evidence_links": int(
            report.get("evidence_evaluation_summary", {}).get("links_persisted", 0)
        ),
        "execution_status": "completed",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def classification_metrics(records: list[dict[str, Any]], rules: list[str]) -> dict[str, Any]:
    per_rule: dict[str, dict[str, Any]] = {}
    total_tp = total_fp = total_fn = 0
    for rule in rules:
        tp = sum(
            rule in row["expected_rules"] and rule in row["predicted_rules"] for row in records
        )
        fp = sum(
            rule not in row["expected_rules"] and rule in row["predicted_rules"] for row in records
        )
        fn = sum(
            rule in row["expected_rules"] and rule not in row["predicted_rules"] for row in records
        )
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1 = safe_div(2 * precision * recall, precision + recall)
        per_rule[rule] = {
            "support": tp + fn,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
        total_tp += tp
        total_fp += fp
        total_fn += fn
    micro_p = safe_div(total_tp, total_tp + total_fp)
    micro_r = safe_div(total_tp, total_tp + total_fn)
    micro_f1 = safe_div(2 * micro_p * micro_r, micro_p + micro_r)
    macro_p = sum(value["precision"] for value in per_rule.values()) / len(rules)
    macro_r = sum(value["recall"] for value in per_rule.values()) / len(rules)
    macro_f1 = sum(value["f1"] for value in per_rule.values()) / len(rules)
    exact = safe_div(
        sum(set(row["expected_rules"]) == set(row["predicted_rules"]) for row in records),
        len(records),
    )
    positives = [row for row in records if row["expected_rules"]]
    negatives = [row for row in records if not row["expected_rules"]]
    positive_hit = safe_div(
        sum(bool(set(row["expected_rules"]) & set(row["predicted_rules"])) for row in positives),
        len(positives),
    )
    false_alarm = safe_div(sum(bool(row["predicted_rules"]) for row in negatives), len(negatives))
    return {
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "micro_precision": micro_p,
        "micro_recall": micro_r,
        "micro_f1": micro_f1,
        "macro_precision": macro_p,
        "macro_recall": macro_r,
        "macro_f1": macro_f1,
        "exact_set_accuracy": exact,
        "positive_case_hit_rate": positive_hit,
        "negative_false_alarm_rate": false_alarm,
        "negative_specificity": 1 - false_alarm,
        "per_rule": per_rule,
    }


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_ci(
    records: list[dict[str, Any]], rules: list[str], iterations: int = 2000
) -> dict[str, list[float]]:
    rng = random.Random(20260809)
    samples: dict[str, list[float]] = {
        "micro_precision": [],
        "micro_recall": [],
        "micro_f1": [],
        "exact_set_accuracy": [],
        "negative_false_alarm_rate": [],
    }
    for _ in range(iterations):
        drawn = [records[rng.randrange(len(records))] for _ in records]
        metrics = classification_metrics(drawn, rules)
        for key in samples:
            samples[key].append(metrics[key])
    return {
        key: [percentile(values, 0.025), percentile(values, 0.975)]
        for key, values in samples.items()
    }


def strata_metrics(records: list[dict[str, Any]], rules: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for stratum in (
        "explicit",
        "paraphrase",
        "colloquial",
        "light_implicit",
        "multi_label",
        "negation",
        "educational",
        "compliant",
        "hard_negative",
    ):
        rows = [row for row in records if row["stratum"] == stratum]
        metrics = classification_metrics(rows, rules)
        output[stratum] = {
            "cases": len(rows),
            "label_assignments": sum(len(row["expected_rules"]) for row in rows),
            "precision": metrics["micro_precision"],
            "recall": metrics["micro_recall"],
            "f1": metrics["micro_f1"],
            "exact_set_accuracy": metrics["exact_set_accuracy"],
            "false_alarm_rate": metrics["negative_false_alarm_rate"]
            if all(not row["expected_rules"] for row in rows)
            else None,
        }
    return output


def choose_rag_cases(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    used_rules: set[str] = set()
    desired = ["explicit", "paraphrase", "colloquial", "light_implicit", "multi_label"]
    eligible = sorted(
        (row for row in records if row["accepted_findings"] and row["expected_rules"]),
        key=lambda row: row["case_id"],
    )
    for stratum in desired:
        for row in eligible:
            if row in chosen or row["stratum"] != stratum:
                continue
            new_rules = [rule for rule in row["predicted_rules"] if rule not in used_rules]
            if not new_rules:
                continue
            chosen.append(row)
            used_rules.add(new_rules[0])
            break
    for row in eligible:
        if len(chosen) >= limit:
            break
        if row in chosen:
            continue
        new_rules = [rule for rule in row["predicted_rules"] if rule not in used_rules]
        if not new_rules:
            continue
        chosen.append(row)
        used_rules.add(new_rules[0])
    return chosen[:limit]


def run_rag_smoke(records: list[dict[str, Any]], parser_calls: int) -> dict[str, Any]:
    if RAG_REPORT.exists():
        return json.loads(RAG_REPORT.read_text(encoding="utf-8"))
    available_calls = max(0, TOTAL_PROVIDER_BUDGET - parser_calls)
    case_limit = min(6, available_calls // 2)
    selected = choose_rag_cases(records, case_limit)
    settings = get_settings()
    service = ControlledExplanationService()
    results: list[dict[str, Any]] = []
    explanation_calls = 0
    for row in selected:
        item: dict[str, Any] = {
            "case_id": row["case_id"],
            "stratum": row["stratum"],
            "screening_run_id": row["screening_run_id"],
            "evidence_link_success": all(
                finding["evidence_link_count"] > 0 for finding in row["accepted_findings"]
            ),
            "audiences": {},
        }
        session = SessionLocal()
        try:
            for audience in ("institution", "consumer"):
                explanation_calls += 1
                try:
                    run = session.scalar(
                        select(ExplanationRun)
                        .where(
                            ExplanationRun.screening_run_id == row["screening_run_id"],
                            ExplanationRun.audience == audience,
                            ExplanationRun.provider_name == settings.llm_provider,
                        )
                        .order_by(ExplanationRun.id.desc())
                    )
                    if run is None:
                        run = service.create(
                            session,
                            screening_run_id=row["screening_run_id"],
                            audience=audience,
                            provider_name=settings.llm_provider,
                        )
                    citations = service.citations(session, run.id)
                    item["audiences"][audience] = {
                        "status": run.status,
                        "validation_status": run.validation_status,
                        "citation_count": len(citations),
                        "citation_validation": bool(citations)
                        and all(value["validation_status"] == "passed" for value in citations),
                        "claim_validation": run.validation_status == "passed",
                        "uncertainty_validation": run.validation_status == "passed",
                        "explanation_run_id": run.id,
                    }
                except Exception as exc:
                    item["audiences"][audience] = {
                        "status": "failed",
                        "validation_status": "failed",
                        "error_code": str(exc),
                        "citation_count": 0,
                        "citation_validation": False,
                        "claim_validation": False,
                        "uncertainty_validation": False,
                    }
        finally:
            session.close()
        results.append(item)
    report = {
        "selection_rule": (
            "case_id order; first valid case for explicit/paraphrase/colloquial/"
            "light_implicit/multi_label with a new taxonomy, then fill by case_id "
            "with new taxonomy"
        ),
        "cases": len(results),
        "explanation_calls": explanation_calls,
        "results": results,
        "evidence_link_success": sum(item["evidence_link_success"] for item in results),
        "institution_artifact_success": sum(
            item["audiences"].get("institution", {}).get("status") == "completed"
            for item in results
        ),
        "consumer_artifact_success": sum(
            item["audiences"].get("consumer", {}).get("status") == "completed" for item in results
        ),
        "citation_validation_success": sum(
            all(value.get("citation_validation") for value in item["audiences"].values())
            for item in results
        ),
        "claim_validation_success": sum(
            all(value.get("claim_validation") for value in item["audiences"].values())
            for item in results
        ),
        "uncertainty_validation_success": sum(
            all(value.get("uncertainty_validation") for value in item["audiences"].values())
            for item in results
        ),
    }
    write_json(RAG_REPORT, report)
    return report


def negative_short_circuit(records: list[dict[str, Any]]) -> dict[str, Any]:
    if SHORT_CIRCUIT_REPORT.exists():
        return json.loads(SHORT_CIRCUIT_REPORT.read_text(encoding="utf-8"))
    selected = sorted(
        (row for row in records if row["case_type"] == "negative"),
        key=lambda row: row["case_id"],
    )[:3]
    results = []
    for row in selected:
        session = SessionLocal()
        try:
            explanation_runs = session.scalar(
                select(func.count(ExplanationRun.id)).where(
                    ExplanationRun.screening_run_id == row["screening_run_id"]
                )
            )
        finally:
            session.close()
        no_finding = not row["accepted_findings"]
        results.append(
            {
                "case_id": row["case_id"],
                "screening_run_id": row["screening_run_id"],
                "no_finding": no_finding,
                "evidence_links": row["evidence_links"],
                "explanation_runs": int(explanation_runs or 0),
                "rag_call_avoided": no_finding and row["evidence_links"] == 0,
                "explanation_calls_avoided": 2 if no_finding and not explanation_runs else 0,
            }
        )
    report = {
        "selection_rule": "first three negative cases by case_id",
        "cases_checked": len(results),
        "no_finding": sum(item["no_finding"] for item in results),
        "rag_calls_avoided": sum(item["rag_call_avoided"] for item in results),
        "explanation_calls_avoided": sum(item["explanation_calls_avoided"] for item in results),
        "results": results,
    }
    write_json(SHORT_CIRCUIT_REPORT, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validate frozen inputs and runtime configuration without making provider calls.",
    )
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    initial_sha = sha256(DATASET)
    if initial_sha != manifest["dataset_sha256"]:
        raise RuntimeError("dataset_sha_mismatch_before_run")
    assert_algorithm_frozen(manifest["system_freeze_head"])
    settings = get_settings()
    if not settings.semantic_parser_enabled or settings.semantic_screening_enabled:
        raise RuntimeError("semantic_runtime_flags_invalid")
    if (
        settings.llm_provider != "openai_compatible"
        or settings.llm_model != manifest["semantic_parser_model"]
    ):
        raise RuntimeError("provider_configuration_mismatch")
    cases = load_jsonl(DATASET)
    if args.preflight:
        print(
            json.dumps(
                {
                    "status": "PREFLIGHT_PASS",
                    "dataset_sha256": initial_sha,
                    "cases": len(cases),
                    "system_freeze_head": manifest["system_freeze_head"],
                    "provider": settings.llm_provider,
                    "model": settings.llm_model,
                    "semantic_parser_enabled": settings.semantic_parser_enabled,
                    "semantic_screening_enabled": settings.semantic_screening_enabled,
                    "max_workers": MAX_WORKERS,
                    "provider_budget": TOTAL_PROVIDER_BUDGET,
                },
                ensure_ascii=False,
            )
        )
        return
    if SUMMARY.exists():
        prior = json.loads(SUMMARY.read_text(encoding="utf-8"))
        if prior.get("dataset", {}).get("sha256") == initial_sha:
            print(json.dumps({"status": "ALREADY_COMPLETE", "summary": str(SUMMARY)}))
            return
    existing = {
        item["case_id"]: item
        for item in load_jsonl(PREDICTIONS)
        if item.get("execution_status") == "completed"
    }
    records = list(existing.values())
    pending = [case for case in cases if case["case_id"] not in existing]
    parser_calls = sum(item["parser_provider_calls"] for item in records)
    started_at = datetime.now(UTC).isoformat()
    lock = threading.Lock()
    for offset in range(0, len(pending), MAX_WORKERS):
        batch = pending[offset : offset + MAX_WORKERS]
        remaining_after = len(pending) - (offset + len(batch))
        if parser_calls + len(batch) + remaining_after > TOTAL_PROVIDER_BUDGET:
            raise RuntimeError("provider_budget_would_be_exceeded")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            outcomes = list(executor.map(evaluate_case, batch))
        with lock:
            records.extend(outcomes)
            parser_calls += sum(item["parser_provider_calls"] for item in outcomes)
            write_predictions(records)
        if parser_calls + remaining_after > TOTAL_PROVIDER_BUDGET:
            raise RuntimeError("provider_budget_exceeded_by_retries")
        print(
            json.dumps(
                {
                    "completed": len(records),
                    "parser_calls": parser_calls,
                    "retries": sum(item["retry_count"] for item in records),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    if len(records) != len(cases):
        raise RuntimeError("evaluation_incomplete")
    final_sha = sha256(DATASET)
    if final_sha != initial_sha:
        raise RuntimeError("dataset_sha_changed_during_run")
    ordered = sorted(records, key=lambda item: item["case_id"])
    failed_parser_cases = sum(row["provider_status"] != "completed" for row in ordered)
    if failed_parser_cases > max(3, math.floor(len(ordered) * 0.05)):
        raise RuntimeError(f"provider_unavailability_invalidates_evaluation:{failed_parser_cases}")
    metrics = classification_metrics(ordered, manifest["taxonomy"])
    cis = bootstrap_ci(ordered, manifest["taxonomy"])
    strata = strata_metrics(ordered, manifest["taxonomy"])
    rag = run_rag_smoke(ordered, parser_calls)
    short_circuit = negative_short_circuit(ordered)
    reject_counts: Counter[str] = Counter()
    for row in ordered:
        reject_counts.update({key: int(value) for key, value in row["reject_reasons"].items()})
    accepted_semantic = sum(row["accepted_semantic_findings"] for row in ordered)
    exact_semantic = sum(row["accepted_exact_semantic_quotes"] for row in ordered)
    total_calls = parser_calls + rag["explanation_calls"]
    summary = {
        "dataset": {
            "name": manifest["dataset_name"],
            "sha256": final_sha,
            "cases": len(cases),
            "single_positive": manifest["single_positive"],
            "multi_positive": manifest["multi_positive"],
            "negative": manifest["negative"],
            "positive_label_assignments": manifest["positive_label_assignments"],
            "label_status": manifest["label_status"],
        },
        "system_freeze_head": manifest["system_freeze_head"],
        "evaluation_head": git_head(),
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "evaluation_status": "VALID",
        "provider": {
            "model": settings.llm_model,
            "parser_cases": len(cases),
            "parser_calls": parser_calls,
            "retries": sum(row["retry_count"] for row in ordered),
            "rag_smoke_cases": rag["cases"],
            "explanation_calls": rag["explanation_calls"],
            "total_provider_calls": total_calls,
            "budget": TOTAL_PROVIDER_BUDGET,
            "budget_exceeded": total_calls > TOTAL_PROVIDER_BUDGET,
        },
        "metrics": metrics,
        "confidence_intervals_95": cis,
        "strata": strata,
        "safety": {
            "accepted_semantic_findings": accepted_semantic,
            "accepted_exact_semantic_quotes": exact_semantic,
            "quote_integrity_rate": safe_div(exact_semantic, accepted_semantic),
            "hallucinated_quote_accepted": sum(
                row["hallucinated_quote_accepted"] for row in ordered
            ),
            "rejected_candidates": sum(row["rejected_candidate_count"] for row in ordered),
            "reject_reasons": dict(sorted(reject_counts.items())),
        },
        "rag_sample": rag,
        "negative_short_circuit": short_circuit,
        "definitions": {
            "classification": (
                "12-label multilabel set classification; duplicate findings collapse "
                "to one predicted rule per case"
            ),
            "positive_case_hit": "at least one expected rule appears in predicted_rules",
            "negative_false_alarm": "negative case has one or more predicted_rules",
            "confidence_interval": " ".join(
                [
                    "case bootstrap, 2000 resamples, fixed seed 20260809,",
                    "percentile 95% interval",
                ]
            ),
        },
    }
    write_json(SUMMARY, summary)
    print(
        json.dumps(
            {"status": "VALID", "total_provider_calls": total_calls, "metrics": metrics},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
