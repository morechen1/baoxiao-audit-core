from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.screening import (
    DeterministicComplianceRuleEngine,
    DeterministicScreeningService,
    HybridScreeningService,
    SemanticClaimParser,
    load_ruleset,
    segment_marketing_text,
)
from data.evaluations.v2_development_v1.run_evaluation import (
    BudgetedProvider,
    grouped_metrics,
    metrics,
    safe_div,
    source_fingerprint,
)

HERE = Path(__file__).resolve().parent
DATASET = HERE / "v2_unseen_holdout_v1.jsonl"
MANIFEST = HERE / "manifest.json"
STARTED = HERE / "holdout_run_started.json"
IN_PROGRESS = HERE / "holdout_predictions_in_progress.jsonl"
PREDICTIONS = HERE / "holdout_predictions_v1.jsonl"
SUMMARY = HERE / "holdout_summary_v1.json"
SYSTEM_FREEZE_HEAD = "08acc0dd210eb022c10c38ce97fe8b16e70558f3"
MAX_PROVIDER_CALLS = 200
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260815


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def material_sha(case: dict[str, Any]) -> str:
    payload = {
        "title": f"V2 Holdout {case['case_id']}",
        "material_type": "advertisement",
        "raw_text": case["text"],
        "source_label": "v2_unseen_holdout_v1",
        "external_reference": case["case_id"],
        "is_constructed_evaluation": True,
        "normalization_version": "marketing_text_normalization_v1",
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluate_case(case: dict[str, Any], provider: BudgetedProvider) -> dict[str, Any]:
    settings = get_settings()
    ruleset = load_ruleset()
    parser = SemanticClaimParser(ruleset=ruleset, settings=settings, provider=provider)
    service = HybridScreeningService(
        settings=settings,
        deterministic=DeterministicScreeningService(
            ruleset=ruleset,
            settings=settings,
            semantic_parser=parser,
        ),
    )
    raw_sha = material_sha(case)
    deterministic = DeterministicComplianceRuleEngine(ruleset).run(
        raw_text=case["text"],
        material_sha256=raw_sha,
        segments=segment_marketing_text(case["text"], raw_sha),
    )
    deterministic_identity = {
        (item.rule_id, item.raw_start_offset, item.raw_end_offset) for item in deterministic
    }
    started = time.perf_counter()
    with SessionLocal() as session:
        run = service.run(
            session,
            title=f"V2 Holdout {case['case_id']}",
            material_type="advertisement",
            raw_text=case["text"],
            source_label="v2_unseen_holdout_v1",
            external_reference=case["case_id"],
            is_constructed_evaluation=True,
        )
        report = cast(dict[str, Any], service.show(session, run.id))
    diagnostics = report["evidence_evaluation_summary"]["semantic_parser"]
    findings: list[dict[str, Any]] = []
    for finding in report["findings"]:
        identity = (
            finding["rule_id"],
            finding["raw_start_offset"],
            finding["raw_end_offset"],
        )
        findings.append(
            {
                "rule_id": finding["rule_id"],
                "matched_text": finding["matched_text"],
                "raw_start_offset": finding["raw_start_offset"],
                "raw_end_offset": finding["raw_end_offset"],
                "provenance": "deterministic" if identity in deterministic_identity else "semantic",
                "exact_source_quote": (
                    case["text"][finding["raw_start_offset"] : finding["raw_end_offset"]]
                    == finding["matched_text"]
                ),
                "evidence_status": finding["evidence_status"],
                "evidence_link_count": len(finding["evidence"]),
            }
        )
    return {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "stratum": case["stratum"],
        "context_group": case["context_group"],
        "expected_rules": case["expected_rules"],
        "predicted_rules": sorted({row["rule_id"] for row in findings}),
        "findings": findings,
        "provider_calls": int(diagnostics.get("provider_calls", 0)),
        "retries": int(diagnostics.get("retries", 0)),
        "provider_latency_ms": float(diagnostics.get("provider_latency_ms", 0)),
        "provider_status": diagnostics.get("status"),
        "semantic_chunks": int(diagnostics.get("semantic_chunks", 0)),
        "cache_hits": int(diagnostics.get("cache_hits", 0)),
        "reject_reasons": diagnostics.get("reject_reasons", {}),
        "hallucinated_quote_accepted": int(diagnostics.get("hallucinated_quote_accepted", 0)),
        "screening_run_id": report["screening_run_id"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def bootstrap_ci(records: list[dict[str, Any]], rules: list[str]) -> dict[str, list[float]]:
    rng = random.Random(BOOTSTRAP_SEED)
    names = (
        "micro_precision",
        "micro_recall",
        "micro_f1",
        "macro_precision",
        "macro_recall",
        "macro_f1",
        "exact_set",
        "positive_hit",
        "negative_false_alarm",
        "hard_negative_false_alarm",
        "specificity",
    )
    samples: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(BOOTSTRAP_SAMPLES):
        resampled = [records[rng.randrange(len(records))] for _ in records]
        metric_values = metrics(resampled, rules)
        for name in names:
            samples[name].append(float(metric_values[name]))
    result: dict[str, list[float]] = {}
    for name, sample_values in samples.items():
        ordered = sorted(sample_values)
        lower = ordered[int(0.025 * (len(ordered) - 1))]
        upper = ordered[int(0.975 * (len(ordered) - 1))]
        result[name] = [lower, upper]
    return result


def _validate_frozen_dataset(records: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))
    if len(records) != 180 or any(row["split"] != "UNSEEN_HOLDOUT" for row in records):
        raise RuntimeError("v2_holdout_dataset_invalid")
    if manifest["system_freeze_head"] != SYSTEM_FREEZE_HEAD:
        raise RuntimeError("v2_system_freeze_head_mismatch")
    if manifest["dataset_sha256"] != canonical_sha(records):
        raise RuntimeError("v2_holdout_dataset_hash_mismatch")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorized-provider-send", action="store_true")
    args = parser.parse_args()
    if not args.authorized_provider_send:
        raise RuntimeError("explicit_holdout_provider_authorization_required")
    if STARTED.exists() or IN_PROGRESS.exists() or PREDICTIONS.exists() or SUMMARY.exists():
        raise RuntimeError("v2_holdout_single_run_already_started")
    settings = get_settings()
    if not settings.semantic_parser_enabled or not settings.llm_api_key:
        raise RuntimeError("semantic_parser_provider_not_configured")
    records = load_jsonl(DATASET)
    manifest = _validate_frozen_dataset(records)
    fingerprint = source_fingerprint()
    algorithm_diff = fingerprint["algorithm_tree_sha256"]
    if algorithm_diff != hashlib.sha256(b"").hexdigest():
        raise RuntimeError("v2_algorithm_tree_not_clean_after_freeze")
    STARTED.write_text(
        json.dumps(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "system_freeze_head": SYSTEM_FREEZE_HEAD,
                "dataset_sha256": manifest["dataset_sha256"],
                "source": fingerprint,
                "single_run_only": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    provider = BudgetedProvider(MAX_PROVIDER_CALLS)
    predictions: list[dict[str, Any]] = []
    with IN_PROGRESS.open("x", encoding="utf-8") as handle:
        for case in records:
            prediction = evaluate_case(case, provider)
            predictions.append(prediction)
            handle.write(json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
    provider_calls = sum(row["provider_calls"] for row in predictions)
    if provider_calls > MAX_PROVIDER_CALLS:
        raise RuntimeError("v2_holdout_provider_budget_exceeded")
    rules = [rule.rule_id for rule in load_ruleset().rules]
    result = metrics(predictions, rules)
    semantic_findings = [
        finding
        for row in predictions
        for finding in row["findings"]
        if finding["provenance"] == "semantic"
    ]
    summary = {
        "dataset": "v2_unseen_holdout_v1",
        "split": "UNSEEN_HOLDOUT",
        "headline_benchmark": True,
        "system_freeze_head": SYSTEM_FREEZE_HEAD,
        "source": fingerprint,
        "dataset_sha256": manifest["dataset_sha256"],
        "dataset_file_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "cases": len(predictions),
        "provider_calls": provider_calls,
        "retries": sum(row["retries"] for row in predictions),
        "provider_latency_ms": {
            "total": round(sum(row["provider_latency_ms"] for row in predictions), 2),
            "average": round(
                sum(row["provider_latency_ms"] for row in predictions) / len(predictions), 2
            ),
        },
        "metrics": result,
        "confidence_intervals_95": bootstrap_ci(predictions, rules),
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED},
        "strata": grouped_metrics(predictions, rules, "stratum"),
        "context_groups": grouped_metrics(predictions, rules, "context_group"),
        "quote_integrity": safe_div(
            sum(row["exact_source_quote"] for row in semantic_findings), len(semantic_findings)
        ),
        "hallucinated_quote_accepted": sum(
            row["hallucinated_quote_accepted"] for row in predictions
        ),
        "provider_statuses": dict(Counter(row["provider_status"] for row in predictions)),
        "completed_at": datetime.now(UTC).isoformat(),
        "algorithm_tuning_after_holdout": False,
    }
    IN_PROGRESS.replace(PREDICTIONS)
    SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
