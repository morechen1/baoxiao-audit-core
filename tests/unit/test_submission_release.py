from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.rebind_trusted_asset_paths import resolve_asset_paths

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_validation_artifacts_verify_offline() -> None:
    completed = subprocess.run(
        ["python3", "scripts/verify_final_validation.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "VALIDATION ARTIFACT VERIFIED" in completed.stdout
    assert "micro_f1=83.93%" in completed.stdout


def test_secret_scanner_never_prints_secret_value(tmp_path: Path) -> None:
    secret = "sk-" + "a1b2c3d4" * 4
    config = tmp_path / "config"
    config.mkdir()
    (config / "local.env").write_text(f"LLM_API_KEY={secret}\n", encoding="utf-8")
    completed = subprocess.run(
        ["python3", str(ROOT / "scripts/scan_release_secrets.py"), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "config/local.env: api_key" in completed.stdout
    assert secret not in completed.stdout
    assert secret not in completed.stderr


def test_competition_runtime_secret_policy_allows_exact_credential_file(
    tmp_path: Path,
) -> None:
    secret = "sk-" + "c0ffee12" * 4
    config = tmp_path / "config"
    config.mkdir()
    (config / "local.env").write_text(
        "\n".join(
            (
                "LLM_ENABLED=true",
                "LLM_PROVIDER=openai_compatible",
                "LLM_BASE_URL=https://api.deepseek.com",
                "LLM_MODEL=deepseek-v4-flash",
                f"LLM_API_KEY={secret}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts/scan_release_secrets.py"),
            str(tmp_path),
            "--mode",
            "competition-runtime",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout
    assert "COMPETITION_RUNTIME_CHECK=PASS" in completed.stdout
    assert "Actual credential files: 1" in completed.stdout
    assert "Unexpected secret files: 0" in completed.stdout
    assert secret not in completed.stdout
    assert secret not in completed.stderr


def test_competition_runtime_secret_policy_rejects_other_locations(
    tmp_path: Path,
) -> None:
    secret = "sk-" + "deadbeef" * 4
    config = tmp_path / "config"
    config.mkdir()
    (config / "local.env").write_text(f"LLM_API_KEY={secret}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(f"do not leak {secret}\n", encoding="utf-8")
    completed = subprocess.run(
        [
            "python3",
            str(ROOT / "scripts/scan_release_secrets.py"),
            str(tmp_path),
            "--mode",
            "competition-runtime",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "COMPETITION_RUNTIME_CHECK=FAIL" in completed.stdout
    assert "Unexpected secret files: 1" in completed.stdout
    assert secret not in completed.stdout
    assert secret not in completed.stderr


def test_competition_runtime_builder_places_credential_once_without_printing_it(
    tmp_path: Path,
) -> None:
    secret = "sk-" + "1234abcd" * 4
    source = tmp_path / "source" / "保销智审_完整一键启动版"
    (source / "config").mkdir(parents=True)
    (source / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/scan_release_secrets.py", source / "scripts")
    (source / "config/local.env.example").write_text("LLM_API_KEY=\n", encoding="utf-8")
    (source / "一键启动.command").write_text("#!/bin/sh\n", encoding="utf-8")
    (source / "scripts/start_project.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (source / "PACKAGE_INFO.txt").write_text("source\n", encoding="utf-8")
    (source / "RELEASE_MANIFEST.json").write_text(
        json.dumps(
            {
                "current_release_commit": "a4b139cf64757cf52f292107eb1c7aac3f7f7e75",
                "source_commit": "a4b139cf64757cf52f292107eb1c7aac3f7f7e75",
                "secrets_included": False,
            }
        ),
        encoding="utf-8",
    )
    (source / "SHA256SUMS").write_text("", encoding="utf-8")
    credential = tmp_path / "local.env"
    credential.write_text(
        "\n".join(
            (
                "LLM_BASE_URL=https://api.deepseek.com",
                "LLM_MODEL=deepseek-v4-flash",
                f"LLM_API_KEY={secret}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    output_zip = tmp_path / "runtime.zip"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_competition_runtime_package.py"),
            "--source-package-dir",
            str(source),
            "--staging-root",
            str(tmp_path / "runtime"),
            "--runtime-config",
            str(credential),
            "--zip-path",
            str(output_zip),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "COMPETITION_RUNTIME_CHECK=PASS" in completed.stdout
    assert secret not in completed.stdout
    assert secret not in completed.stderr
    with zipfile.ZipFile(output_zip) as archive:
        names = archive.namelist()
        credential_name = "保销智审_完整一键启动版/config/local.env"
        assert names.count(credential_name) == 1
        manifest = json.loads(archive.read("保销智审_完整一键启动版/RELEASE_MANIFEST.json"))
        assert manifest["runtime_provider_credential_included"] is True
        assert manifest["runtime_provider"] == "DeepSeek"
        assert manifest["runtime_model"] == "deepseek-v4-flash"


def test_release_config_and_launchers_are_safe() -> None:
    template = (ROOT / "config/local.env.example").read_text(encoding="utf-8")
    assert "LLM_API_KEY=\n" in template
    assert "SEMANTIC_PARSER_ENABLED=true" in template
    assert "SEMANTIC_SCREENING_ENABLED=false" in template
    assert not (ROOT / "config/local.env").exists()
    for path in (
        ROOT / "一键启动.command",
        ROOT / "停止程序.command",
        ROOT / "检查完整性.command",
        ROOT / "scripts/start_project.sh",
    ):
        assert path.exists()
        assert os.access(path, os.X_OK)
    integrity = (ROOT / "检查完整性.command").read_text(encoding="utf-8")
    assert 'SCAN_MODE="competition-runtime"' in integrity
    assert 'scripts/scan_release_secrets.py "$ROOT_DIR" --mode "$SCAN_MODE"' in integrity
    assert "程序完整性：PASS" in integrity
    assert "运行凭据：已配置" in integrity
    assert "Provider：READY" in integrity


def test_release_contains_complete_trusted_restore_assets() -> None:
    trusted = ROOT / "release_assets/trusted_data"
    assert len(list((trusted / "raw").iterdir())) == 15
    assert len(list((trusted / "parsed_artifacts").iterdir())) == 15
    assert (trusted / "m7_legacy_current_identity_map.json").is_file()
    archives = ROOT / "knowledge_archives"
    assert len(list(archives.glob("*.zip"))) == 5
    assert (archives / "SHA256SUMS").is_file()


def test_release_asset_rebinding_requires_exact_content_hashes(tmp_path: Path) -> None:
    raw = b"trusted raw"
    parsed = b'{"schema_version":"1.1"}'
    raw_sha = sha256(raw).hexdigest()
    parsed_sha = sha256(parsed).hexdigest()
    raw_dir = tmp_path / "raw"
    parsed_dir = tmp_path / "parsed_artifacts"
    raw_dir.mkdir()
    parsed_dir.mkdir()
    raw_path = raw_dir / f"{raw_sha}.json"
    parsed_path = parsed_dir / f"{raw_sha}-{parsed_sha}.json"
    raw_path.write_bytes(raw)
    parsed_path.write_bytes(parsed)
    document = SimpleNamespace(sha256=raw_sha, parsed_artifact_sha256=parsed_sha)

    assert resolve_asset_paths(document, tmp_path) == (raw_path.resolve(), parsed_path.resolve())

    raw_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw asset identity mismatch"):
        resolve_asset_paths(document, tmp_path)


def test_existing_complete_database_runs_safe_path_rebinding() -> None:
    launcher = (ROOT / "scripts/start_final_demo.sh").read_text(encoding="utf-8")
    assert "scripts/rebind_trusted_asset_paths.py" in launcher
    assert 'elif [[ "$document_count" != "15" ]]' in launcher
