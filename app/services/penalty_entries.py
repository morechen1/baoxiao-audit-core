from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.review_payload import canonical_json_bytes

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
FRAGMENT_KEYS = frozenset({"quote", "start_offset", "end_offset"})
LOCATOR_BASE_KEYS = frozenset({"nfra_doc_id", "table_index"})
LOCATOR_POSITION_KEYS = frozenset({"logical_row", "numbered_entry"})


def canonical_source_entry_fragments(
    fragments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and canonicalize exact, ordered entry fragments."""
    if not isinstance(fragments, list) or not fragments:
        raise ValueError("penalty_source_entry_fragment_invalid")
    result: list[dict[str, Any]] = []
    previous_end = -1
    for fragment in fragments:
        if not isinstance(fragment, dict) or set(fragment) != FRAGMENT_KEYS:
            raise ValueError("penalty_source_entry_fragment_invalid")
        quote = fragment.get("quote")
        start = fragment.get("start_offset")
        end = fragment.get("end_offset")
        if (
            not isinstance(quote, str)
            or not quote
            or isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or start < previous_end
        ):
            raise ValueError("penalty_source_entry_fragment_invalid")
        result.append(
            {
                "quote": quote,
                "start_offset": start,
                "end_offset": end,
            }
        )
        previous_end = end
    return result


def source_entry_content_sha256(fragments: list[dict[str, Any]]) -> str:
    canonical = canonical_source_entry_fragments(fragments)
    return hashlib.sha256(canonical_json_bytes(canonical)).hexdigest()


def penalty_source_entry_fingerprint(
    *,
    raw_artifact_sha256: str,
    source_entry_content_sha256: str,
) -> str:
    """Bind one entry only to its immutable artifact and exact content fragments."""
    if not SHA256_PATTERN.fullmatch(raw_artifact_sha256):
        raise ValueError("penalty_source_sha256_invalid")
    if not SHA256_PATTERN.fullmatch(source_entry_content_sha256):
        raise ValueError("penalty_source_entry_content_sha256_mismatch")
    identity = {
        "raw_artifact_sha256": raw_artifact_sha256,
        "source_entry_content_sha256": source_entry_content_sha256,
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def validate_source_entry_locator(
    locator: dict[str, Any],
    *,
    expected_nfra_doc_id: str | None,
) -> dict[str, str | int]:
    """Validate the fixed NFRA audit locator without making it part of identity."""
    if not isinstance(locator, dict):
        raise ValueError("penalty_source_entry_locator_invalid")
    expected_keys = LOCATOR_BASE_KEYS | LOCATOR_POSITION_KEYS
    if (
        not set(locator).issubset(expected_keys)
        or "table_index" not in locator
        or len(set(locator) & LOCATOR_POSITION_KEYS) != 1
    ):
        raise ValueError("penalty_source_entry_locator_invalid")
    table_index = locator.get("table_index")
    position_key = next(iter(set(locator) & LOCATOR_POSITION_KEYS))
    position = locator.get(position_key)
    if (
        isinstance(table_index, bool)
        or not isinstance(table_index, int)
        or table_index <= 0
        or isinstance(position, bool)
        or not isinstance(position, int)
        or position <= 0
    ):
        raise ValueError("penalty_source_entry_locator_invalid")
    doc_id = locator.get("nfra_doc_id")
    if expected_nfra_doc_id is not None:
        if (
            set(locator) != LOCATOR_BASE_KEYS | {position_key}
            or not isinstance(doc_id, str)
            or not doc_id
            or doc_id != expected_nfra_doc_id
        ):
            raise ValueError("penalty_source_entry_locator_invalid")
    elif "nfra_doc_id" in locator:
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError("penalty_source_entry_locator_invalid")
    return {
        **({"nfra_doc_id": doc_id} if isinstance(doc_id, str) else {}),
        "table_index": table_index,
        position_key: position,
    }
