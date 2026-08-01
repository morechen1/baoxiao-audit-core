"""add deterministic compliance screening

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "marketing_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_reference", sa.String(255)),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("material_type", sa.String(50), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("normalization_version", sa.String(80), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("source_label", sa.String(255), nullable=False),
        sa.Column("is_constructed_evaluation", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("material_type IN ('advertisement', 'sales_script', 'social_media', 'sms', 'product_introduction', 'other')", name="ck_marketing_materials_type"),
        sa.CheckConstraint("length(input_sha256) = 64", name="ck_marketing_materials_input_sha"),
        sa.UniqueConstraint("input_sha256", name="uq_marketing_materials_input_sha256"),
    )
    op.create_index("ix_marketing_materials_material_type", "marketing_materials", ["material_type"])
    op.create_index("ix_marketing_materials_input_sha256", "marketing_materials", ["input_sha256"])
    op.create_table(
        "material_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("marketing_materials.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("raw_start_offset", sa.Integer(), nullable=False),
        sa.Column("raw_end_offset", sa.Integer(), nullable=False),
        sa.Column("segment_sha256", sa.String(64), nullable=False),
        sa.Column("segmenter_version", sa.String(80), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_material_segments_ordinal"),
        sa.CheckConstraint("raw_start_offset >= 0", name="ck_material_segments_start"),
        sa.CheckConstraint("raw_end_offset > raw_start_offset", name="ck_material_segments_end"),
        sa.CheckConstraint("length(segment_sha256) = 64", name="ck_material_segments_sha"),
        sa.UniqueConstraint("material_id", "ordinal", name="uq_material_segments_ordinal"),
        sa.UniqueConstraint("segment_sha256", name="uq_material_segments_segment_sha256"),
    )
    op.create_index("ix_material_segments_material_id", "material_segments", ["material_id"])
    op.create_table(
        "screening_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("marketing_materials.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ruleset_version", sa.String(80), nullable=False),
        sa.Column("ruleset_sha256", sa.String(64), nullable=False),
        sa.Column("ruleset_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("ruleset_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("retrieval_version", sa.String(80), nullable=False),
        sa.Column("trusted_index_payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("insufficient_evidence_count", sa.Integer(), nullable=False),
        sa.Column("run_payload_sha256", sa.String(64)),
        sa.Column("error_code", sa.String(120)),
        sa.Column("evidence_evaluation_summary_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_screening_runs_status"),
        sa.CheckConstraint("length(ruleset_sha256) = 64", name="ck_screening_runs_ruleset_sha"),
        sa.CheckConstraint("length(ruleset_snapshot_sha256) = 64", name="ck_screening_runs_ruleset_snapshot_sha"),
        sa.CheckConstraint("length(trusted_index_payload_hash) = 64", name="ck_screening_runs_index_hash"),
        sa.CheckConstraint("run_payload_sha256 IS NULL OR length(run_payload_sha256) = 64", name="ck_screening_runs_payload_sha"),
    )
    op.create_index("ix_screening_runs_material_id", "screening_runs", ["material_id"])
    op.create_index("ix_screening_runs_status", "screening_runs", ["status"])
    op.create_table(
        "risk_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("screening_run_id", sa.Integer(), sa.ForeignKey("screening_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("material_segments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rule_id", sa.String(120), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("category", sa.String(120), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("signal_strength", sa.String(20), nullable=False),
        sa.Column("matched_text", sa.Text(), nullable=False),
        sa.Column("raw_start_offset", sa.Integer(), nullable=False),
        sa.Column("raw_end_offset", sa.Integer(), nullable=False),
        sa.Column("normalized_match", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("review_question", sa.Text(), nullable=False),
        sa.Column("remediation_template", sa.Text(), nullable=False),
        sa.Column("consumer_notice_template", sa.Text(), nullable=False),
        sa.Column("rule_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("rule_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_status", sa.String(40), nullable=False),
        sa.Column("finding_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("evidence_status IN ('supported', 'partially_supported', 'evidence_insufficient')", name="ck_risk_findings_evidence_status"),
        sa.CheckConstraint("raw_start_offset >= 0", name="ck_risk_findings_start"),
        sa.CheckConstraint("raw_end_offset > raw_start_offset", name="ck_risk_findings_end"),
        sa.CheckConstraint("length(finding_sha256) = 64", name="ck_risk_findings_sha"),
        sa.CheckConstraint("length(rule_snapshot_sha256) = 64", name="ck_risk_findings_rule_snapshot_sha"),
        sa.UniqueConstraint("screening_run_id", "finding_sha256", name="uq_risk_findings_run_sha"),
    )
    op.create_index("ix_risk_findings_screening_run_id", "risk_findings", ["screening_run_id"])
    op.create_index("ix_risk_findings_segment_id", "risk_findings", ["segment_id"])
    op.create_index("ix_risk_findings_rule_id", "risk_findings", ["rule_id"])
    op.create_table(
        "finding_evidence_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("risk_findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_chunk_id", sa.Integer(), sa.ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("support_type", sa.String(40), nullable=False),
        sa.Column("retrieval_rank", sa.Integer(), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=False),
        sa.Column("chunk_identity_sha256", sa.String(64), nullable=False),
        sa.Column("chunk_content_sha256", sa.String(64), nullable=False),
        sa.Column("source_document_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("source_locator_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("evidence_references_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("support_evaluation_version", sa.String(80), nullable=False),
        sa.Column("support_evaluation_passed", sa.Boolean(), nullable=False),
        sa.Column("matched_support_patterns", sa.JSON(), nullable=False),
        sa.Column("actual_matched_substrings", sa.JSON(), nullable=False),
        sa.Column("matched_pattern_groups", sa.JSON(), nullable=False),
        sa.Column("matched_evidence_fields", sa.JSON(), nullable=False),
        sa.Column("support_reason", sa.String(120), nullable=False),
        sa.Column("semantic_support_score", sa.Float(), nullable=False),
        sa.Column("semantic_support_reason", sa.String(120), nullable=False),
        sa.Column("context_scope", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("support_type IN ('normative_basis', 'enforcement_example', 'product_term_context')", name="ck_finding_evidence_links_support_type"),
        sa.CheckConstraint("length(chunk_identity_sha256) = 64", name="ck_finding_evidence_links_identity_sha"),
        sa.CheckConstraint("length(chunk_content_sha256) = 64", name="ck_finding_evidence_links_content_sha"),
        sa.UniqueConstraint("finding_id", "knowledge_chunk_id", name="uq_finding_evidence_links_chunk"),
    )
    op.create_index("ix_finding_evidence_links_finding_id", "finding_evidence_links", ["finding_id"])
    op.create_index("ix_finding_evidence_links_knowledge_chunk_id", "finding_evidence_links", ["knowledge_chunk_id"])


def downgrade() -> None:
    op.drop_table("finding_evidence_links")
    op.drop_table("risk_findings")
    op.drop_table("screening_runs")
    op.drop_table("material_segments")
    op.drop_table("marketing_materials")
