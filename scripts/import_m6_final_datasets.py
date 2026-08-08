#!/usr/bin/env python3
# ruff: noqa: E501
"""Adapt the authoritative M6 final handoff into unsealed evaluation manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.evaluation.core import (
    MANIFEST_SCHEMA_VERSION,
    manifest_sha256,
    write_manifest_new_version,
)
from app.services.screening import load_ruleset


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _material_type(value: str) -> str:
    return {
        "社媒": "social_media",
        "销售话术": "sales_script",
        "短信": "sms",
        "广告": "advertisement",
        "产品介绍": "product_introduction",
    }.get(value, "other")


def _manifest(
    track: str, version: str, cases: list[dict[str, Any]], sources: list[dict[str, str]]
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "track": track,
        "version": version,
        "status": "DRAFT",
        "cases": cases,
        "source_artifacts": sources,
        "provenance": [
            {
                "event": "IMPORT_CANDIDATE",
                "actor": "M6_FINAL_INTEGRATION_INPUT_v1",
                "note": "AI-assisted candidate labels only; not HUMAN_REVIEWED or SEALED.",
            }
        ],
    }
    manifest["content_sha256"] = manifest_sha256(manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_root", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("data/evaluations/candidates"))
    args = parser.parse_args()
    root = args.input_root
    b_dir = root / "01_M6B_构造案例_当前有效"
    c_dir = root / "02_M6C_官方案例_当前有效"
    source_path = b_dir / "M6B_constructed_benchmark_DRAFT_v1.jsonl"
    annotation_path = b_dir / "M6B_AI_ANNOTATED_PENDING_HUMAN_REVIEW_v1.jsonl"
    external_path = c_dir / "M6C_official_external_VERIFIED_DRAFT_v2.jsonl"
    source, annotations, external = _rows(source_path), _rows(annotation_path), _rows(external_path)
    expected_hashes = {
        source_path: "0f4f3a7eb066c026196a110cc13238d774fc696c52cd53a982283791b626a2bd",
        annotation_path: "36141f66c7f11b72e5294356f3c85d15cc50838654364ad119141f03c29c0f66",
        external_path: "2158374bfffcd058c53cda7189c844b9eff8b0e234dc6346f2c889f26ea1653a",
    }
    if any(_sha(path) != expected for path, expected in expected_hashes.items()):
        raise SystemExit("authoritative_input_sha_mismatch")
    if len(source) != 400 or len(annotations) != 400 or len(external) != 40:
        raise SystemExit("authoritative_input_count_mismatch")
    by_id = {row["case_id"]: row for row in annotations}
    if len(by_id) != 400 or set(by_id) != {row["case_id"] for row in source}:
        raise SystemExit("m6b_case_id_mismatch")
    valid_rules = {rule.rule_id for rule in load_ruleset().rules}
    constructed: list[dict[str, Any]] = []
    for row in source:
        label = by_id[row["case_id"]]
        if row["text_sha256"] != label["text_sha256"] or row["text"] != label["text"]:
            raise SystemExit(f"m6b_text_mismatch:{row['case_id']}")
        labels = sorted(set(label["ai_rule_ids"]))
        if not set(labels) <= valid_rules:
            raise SystemExit(f"m6b_unknown_rule:{row['case_id']}")
        constructed.append(
            {
                "case_id": row["case_id"],
                "text": row["text"],
                "text_sha256": row["text_sha256"],
                "material_type": _material_type(row["material_type"]),
                "source_material_type": row["material_type"],
                "difficulty": row["difficulty"],
                "composition": row["composition"],
                "expected_rule_ids": labels,
                "label_status": "AI_ANNOTATED_PENDING_HUMAN_REVIEW",
                "human_review_status": "DRAFT",
                "ai_confidence": label["ai_confidence"],
                "needs_human_review": label["needs_human_review"],
                "review_reason": label["review_reason"],
            }
        )
    external_cases: list[dict[str, Any]] = []
    for row in external:
        labels = sorted(set(row["expected_rule_ids"]))
        if not set(labels) <= valid_rules:
            raise SystemExit(f"m6c_unknown_rule:{row['case_id']}")
        external_cases.append(
            {
                "case_id": row["case_id"],
                "relevant_excerpt": row["official_excerpt"],
                "excerpt_sha256": row["excerpt_sha256"],
                "expected_rule_ids": labels,
                "material_type": "other",
                "scope_status": row["scope_status"],
                "out_of_scope_reason": row["out_of_scope_reason"],
                "canonical_url": row["canonical_url"],
                "final_url": row["final_url"],
                "source_document_sha256": row["source_document_sha256"],
                "source_verification_status": row["source_verification_status"],
                "source_title": row["source_title"],
                "source_locator": row["source_locator"],
                "authority": row["authority"],
                "publication_date": row["publication_date"],
                "contamination_status": row["contamination_status"],
                "independence_claim": row["independence_claim"],
                "annotation_status": row["annotation_status"],
                "constructed": False,
            }
        )
    if (
        len({r["case_id"] for r in constructed}) != 400
        or len({r["text_sha256"] for r in constructed}) != 400
    ):
        raise SystemExit("m6b_uniqueness_failure")
    if len({r["canonical_url"] for r in external_cases}) != 40:
        raise SystemExit("m6c_url_uniqueness_failure")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        {"path": str(path.relative_to(root)), "sha256": _sha(path)} for path in expected_hashes
    ]
    write_manifest_new_version(
        args.output_dir / "m6b_constructed_candidate_v1.json",
        _manifest("constructed", "m6b_constructed_candidate_v1", constructed, sources[:2]),
    )
    write_manifest_new_version(
        args.output_dir / "m6c_external_candidate_v2.json",
        _manifest("external", "m6c_external_candidate_v2", external_cases, sources[2:]),
    )
    print("M6-B candidate: 400 cases (280 positive / 120 zero-label)")
    print("M6-C candidate: 40 cases (32 in-scope / 8 out-of-scope), v2 only")


if __name__ == "__main__":
    main()
