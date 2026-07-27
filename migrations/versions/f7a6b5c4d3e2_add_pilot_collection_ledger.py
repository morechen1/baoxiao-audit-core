"""add immutable pilot source and collection ledger

Revision ID: f7a6b5c4d3e2
Revises: e6f5a4b3c2d1
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a6b5c4d3e2"
down_revision: str | None = "e6f5a4b3c2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pilot_source_registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "data_source_id",
            sa.Integer(),
            sa.ForeignKey("data_sources.id", ondelete="RESTRICT"),
            nullable=True,
            unique=True,
        ),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("entry_sha256", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("publisher", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("allowed_domains_json", sa.JSON(), nullable=False),
        sa.Column("allow_subdomains", sa.Boolean(), nullable=False),
        sa.Column("rate_limit_seconds", sa.Float(), nullable=False),
        sa.Column("max_documents", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.String(255), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_reference", sa.String(500), nullable=False),
        sa.Column("robots_review_status", sa.String(50), nullable=False),
        sa.Column("robots_checked_at", sa.Date(), nullable=False),
        sa.Column("robots_checked_by", sa.String(255), nullable=False),
        sa.Column("robots_reference_url", sa.String(2048), nullable=True),
        sa.Column("robots_notes", sa.Text(), nullable=False),
        sa.Column("terms_review_status", sa.String(50), nullable=False),
        sa.Column("terms_checked_at", sa.Date(), nullable=False),
        sa.Column("terms_checked_by", sa.String(255), nullable=False),
        sa.Column("terms_reference_url", sa.String(2048), nullable=True),
        sa.Column("terms_notes", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("last_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "source_key",
            "entry_sha256",
            name="uq_pilot_source_registrations_key_hash",
        ),
        sa.UniqueConstraint(
            "source_key",
            "version",
            name="uq_pilot_source_registrations_key_version",
        ),
        sa.CheckConstraint(
            "length(entry_sha256) = 64",
            name="ck_pilot_source_registrations_hash",
        ),
        sa.CheckConstraint(
            "source_type IN ('regulation', 'penalty', 'regulatory_case', 'product_document')",
            name="ck_pilot_source_registrations_type",
        ),
        sa.CheckConstraint(
            "robots_review_status IN ('allowed', 'not_published_manual_review', 'prohibited')",
            name="ck_pilot_source_registrations_robots_status",
        ),
        sa.CheckConstraint(
            "terms_review_status IN ('public_access_allowed', "
            "'not_published_manual_review', 'prohibited')",
            name="ck_pilot_source_registrations_terms_status",
        ),
    )
    op.create_index(
        "ix_pilot_source_registrations_source_key",
        "pilot_source_registrations",
        ["source_key"],
    )
    op.create_index(
        "ix_pilot_source_registrations_entry_sha256",
        "pilot_source_registrations",
        ["entry_sha256"],
    )

    op.create_table(
        "pilot_collection_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_uuid", sa.String(36), nullable=False),
        sa.Column("selected_manifest", sa.String(255), nullable=False),
        sa.Column("manifest_set_sha256", sa.String(64), nullable=False),
        sa.Column("source_registry_set_sha256", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("planned_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_uuid", name="uq_pilot_collection_runs_uuid"),
        sa.CheckConstraint(
            "status IN ('validated', 'running', 'completed', "
            "'completed_with_errors', 'failed_configuration', 'cancelled')",
            name="ck_pilot_collection_runs_status",
        ),
        sa.CheckConstraint(
            "length(manifest_set_sha256) = 64",
            name="ck_pilot_collection_runs_manifest_hash",
        ),
        sa.CheckConstraint(
            "length(source_registry_set_sha256) = 64",
            name="ck_pilot_collection_runs_registry_hash",
        ),
    )

    op.create_table(
        "pilot_collection_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("pilot_collection_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_registration_id",
            sa.Integer(),
            sa.ForeignKey("pilot_source_registrations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("pilot_id", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(4096), nullable=False),
        sa.Column("expected_title", sa.String(1000), nullable=True),
        sa.Column("evaluation_usage_json", sa.JSON(), nullable=False),
        sa.Column("confirmed_by", sa.String(255), nullable=False),
        sa.Column("approval_reference", sa.String(500), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_entry_sha256", sa.String(64), nullable=False),
        sa.Column("source_registry_entry_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "occurrence_id",
            sa.Integer(),
            sa.ForeignKey("document_occurrences.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("document_created", sa.Boolean(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(120), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pilot_id", name="uq_pilot_collection_items_pilot_id"),
        sa.CheckConstraint(
            "length(manifest_entry_sha256) = 64",
            name="ck_pilot_collection_items_manifest_hash",
        ),
        sa.CheckConstraint(
            "length(source_registry_entry_sha256) = 64",
            name="ck_pilot_collection_items_registry_hash",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'attempting', 'collected', 'failed', 'blocked_not_implemented')",
            name="ck_pilot_collection_items_status",
        ),
    )
    op.create_index("ix_pilot_collection_items_run_id", "pilot_collection_items", ["run_id"])
    op.create_index(
        "ix_pilot_collection_items_source_registration_id",
        "pilot_collection_items",
        ["source_registration_id"],
    )
    op.create_index(
        "ix_pilot_collection_items_pilot_id",
        "pilot_collection_items",
        ["pilot_id"],
    )
    op.create_index(
        "ix_pilot_collection_items_document_id",
        "pilot_collection_items",
        ["document_id"],
    )
    op.create_index(
        "ix_pilot_collection_items_occurrence_id",
        "pilot_collection_items",
        ["occurrence_id"],
    )


def downgrade() -> None:
    op.drop_table("pilot_collection_items")
    op.drop_table("pilot_collection_runs")
    op.drop_table("pilot_source_registrations")
