"""Build the competition runtime ZIP from an already-clean source release."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_NAME = "保销智审_完整一键启动版"
RUNTIME_RELEASE_TYPE = "COMPETITION RUNTIME FULL-SOURCE PACKAGE"
RUNTIME_PROVIDER = "DeepSeek"
RUNTIME_MODEL = "deepseek-v4-flash"
RUNTIME_NOTE = """本压缩包为赛事评测运行包。

程序已预配置模型服务运行凭据，可直接使用一键启动程序体验完整功能。

运行凭据仅用于本次赛事评测，不属于开源/源码配置。

请使用：
一键启动.command

系统启动后访问：
http://localhost:8888
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_runtime_config(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError("runtime_credential_missing")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    if not values.get("LLM_API_KEY"):
        raise RuntimeError("runtime_api_key_missing")
    if "deepseek.com" not in values.get("LLM_BASE_URL", ""):
        raise RuntimeError("runtime_provider_endpoint_invalid")
    if values.get("LLM_MODEL") != RUNTIME_MODEL:
        raise RuntimeError("runtime_model_invalid")
    return values


def write_package_info(root: Path, manifest: dict[str, object], build_id: str) -> None:
    commit = str(manifest.get("current_release_commit") or manifest.get("source_commit") or "")
    content = f"""Project: 保销智审
Release type: {RUNTIME_RELEASE_TYPE}
Current release commit: {commit}
Detection baseline: V1 frozen core
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
Runtime Provider Credential: INCLUDED FOR COMPETITION USE
Credential value: NOT DISPLAYED
Credential tracked in Git: NO
Provider: {RUNTIME_PROVIDER}
Model: {RUNTIME_MODEL}
Local virtualenv included: NO
Git history included: NO
Startup: 一键启动.command / scripts/start_project.sh
Runtime package build ID: {build_id}
"""
    (root / "PACKAGE_INFO.txt").write_text(content, encoding="utf-8")


def write_manifest(root: Path, manifest: dict[str, object], build_id: str) -> None:
    manifest.pop("secrets_included", None)
    manifest.update(
        {
            "release_type": RUNTIME_RELEASE_TYPE,
            "release_version": "platform-enhanced-submission-v1.2-competition-runtime",
            "runtime_provider_credential_included": True,
            "runtime_provider": RUNTIME_PROVIDER,
            "runtime_model": RUNTIME_MODEL,
            "runtime_credential_tracked_in_git": False,
            "runtime_package_build_id": build_id,
        }
    )
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


def verify_runtime_structure(root: Path) -> None:
    required = (
        root / "config/local.env",
        root / "config/local.env.example",
        root / "运行版说明.txt",
        root / "PACKAGE_INFO.txt",
        root / "RELEASE_MANIFEST.json",
        root / "SHA256SUMS",
        root / "一键启动.command",
        root / "scripts/start_project.sh",
    )
    if any(not path.is_file() for path in required):
        raise RuntimeError("competition_runtime_structure_incomplete")
    forbidden_names = {".git", ".venv", "runtime", "__MACOSX", "__pycache__"}
    if any(path.is_dir() and path.name in forbidden_names for path in root.rglob("*")):
        raise RuntimeError("competition_runtime_contains_forbidden_directory")
    if any(path.name == ".DS_Store" or path.suffix == ".log" for path in root.rglob("*")):
        raise RuntimeError("competition_runtime_contains_forbidden_file")


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
    parser.add_argument("--source-package-dir", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--zip-path", type=Path, required=True)
    args = parser.parse_args()

    source_package = args.source_package_dir.resolve()
    staging_root = args.staging_root.resolve()
    runtime_config = args.runtime_config.resolve()
    zip_path = args.zip_path.resolve()
    if not source_package.is_dir() or (source_package / "config/local.env").exists():
        raise RuntimeError("source_package_is_not_clean")
    read_runtime_config(runtime_config)
    if staging_root.exists():
        shutil.rmtree(staging_root)
    package_dir = staging_root / PACKAGE_NAME
    shutil.copytree(source_package, package_dir)
    shutil.copyfile(runtime_config, package_dir / "config/local.env")
    (package_dir / "运行版说明.txt").write_text(RUNTIME_NOTE, encoding="utf-8")

    manifest_path = package_dir / "RELEASE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    created_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    commit = str(manifest.get("current_release_commit") or manifest.get("source_commit") or "")
    build_id = f"competition-runtime-{created_at}-{commit[:12]}"
    write_package_info(package_dir, manifest, build_id)
    write_manifest(package_dir, manifest, build_id)
    write_checksums(package_dir)
    verify_runtime_structure(package_dir)

    scan = subprocess.run(
        [
            sys.executable,
            str(package_dir / "scripts/scan_release_secrets.py"),
            str(package_dir),
            "--mode",
            "competition-runtime",
        ],
        check=False,
    )
    if scan.returncode != 0:
        raise RuntimeError("competition_runtime_secret_scan_failed")
    create_zip(package_dir, zip_path)
    print(f"RUNTIME_PACKAGE_BUILD_ID={build_id}")
    print(f"RUNTIME_DIRECTORY={package_dir}")
    print(f"RUNTIME_ZIP={zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
