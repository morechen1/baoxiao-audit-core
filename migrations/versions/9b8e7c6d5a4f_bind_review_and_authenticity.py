"""bind review decisions and authenticity verification

Revision ID: 9b8e7c6d5a4f
Revises: 6e07cfd44bc2
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b8e7c6d5a4f"
down_revision: str | None = "6e07cfd44bc2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HUMAN_DECISIONS = (
    "'approved', 'approved_with_revision', 'rejected', "
    "'pending_source_verification', 'rejected_hallucination', "
    "'rejected_duplicate', 'rejected_outdated', 'requires_expert_review'"
)
AUTHENTICITY_VALUES = (
    "'verified_public', 'constructed_for_evaluation', 'demo_only', "
    "'pending_verification'"
)


def upgrade() -> None:
    with op.batch_alter_table("review_batches") as batch:
        batch.create_check_constraint(
            "ck_review_batches_export_sha256",
            "export_sha256 IS NULL OR length(export_sha256) = 64",
        )
    with op.batch_alter_table("review_batch_items") as batch:
        batch.create_check_constraint(
            "ck_review_batch_items_payload_hash",
            "length(payload_hash) = 64",
        )

    with op.batch_alter_table("review_decisions") as batch:
        batch.add_column(sa.Column("reviewed_payload_hash", sa.String(64)))
        batch.add_column(sa.Column("schema_version", sa.String(32)))
    op.execute(
        sa.text(
            "UPDATE review_decisions SET reviewed_payload_hash = "
            "(SELECT payload_hash FROM review_batch_items "
            "WHERE review_batch_items.decision_id = review_decisions.id), "
            "schema_version = (SELECT schema_version FROM review_batches "
            "WHERE review_batches.id = review_decisions.batch_id)"
        )
    )
    with op.batch_alter_table("review_decisions") as batch:
        batch.alter_column("batch_id", existing_type=sa.Integer(), nullable=False)
        batch.alter_column(
            "reviewed_payload_hash", existing_type=sa.String(64), nullable=False
        )
        batch.alter_column("schema_version", existing_type=sa.String(32), nullable=False)
        batch.drop_constraint("ck_review_decisions_decision", type_="check")
        batch.create_check_constraint(
            "ck_review_decisions_decision",
            f"decision IN ({HUMAN_DECISIONS})",
        )
        batch.create_check_constraint(
            "ck_review_decisions_payload_hash",
            "length(reviewed_payload_hash) = 64",
        )

    with op.batch_alter_table("evaluation_samples") as batch:
        batch.drop_constraint("ck_evaluation_samples_authenticity", type_="check")
        batch.create_check_constraint(
            "ck_evaluation_samples_authenticity",
            "authenticity_type = 'constructed_for_evaluation'",
        )
        batch.create_check_constraint(
            "ck_evaluation_samples_category",
            "sample_category IN ('compliant', 'boundary', 'risky', "
            "'adversarial', 'multi_risk')",
        )
        batch.create_check_constraint(
            "ck_evaluation_samples_split",
            "split IN ('train', 'validation', 'sealed_test')",
        )
        batch.create_check_constraint(
            "ck_evaluation_samples_text_nonempty",
            "length(trim(sample_text)) > 0",
        )
        batch.create_check_constraint(
            "ck_evaluation_samples_basis_nonempty",
            "length(trim(construction_basis)) > 0",
        )

    op.create_table(
        "authenticity_decision_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "previous_authenticity_type", sa.String(50), nullable=False
        ),
        sa.Column("new_authenticity_type", sa.String(50), nullable=False),
        sa.Column("reviewer", sa.String(255), nullable=False),
        sa.Column(
            "review_decision_id",
            sa.Integer(),
            sa.ForeignKey("review_decisions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"previous_authenticity_type IN ({AUTHENTICITY_VALUES})",
            name="ck_auth_decisions_previous_type",
        ),
        sa.CheckConstraint(
            "new_authenticity_type = 'verified_public'",
            name="ck_auth_decisions_new_type",
        ),
    )
    op.create_index(
        "ix_authenticity_decision_logs_document_id",
        "authenticity_decision_logs",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_authenticity_decision_logs_document_id",
        table_name="authenticity_decision_logs",
    )
    op.drop_table("authenticity_decision_logs")

    with op.batch_alter_table("evaluation_samples") as batch:
        batch.drop_constraint("ck_evaluation_samples_basis_nonempty", type_="check")
        batch.drop_constraint("ck_evaluation_samples_text_nonempty", type_="check")
        batch.drop_constraint("ck_evaluation_samples_split", type_="check")
        batch.drop_constraint("ck_evaluation_samples_category", type_="check")
        batch.drop_constraint("ck_evaluation_samples_authenticity", type_="check")
        batch.create_check_constraint(
            "ck_evaluation_samples_authenticity",
            f"authenticity_type IN ({AUTHENTICITY_VALUES})",
        )

    with op.batch_alter_table("review_decisions") as batch:
        batch.drop_constraint("ck_review_decisions_payload_hash", type_="check")
        batch.drop_constraint("ck_review_decisions_decision", type_="check")
        batch.create_check_constraint(
            "ck_review_decisions_decision",
            "decision IN ('collected', 'parsed', 'auto_validation_failed', "
            "'pending_review', 'approved', 'approved_with_revision', 'rejected', "
            "'pending_source_verification', 'rejected_hallucination', "
            "'rejected_duplicate', 'rejected_outdated', 'requires_expert_review')",
        )
        batch.alter_column("batch_id", existing_type=sa.Integer(), nullable=True)
        batch.drop_column("schema_version")
        batch.drop_column("reviewed_payload_hash")

    with op.batch_alter_table("review_batch_items") as batch:
        batch.drop_constraint("ck_review_batch_items_payload_hash", type_="check")
    with op.batch_alter_table("review_batches") as batch:
        batch.drop_constraint("ck_review_batches_export_sha256", type_="check")
