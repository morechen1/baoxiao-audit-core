"""Build the full-source submission directory and ZIP from a clean Git checkpoint."""

from __future__ import annotations

import argparse
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

PROJECT_NAME = "保销智审"
PACKAGE_NAME = "保销智审_完整一键启动版"
RELEASE_TYPE = "FULL SOURCE PLATFORM-ENHANCED SUBMISSION"
DETECTION_BASE = "16ebd6b2dece09a66f9d130ee9e87c669ca2305e"
PLATFORM_INTEGRATION = "44ad1e64f1ad5b9d5e5d1a4f8457d75202d7c33d"
SYSTEM_FREEZE = "1176d10e1fd62b7778324157e585b1f22b3cb035"
EVALUATION_CHECKPOINT = "b6bdd4c15d6795106341104400d13b7b8b2e3184"
DATASET_SHA = "fb67cb12f5e44f5c55d74794856a4beb0616cd74e3d5bf656da6013e6b3736ed"
ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def require_clean_repo() -> str:
    if run("git", "status", "--porcelain"):
        raise RuntimeError("release_requires_clean_worktree")
    return run("git", "rev-parse", "HEAD")


def archive_head(destination: Path) -> None:
    destination.mkdir(parents=True)
    with tempfile.NamedTemporaryFile(suffix=".tar") as archive:
        subprocess.run(
            ["git", "archive", "--format=tar", "HEAD", "-o", archive.name],
            cwd=ROOT,
            check=True,
        )
        with tarfile.open(archive.name) as tar:
            tar.extractall(destination, filter="data")


def remove_forbidden(root: Path) -> None:
    for name in (".git", ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache", "runtime"):
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.name == "__pycache__":
            shutil.rmtree(path)
        elif path.is_file() and (
            path.name == ".DS_Store"
            or path.suffix == ".pyc"
            or path.suffix == ".log"
            or path.name in {".env", "local.env", "project.pid", "final-demo.pid"}
        ):
            path.unlink()


def package_info(commit: str, created_at: str) -> str:
    return f"""Project: {PROJECT_NAME}
Release type: {RELEASE_TYPE}
Detection baseline: V1 frozen core
Detection base commit: {DETECTION_BASE}
Platform integration commit: {PLATFORM_INTEGRATION}
Current release commit: {commit}
Trusted sources: 15
Knowledge chunks: 73
Frozen validation: 156
Micro Precision: 79.50%
Micro Recall: 88.89%
Micro F1: 83.93%
Input: Text / TXT / MD / DOCX / PDF
Batch review: YES
Long document: YES
Report: HTML / JSON
One-click: YES
Secrets included: NO
Local virtualenv included: NO
Git history included: NO
Startup: 一键启动.command / scripts/start_project.sh
Configuration template: config/local.env.example
Validation verify: python3 scripts/verify_final_validation.py
Package generated at: {created_at}
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(root: Path, commit: str, created_at: str) -> None:
    manifest = {
        "project_name": PROJECT_NAME,
        "release_type": RELEASE_TYPE,
        "release_version": "platform-enhanced-submission-v1",
        "source_commit": commit,
        "detection_base_commit": DETECTION_BASE,
        "platform_integration_commit": PLATFORM_INTEGRATION,
        "current_release_commit": commit,
        "system_freeze_commit": SYSTEM_FREEZE,
        "evaluation_commit": EVALUATION_CHECKPOINT,
        "release_commit": commit,
        "created_at": created_at,
        "python_version": ">=3.12",
        "database": "PostgreSQL 16 / baoxiao_contest_final",
        "semantic_parser_model": "deepseek-v4-flash",
        "taxonomy_count": 12,
        "trusted_source_count": 15,
        "knowledge_chunk_count": 73,
        "validation_case_count": 156,
        "validation_sha": DATASET_SHA,
        "validation_dataset_sha256": DATASET_SHA,
        "platform_capabilities": [
            "text_input",
            "txt_input",
            "markdown_input",
            "docx_input",
            "text_pdf_input",
            "long_document_orchestration",
            "batch_review",
            "evidence_link",
            "institution_explanation",
            "consumer_explanation",
            "html_report",
            "json_report",
            "fail_closed",
        ],
        "files_sha256_manifest": "SHA256SUMS",
        "secrets_included": False,
    }
    (root / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_checksums(root: Path) -> None:
    checksum_file = root / "SHA256SUMS"
    files = sorted(path for path in root.rglob("*") if path.is_file() and path != checksum_file)
    checksum_file.write_text(
        "".join(f"{sha256(path)}  {path.relative_to(root).as_posix()}\n" for path in files),
        encoding="utf-8",
    )


def verify_structure(root: Path) -> None:
    required = (
        "README.md",
        "RUN_PROJECT.md",
        "一键启动.command",
        "scripts/start_project.sh",
        "scripts/verify_final_validation.py",
        "config/local.env.example",
        "app/main.py",
        "migrations/env.py",
        "tests",
        "docs",
        "release_assets/trusted_data/m7_legacy_current_identity_map.json",
        "knowledge_archives/SHA256SUMS",
        "data/evaluations/final_extended_frozen_validation_v1/final_validation_predictions_v1.jsonl",
    )
    missing = [item for item in required if not (root / item).exists()]
    if missing:
        raise RuntimeError(f"release_missing_required_paths:{','.join(missing)}")
    forbidden = (
        root / ".git",
        root / ".venv",
        root / ".env",
        root / "config/local.env",
        root / "runtime",
    )
    if any(path.exists() for path in forbidden):
        raise RuntimeError("release_contains_forbidden_runtime_path")


def create_zip(package_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                arcname = Path(PACKAGE_NAME) / path.relative_to(package_dir)
                info = zipfile.ZipInfo.from_file(path, arcname.as_posix())
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as source:
                    archive.writestr(info, source.read(), compress_type=zipfile.ZIP_DEFLATED)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--zip-path", type=Path)
    args = parser.parse_args()
    commit = require_clean_repo()
    staging_root = args.staging_root.resolve()
    package_dir = staging_root / PACKAGE_NAME
    if staging_root.exists():
        shutil.rmtree(staging_root)
    archive_head(package_dir)
    remove_forbidden(package_dir)
    created_at = datetime.now(UTC).isoformat()
    (package_dir / "PACKAGE_INFO.txt").write_text(
        package_info(commit, created_at),
        encoding="utf-8",
    )
    write_manifest(package_dir, commit, created_at)
    write_checksums(package_dir)
    verify_structure(package_dir)
    scan = subprocess.run(
        [sys.executable, str(package_dir / "scripts/scan_release_secrets.py"), str(package_dir)],
        check=False,
    )
    if scan.returncode != 0:
        raise RuntimeError("release_secret_scan_failed")
    if args.zip_path:
        create_zip(package_dir, args.zip_path.resolve())
    print(f"RELEASE_DIRECTORY={package_dir}")
    if args.zip_path:
        print(f"RELEASE_ZIP={args.zip_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
