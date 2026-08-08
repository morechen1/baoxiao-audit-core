#!/usr/bin/env python3
# ruff: noqa: E501
"""Future human-approved M6 seal; deliberately refuses to infer human review."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.evaluation import EvaluationRunner, ExposureAuditor, ReportStore, load_manifest
from app.services.evaluation.core import (
    ManifestError,
    manifest_sha256,
    seal_manifest,
    write_manifest_new_version,
)


def _human_reviewed(
    candidate: dict[str, Any], *, reviewer: str, note: str, timestamp: str
) -> dict[str, Any]:
    if not reviewer.strip() or not note.strip():
        raise ManifestError("human_review_metadata_required")
    try:
        reviewed_at = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ManifestError("approval_timestamp_invalid") from exc
    if reviewed_at.tzinfo is None:
        raise ManifestError("approval_timestamp_timezone_required")
    reviewed = dict(candidate)
    reviewed["status"] = "HUMAN_REVIEWED"
    reviewed["provenance"] = []
    reviewed["content_sha256"] = manifest_sha256(reviewed)
    reviewed["provenance"] = [
        {
            "event": "HUMAN_REVIEWED",
            "version": reviewed["version"],
            "content_sha256": reviewed["content_sha256"],
            "parent_sha256": None,
            "actor": reviewer,
            "timestamp": reviewed_at.isoformat(),
            "note": note,
        }
    ]
    return reviewed


def _require_external_capture(manifest: dict[str, Any]) -> None:
    pending = [
        row["case_id"]
        for row in manifest["cases"]
        if row.get("source_capture_status") != "CAPTURED"
    ]
    if pending:
        raise ManifestError("external_source_capture_pending:" + ",".join(pending))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seal only after genuine project human review.")
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
    parser.add_argument("--constructed-reviewer", required=True)
    parser.add_argument("--constructed-approval-note", required=True)
    parser.add_argument("--external-reviewer", required=True)
    parser.add_argument("--external-approval-note", required=True)
    parser.add_argument(
        "--approval-timestamp", required=True, help="ISO-8601 timestamp with timezone"
    )
    parser.add_argument("--confirm-human-review", action="store_true")
    args = parser.parse_args()
    if not args.confirm_human_review:
        raise SystemExit(
            "Refusing to seal: pass --confirm-human-review only after real project review."
        )
    constructed = load_manifest(args.constructed, require_sealed=False)
    external = load_manifest(args.external, require_sealed=False)
    if constructed["status"] == "SEALED" or external["status"] == "SEALED":
        raise SystemExit("Refusing to overwrite a SEALED candidate manifest.")
    _require_external_capture(external)
    reviewed_external = _human_reviewed(
        external,
        reviewer=args.external_reviewer,
        note=args.external_approval_note,
        timestamp=args.approval_timestamp,
    )
    reviewed_constructed = _human_reviewed(
        constructed,
        reviewer=args.constructed_reviewer,
        note=args.constructed_approval_note,
        timestamp=args.approval_timestamp,
    )
    sealed_constructed = seal_manifest(
        reviewed_constructed,
        actor=args.constructed_reviewer,
        note=args.constructed_approval_note,
        parent_sha256=reviewed_constructed["content_sha256"],
    )
    sealed_external = seal_manifest(
        reviewed_external,
        actor=args.external_reviewer,
        note=args.external_approval_note,
        parent_sha256=reviewed_external["content_sha256"],
    )
    root = Path("data/evaluations")
    write_manifest_new_version(
        root / "formal" / "constructed_manifest_m6b_v1.json", sealed_constructed
    )
    write_manifest_new_version(root / "formal" / "external_manifest_m6c_v2.json", sealed_external)
    # The Evaluation Center reads these stable paths and still rejects any unsealed manifest.
    write_manifest_new_version(root / "constructed_manifest_v1.json", sealed_constructed)
    write_manifest_new_version(root / "external_manifest_v1.json", sealed_external)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    runner = EvaluationRunner()
    with SessionLocal() as session:
        constructed_report = runner.run_constructed(session, sealed_constructed)
        external_report = runner.run_external(session, sealed_external, ExposureAuditor(inventory))
    store = ReportStore(get_settings().data_dir / "evaluation_reports")
    store.write("constructed", constructed_report)
    store.write("external", external_report)
    print(
        json.dumps(
            {"constructed": constructed_report["status"], "external": external_report["status"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
