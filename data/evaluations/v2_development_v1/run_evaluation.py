from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.screening import (
    DeterministicComplianceRuleEngine,
    DeterministicScreeningService,
    HybridScreeningService,
    OpenAICompatibleSemanticParserProvider,
    SemanticClaimParser,
    SemanticParserError,
    load_ruleset,
    segment_marketing_text,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATASET = HERE / "v2_development_v1.jsonl"
MAX_DEVELOPMENT_RUNS = 2
TOTAL_PROVIDER_BUDGET = 300


class BudgetedProvider:
    def __init__(self, remaining: int) -> None:
        self.inner = OpenAICompatibleSemanticParserProvider(get_settings())
        self.remaining = remaining

    def generate(self, raw_text: str) -> Any:
        if self.remaining <= 0:
            raise SemanticParserError("provider_call_budget_exhausted")
        try:
            response = self.inner.generate(raw_text)
        except SemanticParserError as exc:
            self.remaining -= exc.provider_calls
            raise
        self.remaining -= response.provider_calls
        return response


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def metrics(records: list[dict[str, Any]], rules: list[str]) -> dict[str, Any]:
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
    positives = [row for row in records if row["expected_rules"]]
    negatives = [row for row in records if not row["expected_rules"]]
    hard_negatives = [row for row in negatives if row["stratum"] == "hard_negative"]
    return {
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_fn,
        "micro_precision": micro_p,
        "micro_recall": micro_r,
        "micro_f1": safe_div(2 * micro_p * micro_r, micro_p + micro_r),
        "macro_precision": sum(row["precision"] for row in per_rule.values()) / len(rules),
        "macro_recall": sum(row["recall"] for row in per_rule.values()) / len(rules),
        "macro_f1": sum(row["f1"] for row in per_rule.values()) / len(rules),
        "exact_set": safe_div(
            sum(set(row["expected_rules"]) == set(row["predicted_rules"]) for row in records),
            len(records),
        ),
        "positive_hit": safe_div(
            sum(
                bool(set(row["expected_rules"]) & set(row["predicted_rules"])) for row in positives
            ),
            len(positives),
        ),
        "negative_false_alarm": safe_div(
            sum(bool(row["predicted_rules"]) for row in negatives), len(negatives)
        ),
        "hard_negative_false_alarm": safe_div(
            sum(bool(row["predicted_rules"]) for row in hard_negatives), len(hard_negatives)
        ),
        "specificity": 1
        - safe_div(sum(bool(row["predicted_rules"]) for row in negatives), len(negatives)),
        "per_rule": per_rule,
    }


def source_fingerprint() -> dict[str, str]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    diff = subprocess.check_output(
        [
            "git",
            "diff",
            "--binary",
            "--",
            "app/core",
            "app/services/screening",
            "app/rules",
        ],
        cwd=ROOT,
    )
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "app/services/screening"],
        cwd=ROOT,
        text=True,
    )
    for path in sorted(value for value in untracked.splitlines() if value):
        diff += path.encode() + b"\0" + (ROOT / path).read_bytes()
    return {"head": head, "algorithm_tree_sha256": hashlib.sha256(diff).hexdigest()}


def next_run() -> tuple[int, int]:
    summaries = sorted(HERE.glob("development_run_*_summary.json"))
    if len(summaries) >= MAX_DEVELOPMENT_RUNS:
        raise RuntimeError("v2_development_run_limit_reached")
    prior_calls = sum(json.loads(path.read_text())["provider_calls"] for path in summaries)
    return len(summaries) + 1, prior_calls


def material_sha(case: dict[str, Any]) -> str:
    payload = {
        "title": f"V2 Development {case['case_id']}",
        "material_type": "advertisement",
        "raw_text": case["text"],
        "source_label": "v2_development_v1",
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
    parser = SemanticClaimParser(
        ruleset=ruleset,
        settings=settings,
        provider=provider,
    )
    service = HybridScreeningService(
        settings=settings,
        deterministic=DeterministicScreeningService(
            ruleset=ruleset, settings=settings, semantic_parser=parser
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
            title=f"V2 Development {case['case_id']}",
            material_type="advertisement",
            raw_text=case["text"],
            source_label="v2_development_v1",
            external_reference=case["case_id"],
            is_constructed_evaluation=True,
        )
        report = cast(dict[str, Any], service.show(session, run.id))
    diagnostics = report["evidence_evaluation_summary"]["semantic_parser"]
    findings = []
    for finding in report["findings"]:
        identity = (
            finding["rule_id"],
            finding["raw_start_offset"],
            finding["raw_end_offset"],
        )
        quote_exact = (
            case["text"][finding["raw_start_offset"] : finding["raw_end_offset"]]
            == finding["matched_text"]
        )
        findings.append(
            {
                "rule_id": finding["rule_id"],
                "matched_text": finding["matched_text"],
                "raw_start_offset": finding["raw_start_offset"],
                "raw_end_offset": finding["raw_end_offset"],
                "provenance": "deterministic" if identity in deterministic_identity else "semantic",
                "exact_source_quote": quote_exact,
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


def grouped_metrics(records: list[dict[str, Any]], rules: list[str], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        groups.setdefault(str(row[key]), []).append(row)
    return {
        name: {"cases": len(values), **metrics(values, rules)}
        for name, values in sorted(groups.items())
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorized-provider-send", action="store_true")
    args = parser.parse_args()
    if not args.authorized_provider_send:
        raise RuntimeError("explicit_provider_authorization_required")
    settings = get_settings()
    if not settings.semantic_parser_enabled or not settings.llm_api_key:
        raise RuntimeError("semantic_parser_provider_not_configured")
    records = load_jsonl(DATASET)
    if len(records) != 120 or any(row["split"] != "DEV_ONLY" for row in records):
        raise RuntimeError("v2_development_dataset_invalid")
    run_number, prior_calls = next_run()
    provider = BudgetedProvider(TOTAL_PROVIDER_BUDGET - prior_calls)
    predictions: list[dict[str, Any]] = []
    for case in records:
        predictions.append(evaluate_case(case, provider))
    provider_calls = sum(row["provider_calls"] for row in predictions)
    if prior_calls + provider_calls > TOTAL_PROVIDER_BUDGET:
        raise RuntimeError("v2_development_provider_budget_exceeded")
    rules = [rule.rule_id for rule in load_ruleset().rules]
    result = metrics(predictions, rules)
    semantic_findings = [
        finding
        for row in predictions
        for finding in row["findings"]
        if finding["provenance"] == "semantic"
    ]
    summary = {
        "dataset": "v2_development_v1",
        "split": "DEV_ONLY",
        "headline_benchmark": False,
        "run_number": run_number,
        "source": source_fingerprint(),
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "cases": len(predictions),
        "provider_calls": provider_calls,
        "prior_development_calls": prior_calls,
        "retries": sum(row["retries"] for row in predictions),
        "provider_latency_ms": {
            "total": round(sum(row["provider_latency_ms"] for row in predictions), 2),
            "average": round(
                sum(row["provider_latency_ms"] for row in predictions) / len(predictions), 2
            ),
        },
        "metrics": result,
        "strata": grouped_metrics(predictions, rules, "stratum"),
        "context_groups": grouped_metrics(predictions, rules, "context_group"),
        "quote_integrity": safe_div(
            sum(row["exact_source_quote"] for row in semantic_findings), len(semantic_findings)
        ),
        "hallucinated_quote_accepted": sum(
            row["hallucinated_quote_accepted"] for row in predictions
        ),
        "provider_statuses": dict(Counter(row["provider_status"] for row in predictions)),
    }
    stem = f"development_run_{run_number:02d}"
    (HERE / f"{stem}_predictions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in predictions),
        encoding="utf-8",
    )
    (HERE / f"{stem}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
