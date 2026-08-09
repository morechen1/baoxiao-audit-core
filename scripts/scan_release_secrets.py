"""High-confidence release secret scan that never prints matched values."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".command",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SK_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
ENV_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD|TOKEN)[A-Z0-9_]*)\s*=\s*(.*?)\s*$",
    re.IGNORECASE,
)
SAFE_MARKERS = (
    "false",
    "disabled",
    "none",
    "null",
    "changeme",
    "placeholder",
    "example",
    "dummy",
    "test",
    "local_only",
    "${",
    "<",
)
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def is_safe_value(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return not normalized or any(marker in normalized for marker in SAFE_MARKERS)


def config_like(path: Path) -> bool:
    lowered = "/".join(path.parts).lower()
    return path.suffix.lower() in {".env", ".ini", ".cfg"} or any(
        word in lowered for word in ("config", "credential", "secret", "settings")
    )


def findings_for(path: Path, root: Path) -> set[str]:
    relative = path.relative_to(root)
    if any(part in SKIP_PARTS for part in relative.parts):
        return set()
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".env", ".env.example"}:
        return set()
    try:
        raw = path.read_bytes()
    except OSError:
        return set()
    if b"\x00" in raw[:4096] or len(raw) > 20 * 1024 * 1024:
        return set()
    text = raw.decode("utf-8", errors="ignore")
    findings: set[str] = set()
    for match in SK_KEY.finditer(text):
        if not is_safe_value(match.group(0)):
            findings.add("api_key")
    for match in BEARER.finditer(text):
        if not is_safe_value(match.group(0)):
            findings.add("bearer_token")
    if PRIVATE_KEY.search(text):
        findings.add("private_key")
    if config_like(relative):
        for line in text.splitlines():
            match = ENV_ASSIGNMENT.match(line)
            if match and not is_safe_value(match.group(2)):
                findings.add("nonempty_secret_assignment")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit("SECRET_SCAN FAIL: scan root is not a directory")
    hits: list[tuple[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        for secret_type in sorted(findings_for(path, root)):
            hits.append((path.relative_to(root).as_posix(), secret_type))
    if hits:
        print(f"SECRET_SCAN FAIL real_secrets={len(hits)}")
        for path, secret_type in hits:
            print(f"{path}: {secret_type}")
        return 1
    print("SECRET_SCAN PASS real_secrets=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
