from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.review_payload import canonical_json_bytes

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
FRAGMENT_KEYS = frozenset({"quote", "start_offset", "end_offset"})
LOCATOR_BASE_KEYS = frozenset({"nfra_doc_id", "table_index"})
LOCATOR_POSITION_KEYS = frozenset({"logical_row", "numbered_entry"})
PENALTY_IDENTITY_FIELDS = (
    "punished_entity",
    "penalty_result",
    "illegal_facts",
    "document_number",
)
IDENTITY_EVIDENCE_MODES = frozenset({"verbatim", "normalized"})


def canonical_source_entry_fragments(
    fragments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and canonicalize exact entry fragments for audit comparison."""
    if not isinstance(fragments, list) or not fragments:
        raise ValueError("penalty_source_entry_fragment_invalid")
    result: list[dict[str, Any]] = []
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
        ):
            raise ValueError("penalty_source_entry_fragment_invalid")
        result.append(
            {
                "quote": quote,
                "start_offset": start,
                "end_offset": end,
            }
        )
    if len(result) != len(
        {(item["quote"], item["start_offset"], item["end_offset"]) for item in result}
    ):
        raise ValueError("penalty_source_entry_fragment_invalid")
    return sorted(
        result,
        key=lambda item: (item["quote"], item["start_offset"], item["end_offset"]),
    )


def build_penalty_identity_material(
    validated_fields: dict[str, Any],
    validated_evidence: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, str]]]:
    """Derive identity only from fixed business fields and validated evidence."""
    punished_entity = validated_fields.get("punished_entity")
    if not isinstance(punished_entity, str) or not punished_entity.strip():
        raise ValueError("penalty_punished_entity_required")
    material: dict[str, list[dict[str, str]]] = {}
    for field_name in PENALTY_IDENTITY_FIELDS:
        value = validated_fields.get(field_name)
        if value is None or not str(value).strip():
            continue
        items = validated_evidence.get(field_name)
        if not isinstance(items, list) or not items:
            raise ValueError("penalty_source_entry_fragment_set_mismatch")
        quotes: set[str] = set()
        for item in items:
            if (
                not isinstance(item, dict)
                or item.get("mode") not in IDENTITY_EVIDENCE_MODES
                or not isinstance(item.get("quote"), str)
                or not item["quote"]
            ):
                raise ValueError("penalty_source_entry_fragment_set_mismatch")
            quotes.add(item["quote"])
        material[field_name] = [{"quote": quote} for quote in sorted(quotes)]
    return material


def build_penalty_source_entry_fragments(
    validated_fields: dict[str, Any],
    validated_evidence: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    build_penalty_identity_material(validated_fields, validated_evidence)
    fragments: dict[tuple[str, int, int], dict[str, Any]] = {}
    for field_name in PENALTY_IDENTITY_FIELDS:
        value = validated_fields.get(field_name)
        if value is None or not str(value).strip():
            continue
        for item in validated_evidence[field_name]:
            identity = (
                str(item["quote"]),
                int(item["start_offset"]),
                int(item["end_offset"]),
            )
            fragments[identity] = {
                "quote": identity[0],
                "start_offset": identity[1],
                "end_offset": identity[2],
            }
    return canonical_source_entry_fragments(list(fragments.values()))


def source_entry_content_sha256(
    identity_material: dict[str, list[dict[str, str]]],
) -> str:
    expected_keys = [
        field_name for field_name in PENALTY_IDENTITY_FIELDS if field_name in identity_material
    ]
    if list(identity_material) != expected_keys or not identity_material:
        raise ValueError("penalty_source_entry_fragment_set_mismatch")
    return hashlib.sha256(canonical_json_bytes(identity_material)).hexdigest()


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
        raise ValueError("penalty_source_entry_locator_invalid")
    return {
        **({"nfra_doc_id": doc_id} if isinstance(doc_id, str) else {}),
        "table_index": table_index,
        position_key: position,
    }
