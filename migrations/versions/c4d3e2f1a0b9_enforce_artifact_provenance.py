"""enforce artifact provenance and align foreign keys

Revision ID: c4d3e2f1a0b9
Revises: 9b8e7c6d5a4f
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d3e2f1a0b9"
down_revision: str | None = "9b8e7c6d5a4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENT_TYPES = "'regulation', 'penalty', 'product_document'"
FK_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _replace_foreign_key(
    table: str,
    column: str,
    referred_table: str,
    *,
    ondelete: str | None,
) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        reflected_name = f"fk_{table}_{column}_{referred_table}"
        with op.batch_alter_table(table, naming_convention=FK_NAMING) as batch:
            batch.drop_constraint(reflected_name, type_="foreignkey")
            batch.create_foreign_key(
                reflected_name,
                referred_table,
                [column],
                ["id"],
                ondelete=ondelete,
            )
        return
    constraint_name = f"{table}_{column}_fkey"
    op.drop_constraint(constraint_name, table, type_="foreignkey")
    op.create_foreign_key(
        constraint_name,
        table,
        referred_table,
        [column],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    _replace_foreign_key("source_documents", "source_id", "data_sources", ondelete="SET NULL")
    for table in (
        "document_chunks",
        "regulations",
        "penalties",
        "product_documents",
    ):
        _replace_foreign_key(table, "document_id", "source_documents", ondelete="CASCADE")
    _replace_foreign_key("review_decisions", "batch_id", "review_batches", ondelete="RESTRICT")

    with op.batch_alter_table("data_sources") as batch:
        batch.create_check_constraint(
            "ck_data_sources_document_type",
            f"source_type IN ({DOCUMENT_TYPES})",
        )
    with op.batch_alter_table("source_documents") as batch:
        batch.create_check_constraint(
            "ck_source_documents_data_type",
            f"data_type IN ({DOCUMENT_TYPES})",
        )

    with op.batch_alter_table("review_batches") as batch:
        batch.add_column(sa.Column("bundle_path", sa.String(2048)))
        batch.add_column(sa.Column("bundle_sha256", sa.String(64)))
        batch.add_column(sa.Column("bundle_manifest_sha256", sa.String(64)))
        batch.create_check_constraint(
            "ck_review_batches_bundle_sha256",
            "bundle_sha256 IS NULL OR length(bundle_sha256) = 64",
        )
        batch.create_check_constraint(
            "ck_review_batches_bundle_manifest_sha256",
            "bundle_manifest_sha256 IS NULL OR length(bundle_manifest_sha256) = 64",
        )

    with op.batch_alter_table("authenticity_decision_logs") as batch:
        batch.add_column(sa.Column("source_id", sa.Integer()))
        batch.add_column(sa.Column("verified_occurrence_id", sa.Integer()))
    op.execute(
        sa.text(
            "UPDATE authenticity_decision_logs SET verified_occurrence_id = "
            "(SELECT id FROM document_occurrences "
            "WHERE document_occurrences.document_id = "
            "authenticity_decision_logs.document_id "
            "AND document_occurrences.source_id IS NOT NULL ORDER BY id LIMIT 1)"
        )
    )
    op.execute(
        sa.text(
            "UPDATE authenticity_decision_logs SET source_id = "
            "(SELECT source_id FROM document_occurrences "
            "WHERE document_occurrences.id = "
            "authenticity_decision_logs.verified_occurrence_id)"
        )
    )
    with op.batch_alter_table("authenticity_decision_logs") as batch:
        batch.alter_column("source_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("verified_occurrence_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_auth_decisions_source",
            "data_sources",
            ["source_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_auth_decisions_occurrence",
            "document_occurrences",
            ["verified_occurrence_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_auth_decisions_verified_occurrence",
            ["verified_occurrence_id"],
        )
        batch.create_index(
            "ix_authenticity_decision_logs_source_id",
            ["source_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("authenticity_decision_logs") as batch:
        batch.drop_index("ix_authenticity_decision_logs_source_id")
        batch.drop_constraint("uq_auth_decisions_verified_occurrence", type_="unique")
        batch.drop_constraint("fk_auth_decisions_occurrence", type_="foreignkey")
        batch.drop_constraint("fk_auth_decisions_source", type_="foreignkey")
        batch.drop_column("verified_occurrence_id")
        batch.drop_column("source_id")

    with op.batch_alter_table("review_batches") as batch:
        batch.drop_constraint("ck_review_batches_bundle_manifest_sha256", type_="check")
        batch.drop_constraint("ck_review_batches_bundle_sha256", type_="check")
        batch.drop_column("bundle_manifest_sha256")
        batch.drop_column("bundle_sha256")
        batch.drop_column("bundle_path")
    with op.batch_alter_table("source_documents") as batch:
        batch.drop_constraint("ck_source_documents_data_type", type_="check")
    with op.batch_alter_table("data_sources") as batch:
        batch.drop_constraint("ck_data_sources_document_type", type_="check")

    _replace_foreign_key("review_decisions", "batch_id", "review_batches", ondelete=None)
    for table in (
        "product_documents",
        "penalties",
        "regulations",
        "document_chunks",
    ):
        _replace_foreign_key(table, "document_id", "source_documents", ondelete=None)
    _replace_foreign_key("source_documents", "source_id", "data_sources", ondelete=None)
