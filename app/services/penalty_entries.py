from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.review_payload import canonical_json_bytes

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


def penalty_source_entry_fingerprint(
    *,
    raw_artifact_sha256: str,
    source_entry_index: int,
    stable_source_locator: dict[str, Any],
    exact_source_entry_text_sha256: str,
) -> str:
    """Build the stable identity for one penalty entry in an immutable source artifact."""
    if not SHA256_PATTERN.fullmatch(raw_artifact_sha256):
        raise ValueError("penalty_source_sha256_invalid")
    if isinstance(source_entry_index, bool) or source_entry_index <= 0:
        raise ValueError("penalty_source_entry_index_invalid")
    if not stable_source_locator or not all(
        isinstance(key, str) and key.strip() for key in stable_source_locator
    ):
        raise ValueError("penalty_source_entry_locator_invalid")
    if not SHA256_PATTERN.fullmatch(exact_source_entry_text_sha256):
        raise ValueError("penalty_source_entry_text_sha256_invalid")
    identity = {
        "exact_source_entry_text_sha256": exact_source_entry_text_sha256,
        "raw_artifact_sha256": raw_artifact_sha256,
        "source_entry_index": source_entry_index,
        "stable_source_locator": stable_source_locator,
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def exact_source_entry_text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
