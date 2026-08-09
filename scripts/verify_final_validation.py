"""Offline verification for the frozen 156-case validation artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "data/evaluations/final_extended_frozen_validation_v1"
DATASET = ARTIFACT_DIR / "final_extended_frozen_validation_v1.jsonl"
MANIFEST = ARTIFACT_DIR / "manifest.json"
PREDICTIONS = ARTIFACT_DIR / "final_validation_predictions_v1.jsonl"
SUMMARY = ARTIFACT_DIR / "final_validation_summary_v1.json"
RAG_REPORT = ARTIFACT_DIR / "final_validation_rag_smoke_v1.json"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected_object:{path.name}")
    return payload


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected_jsonl_objects:{path.name}")
    return rows


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
    micro_precision = safe_div(total_tp, total_tp + total_fp)
    micro_recall = safe_div(total_tp, total_tp + total_fn)
    micro_f1 = safe_div(
        2 * micro_precision * micro_recall,
        micro_precision + micro_recall,
    )
    positives = [row for row in records if row["expected_rules"]]
    negatives = [row for row in records if not row["expected_rules"]]
    false_alarm = safe_div(sum(bool(row["predicted_rules"]) for row in negatives), len(negatives))
    return {
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "micro_f1": micro_f1,
        "macro_precision": sum(row["precision"] for row in per_rule.values()) / len(rules),
        "macro_recall": sum(row["recall"] for row in per_rule.values()) / len(rules),
        "macro_f1": sum(row["f1"] for row in per_rule.values()) / len(rules),
        "exact_set_accuracy": safe_div(
            sum(set(row["expected_rules"]) == set(row["predicted_rules"]) for row in records),
            len(records),
        ),
        "positive_case_hit_rate": safe_div(
            sum(
                bool(set(row["expected_rules"]) & set(row["predicted_rules"])) for row in positives
            ),
            len(positives),
        ),
        "negative_false_alarm_rate": false_alarm,
        "negative_specificity": 1 - false_alarm,
        "per_rule": per_rule,
    }


def strata_metrics(records: list[dict[str, Any]], rules: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    strata = (
        "explicit",
        "paraphrase",
        "colloquial",
        "light_implicit",
        "multi_label",
        "negation",
        "educational",
        "compliant",
        "hard_negative",
    )
    for stratum in strata:
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


def assert_equal(actual: Any, expected: Any, path: str) -> None:
    if isinstance(actual, float) or isinstance(expected, float):
        if not math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-12):
            raise AssertionError(f"metric_mismatch:{path}")
        return
    if actual != expected:
        raise AssertionError(f"artifact_mismatch:{path}")


def compare_tree(actual: Any, expected: Any, path: str) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise AssertionError(f"artifact_type_mismatch:{path}")
        for key, value in expected.items():
            compare_tree(actual.get(key), value, f"{path}.{key}")
        return
    assert_equal(actual, expected, path)


def main() -> int:
    manifest = load_json(MANIFEST)
    summary = load_json(SUMMARY)
    rag = load_json(RAG_REPORT)
    dataset = load_jsonl(DATASET)
    predictions = load_jsonl(PREDICTIONS)
    dataset_sha = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    assert_equal(dataset_sha, manifest["dataset_sha256"], "dataset.manifest_sha256")
    assert_equal(dataset_sha, summary["dataset"]["sha256"], "dataset.summary_sha256")
    assert_equal(len(dataset), 156, "dataset.cases")
    assert_equal(len(predictions), 156, "predictions.cases")

    expected_by_id = {row["case_id"]: sorted(row["expected_rules"]) for row in dataset}
    if len(expected_by_id) != len(dataset):
        raise AssertionError("duplicate_dataset_case_id")
    for row in predictions:
        assert_equal(
            sorted(row["expected_rules"]),
            expected_by_id.get(row["case_id"]),
            f"prediction_expected_rules.{row['case_id']}",
        )

    rules = manifest["taxonomy"]
    metrics = classification_metrics(predictions, rules)
    compare_tree(summary["metrics"], metrics, "metrics")
    compare_tree(summary["strata"], strata_metrics(predictions, rules), "strata")

    rejected_reasons: Counter[str] = Counter()
    for row in predictions:
        rejected_reasons.update(
            {key: int(value) for key, value in row.get("reject_reasons", {}).items()}
        )
    semantic_findings = sum(int(row["accepted_semantic_findings"]) for row in predictions)
    exact_quotes = sum(int(row["accepted_exact_semantic_quotes"]) for row in predictions)
    safety = {
        "accepted_semantic_findings": semantic_findings,
        "accepted_exact_semantic_quotes": exact_quotes,
        "quote_integrity_rate": safe_div(exact_quotes, semantic_findings),
        "hallucinated_quote_accepted": sum(
            int(row["hallucinated_quote_accepted"]) for row in predictions
        ),
        "rejected_candidates": sum(int(row["rejected_candidate_count"]) for row in predictions),
        "reject_reasons": dict(sorted(rejected_reasons.items())),
    }
    compare_tree(summary["safety"], safety, "safety")

    parser_calls = sum(int(row["parser_provider_calls"]) for row in predictions)
    retries = sum(int(row["retry_count"]) for row in predictions)
    provider = summary["provider"]
    assert_equal(provider["parser_cases"], len(predictions), "provider.parser_cases")
    assert_equal(provider["parser_calls"], parser_calls, "provider.parser_calls")
    assert_equal(provider["retries"], retries, "provider.retries")
    assert_equal(provider["rag_smoke_cases"], rag["cases"], "provider.rag_smoke_cases")
    assert_equal(
        provider["explanation_calls"],
        rag["explanation_calls"],
        "provider.explanation_calls",
    )
    assert_equal(
        provider["total_provider_calls"],
        parser_calls + rag["explanation_calls"],
        "provider.total_provider_calls",
    )
    assert_equal(provider["total_provider_calls"], 168, "provider.frozen_total")
    assert_equal(provider["budget_exceeded"], False, "provider.budget_exceeded")
    print("VALIDATION ARTIFACT VERIFIED")
    print(f"cases={len(predictions)} dataset_sha256={dataset_sha}")
    print(
        "micro_precision={:.2%} micro_recall={:.2%} micro_f1={:.2%} exact_set={:.2%}".format(
            metrics["micro_precision"],
            metrics["micro_recall"],
            metrics["micro_f1"],
            metrics["exact_set_accuracy"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
