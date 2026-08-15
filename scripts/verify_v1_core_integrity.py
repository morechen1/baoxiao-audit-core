"""Fail if any frozen V1 detector, RAG, schema or trusted-corpus byte changes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASELINE = "16ebd6b2dece09a66f9d130ee9e87c669ca2305e"
ROOT = Path(__file__).resolve().parents[1]
PROTECTED = (
    "app/rules",
    "app/services/screening",
    "app/services/explanation",
    "app/services/knowledge",
    "app/prompts",
    "app/models/entities.py",
    "app/models/enums.py",
    "migrations",
    "release_assets/trusted_data",
)


def git(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def baseline_paths() -> set[str]:
    output = git("ls-tree", "-r", "--name-only", BASELINE, "--", *PROTECTED).decode()
    return {line for line in output.splitlines() if line}


def working_paths() -> set[str]:
    paths: set[str] = set()
    for protected in PROTECTED:
        target = ROOT / protected
        if target.is_file():
            paths.add(protected)
            continue
        if not target.exists():
            continue
        for path in target.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            paths.add(path.relative_to(ROOT).as_posix())
    return paths


def main() -> int:
    try:
        expected = baseline_paths()
    except subprocess.CalledProcessError:
        print("V1 CORE INTEGRITY FAILED: baseline commit unavailable", file=sys.stderr)
        return 1
    actual = working_paths()
    failures: list[str] = []
    for path in sorted(expected - actual):
        failures.append(f"missing:{path}")
    for path in sorted(actual - expected):
        failures.append(f"unexpected:{path}")
    for path in sorted(expected & actual):
        baseline_bytes = git("show", f"{BASELINE}:{path}")
        if (ROOT / path).read_bytes() != baseline_bytes:
            failures.append(f"changed:{path}")
    if failures:
        print("V1 CORE INTEGRITY FAILED", file=sys.stderr)
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print("V1 CORE INTEGRITY VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
