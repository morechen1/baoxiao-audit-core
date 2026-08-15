"""High-confidence release secret scan that never prints matched values."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SOURCE_MODE = "source-release"
RUNTIME_MODE = "competition-runtime"
ALLOWED_RUNTIME_CREDENTIAL = Path("config/local.env")

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


def nonempty_secret_assignments(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return sum(
        1
        for line in text.splitlines()
        if (match := ENV_ASSIGNMENT.match(line)) and not is_safe_value(match.group(2))
    )


def collect_hits(root: Path) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        for secret_type in sorted(findings_for(path, root)):
            hits.append((path.relative_to(root).as_posix(), secret_type))
    return hits


def source_release_check(hits: list[tuple[str, str]]) -> int:
    if hits:
        print("SOURCE_RELEASE_CHECK=FAIL")
        print(f"REAL_SECRETS={len(hits)}")
        for path, secret_type in hits:
            print(f"{path}: {secret_type}")
        return 1
    print("SOURCE_RELEASE_CHECK=PASS")
    print("REAL_SECRETS=0")
    return 0


def competition_runtime_check(root: Path, hits: list[tuple[str, str]]) -> int:
    allowed_path = root / ALLOWED_RUNTIME_CREDENTIAL
    allowed_hits = [item for item in hits if item[0] == ALLOWED_RUNTIME_CREDENTIAL.as_posix()]
    unexpected_hits = [item for item in hits if item[0] != ALLOWED_RUNTIME_CREDENTIAL.as_posix()]
    assignment_count = nonempty_secret_assignments(allowed_path) if allowed_path.is_file() else 0
    allowed_types = {"api_key", "nonempty_secret_assignment"}
    allowed_type_valid = bool(allowed_hits) and all(
        secret_type in allowed_types for _, secret_type in allowed_hits
    )
    actual_credential_files = int(
        allowed_path.is_file() and assignment_count == 1 and allowed_type_valid
    )
    passed = actual_credential_files == 1 and not unexpected_hits
    print(f"COMPETITION_RUNTIME_CHECK={'PASS' if passed else 'FAIL'}")
    print("Expected credential files: 1")
    print(f"Actual credential files: {actual_credential_files}")
    print(f"Allowed: {ALLOWED_RUNTIME_CREDENTIAL.as_posix()}")
    print(f"Unexpected secret files: {len({path for path, _ in unexpected_hits})}")
    if unexpected_hits:
        for path, secret_type in unexpected_hits:
            print(f"{path}: {secret_type}")
    if assignment_count != 1:
        print("Allowed credential assignment count is invalid")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--mode",
        choices=(SOURCE_MODE, RUNTIME_MODE),
        default=SOURCE_MODE,
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit("SECRET_SCAN_FAIL: scan root is not a directory")
    hits = collect_hits(root)
    if args.mode == RUNTIME_MODE:
        return competition_runtime_check(root, hits)
    return source_release_check(hits)


if __name__ == "__main__":
    raise SystemExit(main())
