"""add trusted knowledge chunks

Revision ID: c0d1e2f3a4b5
Revises: b9c8d7e6f5a4
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b9c8d7e6f5a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "knowledge_index_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("index_version", sa.String(length=80), nullable=False),
        sa.Column("source_document_count", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_knowledge_index_runs_status",
        ),
        sa.CheckConstraint(
            "payload_hash IS NULL OR length(payload_hash) = 64",
            name="ck_knowledge_index_runs_payload_hash",
        ),
        sa.UniqueConstraint("run_id", name="uq_knowledge_index_runs_run_id"),
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_type", sa.String(length=50), nullable=False),
        sa.Column("structured_record_id", sa.Integer(), nullable=True),
        sa.Column("pilot_id", sa.String(length=64), nullable=True),
        sa.Column("portable_record_key", sa.String(length=64), nullable=True),
        sa.Column("chunk_kind", sa.String(length=80), nullable=False),
        sa.Column("chunk_ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("lexical_tokens", sa.Text(), nullable=False),
        sa.Column("authority", sa.String(length=500), nullable=True),
        sa.Column("authority_filter_text", sa.String(length=500), nullable=True),
        sa.Column("relevant_date", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(length=4096), nullable=False),
        sa.Column("source_locator_json", sa.JSON(), nullable=False),
        sa.Column("evidence_reference_json", sa.JSON(), nullable=False),
        sa.Column("evidence_quality", sa.String(length=1), nullable=True),
        sa.Column("authenticity_status", sa.String(length=50), nullable=False),
        sa.Column("review_status", sa.String(length=50), nullable=False),
        sa.Column("source_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("chunk_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunk_identity_sha256", sa.String(length=64), nullable=False),
        sa.Column("tokenizer_version", sa.String(length=80), nullable=False),
        sa.Column("chunker_version", sa.String(length=80), nullable=False),
        sa.Column("ranking_version", sa.String(length=80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("chunk_ordinal >= 0", name="ck_knowledge_chunks_ordinal"),
        sa.CheckConstraint(
            "length(source_payload_hash) = 64",
            name="ck_knowledge_chunks_source_payload_hash",
        ),
        sa.CheckConstraint(
            "length(chunk_content_sha256) = 64",
            name="ck_knowledge_chunks_content_sha256",
        ),
        sa.CheckConstraint(
            "length(chunk_identity_sha256) = 64",
            name="ck_knowledge_chunks_identity_sha256",
        ),
        sa.CheckConstraint(
            "evidence_quality IS NULL OR evidence_quality IN ('A', 'B', 'C', 'D')",
            name="ck_knowledge_chunks_evidence_quality",
        ),
        sa.UniqueConstraint(
            "chunk_identity_sha256",
            name="uq_knowledge_chunks_identity_sha256",
        ),
    )
    for name, columns in (
        ("ix_knowledge_chunks_source_document_id", ["source_document_id"]),
        ("ix_knowledge_chunks_record_type", ["record_type"]),
        ("ix_knowledge_chunks_pilot_id", ["pilot_id"]),
        ("ix_knowledge_chunks_is_active", ["is_active"]),
        ("ix_knowledge_chunks_evidence_quality", ["evidence_quality"]),
        ("ix_knowledge_chunks_authority", ["authority"]),
        ("ix_knowledge_chunks_relevant_date", ["relevant_date"]),
        (
            "ix_knowledge_chunks_trust_state",
            ["authenticity_status", "review_status"],
        ),
        (
            "ix_knowledge_chunks_record_locator",
            ["structured_record_id", "portable_record_key"],
        ),
        ("ix_knowledge_chunks_structured_record_id", ["structured_record_id"]),
        ("ix_knowledge_chunks_portable_record_key", ["portable_record_key"]),
    ):
        op.create_index(name, "knowledge_chunks", columns)

    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_lexical_tokens_fts "
            "ON knowledge_chunks USING gin (to_tsvector('simple', lexical_tokens))"
        )
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_normalized_text_trgm "
            "ON knowledge_chunks USING gin (normalized_text gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_knowledge_chunks_authority_filter_text_trgm "
            "ON knowledge_chunks USING gin (authority_filter_text gin_trgm_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_authority_filter_text_trgm")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_normalized_text_trgm")
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_lexical_tokens_fts")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_index_runs")
