"""Scan reachable Git blobs for high-confidence secrets without printing values."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scan_release_secrets import (
    BEARER,
    ENV_ASSIGNMENT,
    PRIVATE_KEY,
    SK_KEY,
    config_like,
    is_safe_value,
)

ROOT = Path(__file__).resolve().parents[1]


def blob_findings(path: Path, text: str) -> set[str]:
    findings: set[str] = set()
    for match in SK_KEY.finditer(text):
        if not is_safe_value(match.group(0)):
            findings.add("api_key")
    for match in BEARER.finditer(text):
        if not is_safe_value(match.group(0)):
            findings.add("bearer_token")
    if PRIVATE_KEY.search(text):
        findings.add("private_key")
    if config_like(path):
        for line in text.splitlines():
            match = ENV_ASSIGNMENT.match(line)
            if match and not is_safe_value(match.group(2)):
                findings.add("nonempty_secret_assignment")
    return findings


def main() -> int:
    objects = subprocess.check_output(
        ["git", "rev-list", "--objects", "--all"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    hits: set[tuple[str, str]] = set()
    for row in objects:
        object_id, separator, name = row.partition(" ")
        if not separator or not name:
            continue
        object_type = subprocess.check_output(
            ["git", "cat-file", "-t", object_id],
            cwd=ROOT,
            text=True,
        ).strip()
        if object_type != "blob":
            continue
        content = subprocess.check_output(
            ["git", "cat-file", "-p", object_id],
            cwd=ROOT,
        )
        if b"\x00" in content[:4096] or len(content) > 20 * 1024 * 1024:
            continue
        text = content.decode("utf-8", errors="ignore")
        for secret_type in blob_findings(Path(name), text):
            hits.add((name, secret_type))
    if hits:
        print(f"SECRET_HISTORY_EXPOSURE=YES findings={len(hits)}")
        for path, secret_type in sorted(hits):
            print(f"{path}: {secret_type}")
        return 1
    print("SECRET_HISTORY_EXPOSURE=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
