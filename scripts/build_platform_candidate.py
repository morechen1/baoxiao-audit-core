"""Build a secret-free V1-core platform-integration candidate directory and ZIP."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT.parent
PACKAGE_NAME = "保销智审_平台增强候选版"
BASELINE = "16ebd6b2dece09a66f9d130ee9e87c669ca2305e"


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def require_candidate_checkpoint() -> str:
    if run("git", "status", "--porcelain"):
        raise RuntimeError("candidate_requires_clean_worktree")
    branch = run("git", "branch", "--show-current")
    if branch != "upgrade/v1-core-v2-platform":
        raise RuntimeError("candidate_requires_integration_branch")
    subprocess.run(
        [sys.executable, "scripts/verify_v1_core_integrity.py"],
        cwd=ROOT,
        check=True,
    )
    return run("git", "rev-parse", "HEAD")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_head(destination: Path) -> None:
    destination.mkdir(parents=True)
    with tempfile.NamedTemporaryFile(suffix=".tar") as archive:
        subprocess.run(
            ["git", "archive", "--format=tar", "HEAD", "-o", archive.name],
            cwd=ROOT,
            check=True,
        )
        with tarfile.open(archive.name) as bundle:
            bundle.extractall(destination, filter="data")


def verify_structure(destination: Path) -> None:
    required = (
        "README.md",
        "RUN_PROJECT.md",
        "一键启动.command",
        "scripts/start_project.sh",
        "scripts/start_final_demo.sh",
        "scripts/verify_v1_core_integrity.py",
        "app/services/platform/ingestion.py",
        "app/services/platform/screening.py",
        "app/services/platform/batch.py",
        "app/services/platform/export.py",
        "tests/unit/test_platform_integration.py",
        "migrations/env.py",
        "release_assets/trusted_data/m7_legacy_current_identity_map.json",
        "data/evaluations/final_extended_frozen_validation_v1/final_validation_predictions_v1.jsonl",
        "docs/PLATFORM_INTEGRATION.md",
        "config/local.env.example",
    )
    missing = [value for value in required if not (destination / value).exists()]
    if missing:
        raise RuntimeError(f"candidate_missing_paths:{','.join(missing)}")
    forbidden = (
        destination / ".git",
        destination / ".venv",
        destination / "config/local.env",
        destination / "runtime",
    )
    if any(path.exists() for path in forbidden):
        raise RuntimeError("candidate_contains_forbidden_runtime_path")


def write_metadata(destination: Path, commit: str) -> None:
    created_at = datetime.now(UTC).isoformat()
    manifest = {
        "project": "保销智审",
        "release_type": "V1 CORE + PLATFORM INTEGRATION CANDIDATE",
        "source_commit": commit,
        "v1_baseline": BASELINE,
        "detection_baseline": "V1",
        "trusted_sources": 15,
        "knowledge_chunks": 73,
        "frozen_validation_cases": 156,
        "secrets_included": False,
        "venv_included": False,
        "created_at": created_at,
    }
    (destination / "PLATFORM_INTEGRATION_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination / "CANDIDATE_INFO.txt").write_text(
        "\n".join(
            (
                "Project: 保销智审",
                "Status: INTEGRATION CANDIDATE",
                f"Source HEAD: {commit}",
                f"V1 core baseline: {BASELINE}",
                "Detection core changed: NO",
                "Startup: 一键启动.command",
                "Secrets included: NO",
                "Bundled .venv: NO",
                "",
            )
        ),
        encoding="utf-8",
    )


def write_checksums(destination: Path) -> None:
    checksum = destination / "SHA256SUMS"
    files = sorted(path for path in destination.rglob("*") if path.is_file() and path != checksum)
    checksum.write_text(
        "".join(f"{sha256(path)}  {path.relative_to(destination).as_posix()}\n" for path in files),
        encoding="utf-8",
    )


def create_zip(destination: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(PACKAGE_NAME) / path.relative_to(destination)).as_posix())


def main() -> int:
    commit = require_candidate_checkpoint()
    output_root = DEFAULT_OUTPUT_ROOT
    destination = output_root / PACKAGE_NAME
    zip_path = output_root / f"{PACKAGE_NAME}.zip"
    if destination.exists():
        shutil.rmtree(destination)
    if zip_path.exists():
        zip_path.unlink()
    extract_head(destination)
    verify_structure(destination)
    write_metadata(destination, commit)
    scan = subprocess.run(
        [sys.executable, str(destination / "scripts/scan_release_secrets.py"), str(destination)],
        check=False,
    )
    if scan.returncode != 0:
        raise RuntimeError("candidate_secret_scan_failed")
    write_checksums(destination)
    create_zip(destination, zip_path)
    print(f"CANDIDATE_DIRECTORY={destination}")
    print(f"CANDIDATE_ZIP={zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
