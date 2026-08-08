#!/usr/bin/env python3
# ruff: noqa: E501
"""Run candidate M6 labels without publishing a formal SEALED result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.database import SessionLocal
from app.services.evaluation import EvaluationRunner, ExposureAuditor, load_manifest
from app.services.evaluation.core import canonical_sha256


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(report)
    report["report_sha256"] = canonical_sha256(report)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--constructed",
        type=Path,
        default=Path("data/evaluations/candidates/m6b_constructed_candidate_v1.json"),
    )
    parser.add_argument(
        "--external",
        type=Path,
        default=Path("data/evaluations/candidates/m6c_external_candidate_v2.json"),
    )
    parser.add_argument(
        "--inventory", type=Path, default=Path("data/evaluations/exposure_inventory_v1.json")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/provisional"))
    args = parser.parse_args()
    constructed = load_manifest(args.constructed, require_sealed=False)
    external = load_manifest(args.external, require_sealed=False)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    runner = EvaluationRunner()
    with SessionLocal() as session:
        constructed_report = runner.run_constructed_provisional(session, constructed)
        external_report = runner.run_external_provisional(
            session, external, ExposureAuditor(inventory)
        )
    _write(args.output_dir / "m6b_constructed_provisional_v1.json", constructed_report)
    _write(args.output_dir / "m6c_external_provisional_v2.json", external_report)
    print(
        json.dumps(
            {"constructed": constructed_report["status"], "external": external_report["status"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
