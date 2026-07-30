"""support multiple penalty records per source document

Revision ID: b9c8d7e6f5a4
Revises: a8b7c6d5e4f3
Create Date: 2026-07-29
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "b9c8d7e6f5a4"
down_revision: str | None = "a8b7c6d5e4f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_IDENTITY_VERSION = "legacy_source_quote_v1"
LEGACY_IDENTITY_STATUS = "reimport_required"
LEGACY_BACKFILL_ERROR = "cannot_backfill_legacy_penalty_identity_marker"


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _legacy_fragments(source_quote: str, raw_text: str | None = None) -> list[dict[str, Any]]:
    start = (raw_text or "").find(source_quote)
    if start < 0:
        start = 0
    return [
        {
            "quote": source_quote,
            "start_offset": start,
            "end_offset": start + len(source_quote),
        }
    ]


def _legacy_fingerprint(
    raw_sha256: str,
    source_quote: str,
    raw_text: str | None = None,
) -> str:
    fragments = _legacy_fragments(source_quote, raw_text)
    content_sha256 = hashlib.sha256(_canonical_json_bytes(fragments)).hexdigest()
    identity = {
        "raw_artifact_sha256": raw_sha256,
        "source_entry_content_sha256": content_sha256,
    }
    return hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(LEGACY_BACKFILL_ERROR) from exc
    if not isinstance(value, dict):
        raise RuntimeError(LEGACY_BACKFILL_ERROR)
    return dict(value)


def _mark_legacy_penalty_provenance(
    metadata_value: Any,
    penalty_id: int,
) -> dict[str, Any]:
    metadata = _metadata_dict(metadata_value)
    raw_provenance = metadata.get("structured_draft_provenance")
    if not isinstance(raw_provenance, list):
        raise RuntimeError(LEGACY_BACKFILL_ERROR)
    matches = [
        index
        for index, item in enumerate(raw_provenance)
        if isinstance(item, dict)
        and item.get("structured_record_id") == penalty_id
        and item.get("record_type") == "penalty"
    ]
    if len(matches) != 1:
        raise RuntimeError(LEGACY_BACKFILL_ERROR)
    marked_provenance = [
        dict(item) if isinstance(item, dict) else item for item in raw_provenance
    ]
    marked_provenance[matches[0]].update(
        {
            "identity_version": LEGACY_IDENTITY_VERSION,
            "source_identity_status": LEGACY_IDENTITY_STATUS,
        }
    )
    metadata["structured_draft_provenance"] = marked_provenance
    return metadata


def upgrade() -> None:
    op.add_column("penalties", sa.Column("source_entry_index", sa.Integer(), nullable=True))
    op.add_column(
        "penalties",
        sa.Column("source_entry_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "penalties",
        sa.Column(
            "duplicate_candidate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                """
                SELECT penalties.id, source_documents.id AS document_id,
                       source_documents.sha256, source_documents.raw_text,
                       source_documents.metadata_json, penalties.source_quote
                FROM penalties
                JOIN source_documents ON source_documents.id = penalties.document_id
                ORDER BY penalties.id
                """
            )
        ).mappings()
    )
    backfills = [
        {
            "penalty_id": row["id"],
            "document_id": row["document_id"],
            "fingerprint": _legacy_fingerprint(
                row["sha256"],
                row["source_quote"],
                row["raw_text"],
            ),
            "metadata_json": _mark_legacy_penalty_provenance(
                row["metadata_json"],
                row["id"],
            ),
        }
        for row in rows
    ]
    penalty_update = sa.text(
        """
        UPDATE penalties
        SET source_entry_index = 1,
            source_entry_fingerprint = :fingerprint
        WHERE id = :penalty_id
        """
    )
    metadata_update = sa.text(
        """
        UPDATE source_documents
        SET metadata_json = :metadata_json
        WHERE id = :document_id
        """
    ).bindparams(sa.bindparam("metadata_json", type_=sa.JSON()))
    for backfill in backfills:
        connection.execute(penalty_update, backfill)
        connection.execute(metadata_update, backfill)
    with op.batch_alter_table("penalties") as batch:
        batch.drop_constraint("uq_penalties_document_id", type_="unique")
        batch.alter_column("source_entry_index", nullable=False)
        batch.alter_column("source_entry_fingerprint", nullable=False)
        batch.create_check_constraint(
            "ck_penalties_source_entry_index_positive",
            "source_entry_index > 0",
        )
        batch.create_check_constraint(
            "ck_penalties_source_entry_fingerprint_length",
            "length(source_entry_fingerprint) = 64",
        )
        batch.create_unique_constraint(
            "uq_penalties_document_entry_index",
            ["document_id", "source_entry_index"],
        )
        batch.create_unique_constraint(
            "uq_penalties_document_entry_fingerprint",
            ["document_id", "source_entry_fingerprint"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    multi_record_document = connection.execute(
        sa.text(
            """
            SELECT document_id
            FROM penalties
            GROUP BY document_id
            HAVING count(*) > 1
            ORDER BY document_id
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if multi_record_document is not None:
        raise RuntimeError(
            "cannot_downgrade_multi_record_penalties: "
            "at least one source document has multiple penalty records"
        )
    with op.batch_alter_table("penalties") as batch:
        batch.drop_constraint(
            "uq_penalties_document_entry_fingerprint",
            type_="unique",
        )
        batch.drop_constraint(
            "uq_penalties_document_entry_index",
            type_="unique",
        )
        batch.drop_constraint(
            "ck_penalties_source_entry_fingerprint_length",
            type_="check",
        )
        batch.drop_constraint(
            "ck_penalties_source_entry_index_positive",
            type_="check",
        )
        batch.drop_column("source_entry_fingerprint")
        batch.drop_column("source_entry_index")
        batch.drop_column("duplicate_candidate")
        batch.create_unique_constraint(
            "uq_penalties_document_id",
            ["document_id"],
        )
