from __future__ import annotations

import os
import subprocess
from pathlib import Path

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


def test_release_contains_complete_trusted_restore_assets() -> None:
    trusted = ROOT / "release_assets/trusted_data"
    assert len(list((trusted / "raw").iterdir())) == 15
    assert len(list((trusted / "parsed_artifacts").iterdir())) == 15
    assert (trusted / "m7_legacy_current_identity_map.json").is_file()
    archives = ROOT / "knowledge_archives"
    assert len(list(archives.glob("*.zip"))) == 5
    assert (archives / "SHA256SUMS").is_file()
