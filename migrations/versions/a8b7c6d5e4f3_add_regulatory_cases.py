"""add regulatory cases and extend document types

Revision ID: a8b7c6d5e4f3
Revises: f7a6b5c4d3e2
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b7c6d5e4f3"
down_revision: str | None = "f7a6b5c4d3e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_DOCUMENT_TYPES = "'regulation', 'penalty', 'product_document'"
NEW_DOCUMENT_TYPES = "'regulation', 'penalty', 'product_document', 'regulatory_case'"
CASE_CATEGORIES = (
    "'regulatory_typical_case', 'consumer_risk_alert', 'case_based_education', "
    "'consumer_dispute_case', 'judicial_case'"
)
CASE_USAGES = "'retrieval_only', 'external_test_candidate', 'sealed_external_test'"
REVIEW_STATUSES = (
    "'collected', 'parsed', 'auto_validation_failed', 'pending_review', 'approved', "
    "'approved_with_revision', 'rejected', 'pending_source_verification', "
    "'rejected_hallucination', 'rejected_duplicate', 'rejected_outdated', "
    "'requires_expert_review'"
)


def _replace_document_type_constraints(values: str) -> None:
    with op.batch_alter_table("data_sources") as batch:
        batch.drop_constraint("ck_data_sources_document_type", type_="check")
        batch.create_check_constraint(
            "ck_data_sources_document_type",
            f"source_type IN ({values})",
        )
    with op.batch_alter_table("source_documents") as batch:
        batch.drop_constraint("ck_source_documents_data_type", type_="check")
        batch.create_check_constraint(
            "ck_source_documents_data_type",
            f"data_type IN ({values})",
        )


def upgrade() -> None:
    _replace_document_type_constraints(NEW_DOCUMENT_TYPES)
    op.create_table(
        "regulatory_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_title", sa.String(1000), nullable=False),
        sa.Column("publisher", sa.String(500), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("case_category", sa.String(50), nullable=False),
        sa.Column("scenario_text", sa.Text(), nullable=False),
        sa.Column("marketing_wording_disclosed", sa.Boolean(), nullable=False),
        sa.Column("marketing_wording", sa.Text(), nullable=True),
        sa.Column("case_facts", sa.Text(), nullable=True),
        sa.Column("regulatory_analysis", sa.Text(), nullable=True),
        sa.Column("consumer_advice", sa.Text(), nullable=True),
        sa.Column(
            "case_usage",
            sa.String(50),
            nullable=False,
            server_default="external_test_candidate",
        ),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("field_evidence_json", sa.JSON(), nullable=False),
        sa.Column("final_review_status", sa.String(50), nullable=False),
        sa.Column("evidence_quality", sa.String(1), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", name="uq_regulatory_cases_document_id"),
        sa.CheckConstraint(
            f"case_category IN ({CASE_CATEGORIES})",
            name="ck_regulatory_cases_category",
        ),
        sa.CheckConstraint(
            f"case_usage IN ({CASE_USAGES})",
            name="ck_regulatory_cases_usage",
        ),
        sa.CheckConstraint(
            "(marketing_wording_disclosed = true "
            "AND marketing_wording IS NOT NULL "
            "AND length(trim(marketing_wording)) > 0) "
            "OR (marketing_wording_disclosed = false "
            "AND marketing_wording IS NULL)",
            name="ck_regulatory_cases_marketing_wording",
        ),
        sa.CheckConstraint(
            f"final_review_status IN ({REVIEW_STATUSES})",
            name="ck_regulatory_cases_review_status",
        ),
        sa.CheckConstraint(
            "evidence_quality IS NULL OR evidence_quality IN ('A', 'B', 'C', 'D')",
            name="ck_regulatory_cases_evidence_quality",
        ),
    )
    op.create_index(
        "ix_regulatory_cases_document_id",
        "regulatory_cases",
        ["document_id"],
    )
    op.create_index(
        "ix_regulatory_cases_case_category",
        "regulatory_cases",
        ["case_category"],
    )
    op.create_index(
        "ix_regulatory_cases_case_usage",
        "regulatory_cases",
        ["case_usage"],
    )
    op.create_index(
        "ix_regulatory_cases_published_at",
        "regulatory_cases",
        ["published_at"],
    )
    op.create_index(
        "ix_regulatory_cases_final_review_status",
        "regulatory_cases",
        ["final_review_status"],
    )


def downgrade() -> None:
    op.drop_table("regulatory_cases")
    _replace_document_type_constraints(OLD_DOCUMENT_TYPES)
