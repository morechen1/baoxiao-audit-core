"""add immutable parsed evidence chain and review reservations

Revision ID: d5e4f3a2b1c0
Revises: c4d3e2f1a0b9
Create Date: 2026-07-27
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e4f3a2b1c0"
down_revision: str | None = "c4d3e2f1a0b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_documents") as batch:
        batch.add_column(sa.Column("parsed_artifact_path", sa.String(2048)))
        batch.add_column(sa.Column("parsed_artifact_sha256", sa.String(64)))
        batch.add_column(sa.Column("parsed_text_sha256", sa.String(64)))
        batch.add_column(sa.Column("parsed_from_raw_sha256", sa.String(64)))
        batch.add_column(sa.Column("parser_name", sa.String(255)))
        batch.add_column(sa.Column("parser_version", sa.String(32)))
        batch.add_column(sa.Column("parsed_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_source_documents_parsed_artifact_sha256",
            "parsed_artifact_sha256 IS NULL OR length(parsed_artifact_sha256) = 64",
        )
        batch.create_check_constraint(
            "ck_source_documents_parsed_text_sha256",
            "parsed_text_sha256 IS NULL OR length(parsed_text_sha256) = 64",
        )
        batch.create_check_constraint(
            "ck_source_documents_parsed_from_raw_sha256",
            "parsed_from_raw_sha256 IS NULL OR length(parsed_from_raw_sha256) = 64",
        )
    for table_name in ("regulations", "penalties", "product_documents"):
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(
                sa.Column(
                    "field_evidence_json",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
            batch.alter_column(
                "field_evidence_json",
                existing_type=sa.JSON(),
                nullable=False,
                server_default=None,
            )
    with op.batch_alter_table("review_batches") as batch:
        batch.drop_constraint("ck_review_batches_status", type_="check")
        batch.create_check_constraint(
            "ck_review_batches_status",
            "status IN ('exported', 'completed', 'cancelled')",
        )
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("cancellation_reason", sa.Text()))

    op.create_table(
        "parsed_artifact_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_path", sa.String(2048), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("from_raw_sha256", sa.String(64), nullable=False),
        sa.Column("parser_name", sa.String(255), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_parsed_artifact_versions_document_id",
        "parsed_artifact_versions",
        ["document_id"],
    )
    op.create_index(
        "ix_parsed_artifact_versions_artifact_sha256",
        "parsed_artifact_versions",
        ["artifact_sha256"],
    )
    op.create_table(
        "structured_draft_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column("structured_record_id", sa.Integer()),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("previous_fields_json", sa.JSON(), nullable=False),
        sa.Column("new_fields_json", sa.JSON(), nullable=False),
        sa.Column("previous_evidence_json", sa.JSON(), nullable=False),
        sa.Column("new_evidence_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_structured_draft_revisions_document_id",
        "structured_draft_revisions",
        ["document_id"],
    )
    op.create_table(
        "review_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("review_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_reason", sa.Text()),
        sa.CheckConstraint(
            "status IN ('active', 'released')",
            name="ck_review_reservations_status",
        ),
    )
    op.create_index(
        "ix_review_reservations_document_id",
        "review_reservations",
        ["document_id"],
    )
    op.create_index(
        "ix_review_reservations_batch_id",
        "review_reservations",
        ["batch_id"],
    )
    op.create_index(
        "uq_review_reservations_active_record",
        "review_reservations",
        ["record_type", "record_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )

    documents = sa.table(
        "source_documents",
        sa.column("id", sa.Integer()),
        sa.column("authenticity_type", sa.String()),
        sa.column("knowledge_index_status", sa.String()),
        sa.column("metadata_json", sa.JSON()),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(documents.c.id, documents.c.metadata_json).where(
            documents.c.authenticity_type == "verified_public"
        )
    ).mappings()
    for row in rows:
        metadata = row["metadata_json"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        updated = dict(metadata or {})
        updated["requires_evidence_backfill"] = True
        bind.execute(
            documents.update()
            .where(documents.c.id == row["id"])
            .values(
                knowledge_index_status="not_indexed",
                metadata_json=updated,
            )
        )


def downgrade() -> None:
    op.drop_index(
        "uq_review_reservations_active_record",
        table_name="review_reservations",
    )
    op.drop_index(
        "ix_review_reservations_batch_id",
        table_name="review_reservations",
    )
    op.drop_index(
        "ix_review_reservations_document_id",
        table_name="review_reservations",
    )
    op.drop_table("review_reservations")
    op.drop_index(
        "ix_structured_draft_revisions_document_id",
        table_name="structured_draft_revisions",
    )
    op.drop_table("structured_draft_revisions")
    op.drop_index(
        "ix_parsed_artifact_versions_artifact_sha256",
        table_name="parsed_artifact_versions",
    )
    op.drop_index(
        "ix_parsed_artifact_versions_document_id",
        table_name="parsed_artifact_versions",
    )
    op.drop_table("parsed_artifact_versions")
    with op.batch_alter_table("review_batches") as batch:
        batch.drop_column("cancellation_reason")
        batch.drop_column("cancelled_at")
        batch.drop_constraint("ck_review_batches_status", type_="check")
        batch.create_check_constraint(
            "ck_review_batches_status",
            "status IN ('exported', 'completed')",
        )
    for table_name in ("product_documents", "penalties", "regulations"):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("field_evidence_json")
    with op.batch_alter_table("source_documents") as batch:
        batch.drop_constraint(
            "ck_source_documents_parsed_from_raw_sha256",
            type_="check",
        )
        batch.drop_constraint(
            "ck_source_documents_parsed_text_sha256",
            type_="check",
        )
        batch.drop_constraint(
            "ck_source_documents_parsed_artifact_sha256",
            type_="check",
        )
        batch.drop_column("parsed_at")
        batch.drop_column("parser_version")
        batch.drop_column("parser_name")
        batch.drop_column("parsed_from_raw_sha256")
        batch.drop_column("parsed_text_sha256")
        batch.drop_column("parsed_artifact_sha256")
        batch.drop_column("parsed_artifact_path")
