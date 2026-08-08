#!/usr/bin/env python3
# ruff: noqa: E501
"""Capture only M6-C manifest URLs; failed captures remain explicitly pending."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.evaluation.core import manifest_sha256, write_manifest_new_version


def _normalized(body: bytes) -> str:
    return re.sub(r"\s+", " ", body.decode("utf-8", errors="replace")).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--capture-dir", type=Path, default=Path("data/evaluations/source_captures/m6c_v2")
    )
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    captured = pending = 0
    for row in manifest["cases"]:
        capture_path = args.capture_dir / f"{row['case_id']}.txt"
        capture_path.unlink(missing_ok=True)
        request = Request(
            row["canonical_url"], headers={"User-Agent": "BaoxiaoAuditCore-M6Capture/1.0"}
        )
        try:
            with urlopen(request, timeout=args.timeout) as response:
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ValueError("source_body_too_large")
            normalized = _normalized(body)
            if not normalized:
                raise ValueError("source_body_empty")
            expected_title = re.sub(r"\s+", "", str(row.get("source_title", "")))
            expected_excerpt = re.sub(r"\s+", "", str(row.get("relevant_excerpt", "")))
            normalized_compact = re.sub(r"\s+", "", normalized)
            excerpt_signal = expected_excerpt[:12]
            if not (
                expected_title in normalized_compact
                or (excerpt_signal and excerpt_signal in normalized_compact)
            ):
                raise ValueError("source_body_does_not_contain_case_signal")
            sha = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            capture_path.write_text(normalized + "\n", encoding="utf-8")
            row["source_document_sha256"] = sha
            row["source_capture_status"] = "CAPTURED"
            row["source_capture_path"] = str(capture_path)
            captured += 1
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            row["source_document_sha256"] = "PENDING_CAPTURE"
            row["source_capture_status"] = "PENDING_CAPTURE"
            row["source_capture_error"] = f"{type(exc).__name__}: {exc}"[:300]
            row.pop("source_capture_path", None)
            pending += 1
    manifest["source_capture_updated_at"] = datetime.now(UTC).isoformat()
    manifest["content_sha256"] = manifest_sha256(manifest)
    write_manifest_new_version(args.manifest, manifest)
    print(json.dumps({"captured": captured, "pending": pending}, ensure_ascii=False))


if __name__ == "__main__":
    main()
