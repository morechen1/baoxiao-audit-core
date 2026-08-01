"""add controlled explanation orchestration

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "explanation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "screening_run_id",
            sa.Integer(),
            sa.ForeignKey("screening_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience", sa.String(20), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("context_schema_version", sa.String(80), nullable=False),
        sa.Column("context_payload_sha256", sa.String(64), nullable=False),
        sa.Column("context_payload_json", sa.JSON(), nullable=False),
        sa.Column("provider_name", sa.String(80), nullable=False),
        sa.Column("provider_model", sa.String(120), nullable=False),
        sa.Column("provider_configuration_sha256", sa.String(64), nullable=False),
        sa.Column("provider_configuration_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("validation_status", sa.String(50), nullable=False),
        sa.Column("error_code", sa.String(120)),
        sa.Column(
            "retry_of_id",
            sa.Integer(),
            sa.ForeignKey("explanation_runs.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "audience IN ('institution', 'consumer')", name="ck_explanation_runs_audience"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'rejected', 'failed')",
            name="ck_explanation_runs_status",
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'passed', 'rejected_invalid_schema', "
            "'rejected_invalid_citation', 'rejected_unsupported_claim', "
            "'rejected_policy_violation')",
            name="ck_explanation_runs_validation_status",
        ),
        sa.CheckConstraint("length(prompt_sha256) = 64", name="ck_explanation_runs_prompt_sha"),
        sa.CheckConstraint(
            "length(context_payload_sha256) = 64", name="ck_explanation_runs_context_sha"
        ),
        sa.CheckConstraint(
            "length(provider_configuration_sha256) = 64",
            name="ck_explanation_runs_provider_config_sha",
        ),
    )
    op.create_index(
        "ix_explanation_runs_screening_run_id", "explanation_runs", ["screening_run_id"]
    )
    op.create_index("ix_explanation_runs_audience", "explanation_runs", ["audience"])
    op.create_index("ix_explanation_runs_status", "explanation_runs", ["status"])
    op.create_index("ix_explanation_runs_retry_of_id", "explanation_runs", ["retry_of_id"])

    op.create_table(
        "explanation_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "explanation_run_id",
            sa.Integer(),
            sa.ForeignKey("explanation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("output_schema_version", sa.String(80), nullable=False),
        sa.Column("raw_provider_response_sha256", sa.String(64), nullable=False),
        sa.Column("validated_output_json", sa.JSON(), nullable=False),
        sa.Column("resolved_citations_json", sa.JSON(), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(raw_provider_response_sha256) = 64",
            name="ck_explanation_artifacts_raw_response_sha",
        ),
        sa.CheckConstraint(
            "length(artifact_sha256) = 64", name="ck_explanation_artifacts_artifact_sha"
        ),
        sa.UniqueConstraint("explanation_run_id", name="uq_explanation_artifacts_run"),
    )
    op.create_index(
        "ix_explanation_artifacts_explanation_run_id",
        "explanation_artifacts",
        ["explanation_run_id"],
    )
    op.create_index(
        "ix_explanation_artifacts_artifact_sha256", "explanation_artifacts", ["artifact_sha256"]
    )

    op.create_table(
        "explanation_citations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "explanation_artifact_id",
            sa.Integer(),
            sa.ForeignKey("explanation_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("citation_key", sa.String(20), nullable=False),
        sa.Column("finding_key", sa.String(20), nullable=False),
        sa.Column(
            "finding_id",
            sa.Integer(),
            sa.ForeignKey("risk_findings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "finding_evidence_link_id",
            sa.Integer(),
            sa.ForeignKey("finding_evidence_links.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("chunk_identity_sha256", sa.String(64), nullable=False),
        sa.Column("chunk_content_sha256", sa.String(64), nullable=False),
        sa.Column("support_type", sa.String(40), nullable=False),
        sa.Column("source_title", sa.String(1000), nullable=False),
        sa.Column("source_url", sa.String(4096), nullable=False),
        sa.Column("pilot_id", sa.String(64)),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column("chunk_kind", sa.String(80), nullable=False),
        sa.Column("source_locator_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("context_scope", sa.String(80), nullable=False),
        sa.Column("evidence_field_name", sa.String(80), nullable=False),
        sa.Column("cited_quote", sa.Text(), nullable=False),
        sa.Column("quote_start_offset", sa.Integer()),
        sa.Column("quote_end_offset", sa.Integer()),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(chunk_identity_sha256) = 64",
            name="ck_explanation_citations_identity_sha",
        ),
        sa.CheckConstraint(
            "length(chunk_content_sha256) = 64",
            name="ck_explanation_citations_content_sha",
        ),
        sa.CheckConstraint(
            "quote_start_offset IS NULL OR quote_start_offset >= 0",
            name="ck_explanation_citations_quote_start",
        ),
        sa.CheckConstraint(
            "quote_end_offset IS NULL OR quote_end_offset > quote_start_offset",
            name="ck_explanation_citations_quote_end",
        ),
        sa.UniqueConstraint(
            "explanation_artifact_id", "citation_key", name="uq_explanation_citations_key"
        ),
    )
    op.create_index(
        "ix_explanation_citations_explanation_artifact_id",
        "explanation_citations",
        ["explanation_artifact_id"],
    )
    op.create_index("ix_explanation_citations_finding_id", "explanation_citations", ["finding_id"])
    op.create_index(
        "ix_explanation_citations_finding_evidence_link_id",
        "explanation_citations",
        ["finding_evidence_link_id"],
    )


def downgrade() -> None:
    op.drop_table("explanation_citations")
    op.drop_table("explanation_artifacts")
    op.drop_table("explanation_runs")
