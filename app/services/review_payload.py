from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

REVIEW_PAYLOAD_SCHEMA_VERSION = 2

_VOLATILE_RECORD_FIELDS = frozenset(
    {
        "structured_record_id",
        "id",
        "document_id",
        "portable_record_key",
        "final_review_status",
        "knowledge_index_status",
        "indexed_at",
        "created_at",
        "updated_at",
        "field_evidence",
        "field_evidence_json",
        "draft_provenance",
    }
)
_STABLE_EVIDENCE_FIELDS = frozenset(
    {
        "quote",
        "page_number",
        "start_offset",
        "end_offset",
        "mode",
        "transformation_note",
        "metadata_field",
    }
)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_review_payload_hash_v2(review_row: dict[str, Any]) -> str:
    canonical = canonical_json_bytes(build_canonical_review_payload_v2(review_row))
    return hashlib.sha256(canonical).hexdigest()


def canonical_portable_source_url(value: Any) -> Any:
    if not isinstance(value, str) or not value.startswith("file://"):
        return value
    parsed = urlparse(value)
    filename = parsed.path.rsplit("/", 1)[-1]
    if filename in {"", ".", ".."}:
        raise ValueError("portable_source_filename_missing")
    return f"local-unattributed://{filename}"


def portable_record_key(
    raw_artifact_sha256: str,
    record_type: str,
    record: dict[str, Any],
) -> str:
    content = _canonical_record_content(record)
    identity = {
        "raw_artifact_sha256": raw_artifact_sha256,
        "record_type": record_type,
        "draft_provenance": content["draft_provenance"],
        "business_fields": content["business_fields"],
        "field_evidence": content["field_evidence"],
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def build_canonical_review_payload_v2(review_row: dict[str, Any]) -> dict[str, Any]:
    if review_row.get("review_payload_schema_version") != REVIEW_PAYLOAD_SCHEMA_VERSION:
        raise ValueError("review_payload_schema_unsupported")
    record_type = str(review_row.get("record_type") or "")
    raw_sha256 = str(review_row.get("sha256") or "")
    raw_records = review_row.get("parsed_fields", {}).get("records", [])
    records: list[dict[str, Any]] = []
    if isinstance(raw_records, list):
        for raw_record in raw_records:
            if not isinstance(raw_record, dict):
                raise ValueError("review_payload_invalid")
            content = _canonical_record_content(raw_record)
            key = portable_record_key(raw_sha256, record_type, raw_record)
            records.append({"portable_record_key": key, **content})
    records.sort(
        key=lambda value: (
            (value["business_fields"].get("source_entry_index") if record_type == "penalty" else 0),
            value["portable_record_key"],
        )
    )

    occurrences = [
        {
            "source_url": canonical_portable_source_url(item.get("source_url")),
            "final_url": canonical_portable_source_url(item.get("final_url")),
            "publisher": item.get("publisher"),
            "http_status": item.get("http_status"),
        }
        for item in review_row.get("source_occurrences", [])
        if isinstance(item, dict)
    ]
    occurrences.sort(key=canonical_json_bytes)
    pilot_ids = sorted(
        {
            str(value)
            for value in review_row.get("pilot_ids", [])
            if isinstance(value, str) and value
        }
    )
    index_rejection_reasons = sorted(
        {
            str(value)
            for value in review_row.get("index_rejection_reasons", [])
            if isinstance(value, str) and value
        }
    )
    evaluation_fields: dict[str, Any] | None = None
    if record_type == "evaluation_sample":
        raw_fields = review_row.get("parsed_fields")
        evaluation_fields = {
            "sample_text": review_row.get("raw_text"),
            **(raw_fields if isinstance(raw_fields, dict) else {}),
        }

    return {
        "review_payload_schema_version": REVIEW_PAYLOAD_SCHEMA_VERSION,
        "record_type": record_type,
        "source": {
            "source_url": canonical_portable_source_url(review_row.get("source_url")),
            "retrieval_url": canonical_portable_source_url(review_row.get("final_url")),
            "source_title": review_row.get("source_title"),
            "publisher": review_row.get("publisher"),
            "published_at": review_row.get("published_at"),
            "raw_artifact_sha256": review_row.get("sha256"),
            "parsed_artifact_sha256": review_row.get("parsed_artifact_sha256"),
            "plain_text_sha256": review_row.get("parsed_text_sha256"),
            "parsed_from_raw_sha256": review_row.get("parsed_from_raw_sha256"),
        },
        "trust_state": {
            "authenticity_type": review_row.get("authenticity_type"),
            "final_review_status": review_row.get("current_status"),
            "knowledge_index_status": review_row.get("knowledge_index_status"),
            "can_index": review_row.get("can_index"),
            "index_rejection_reasons": index_rejection_reasons,
        },
        "pilot_ids": pilot_ids,
        "source_occurrences": occurrences,
        "structured_records": records,
        "evaluation_fields": evaluation_fields,
    }


def _canonical_record_content(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("field_evidence")
    if evidence is None:
        evidence = record.get("field_evidence_json")
    provenance = record.get("draft_provenance")
    return {
        "business_fields": {
            key: _canonical_value(value)
            for key, value in record.items()
            if key not in _VOLATILE_RECORD_FIELDS
        },
        "field_evidence": _canonical_evidence(evidence),
        "draft_provenance": _canonical_provenance(provenance),
    }


def _canonical_evidence(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for field_name, raw_items in value.items():
        if not isinstance(raw_items, list):
            continue
        items = [
            {
                key: _canonical_value(item[key])
                for key in sorted(_STABLE_EVIDENCE_FIELDS)
                if isinstance(item, dict) and key in item and item[key] is not None
            }
            for item in raw_items
            if isinstance(item, dict)
        ]
        result[str(field_name)] = sorted(items, key=canonical_json_bytes)
    return result


def _canonical_provenance(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        key: _canonical_value(value.get(key))
        for key in (
            "pilot_id",
            "draft_generation_method",
            "draft_generation_version",
        )
    }
    for key in (
        "source_entry_locator",
        "source_entry_fragments",
        "source_entry_content_sha256",
    ):
        if key in value:
            result[key] = _canonical_value(value.get(key))
    return result


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("review_payload_invalid")
    if isinstance(value, dict):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((_canonical_value(item) for item in value), key=canonical_json_bytes)
    return value
