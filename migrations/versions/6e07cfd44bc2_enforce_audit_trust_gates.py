"""enforce audit trust gates

Revision ID: 6e07cfd44bc2
Revises: 2c05821ed624
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6e07cfd44bc2"
down_revision: str | None = "2c05821ed624"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVIEW_VALUES = (
    "'collected', 'parsed', 'auto_validation_failed', 'pending_review', "
    "'approved', 'approved_with_revision', 'rejected', "
    "'pending_source_verification', 'rejected_hallucination', "
    "'rejected_duplicate', 'rejected_outdated', 'requires_expert_review'"
)
AUTHENTICITY_VALUES = (
    "'verified_public', 'constructed_for_evaluation', 'demo_only', "
    "'pending_verification'"
)


def upgrade() -> None:
    # Repair the foundation version where indexing overwrote the human decision.
    op.execute(
        sa.text(
            "UPDATE source_documents SET final_review_status = COALESCE("
            "(SELECT sh.from_status FROM status_history AS sh "
            "WHERE sh.record_id = source_documents.id "
            "AND sh.record_type = source_documents.data_type "
            "AND sh.to_status = 'indexed' ORDER BY sh.id DESC LIMIT 1), "
            "'approved') WHERE final_review_status = 'indexed'"
        )
    )

    op.create_table(
        "document_occurrences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("data_sources.id", ondelete="SET NULL"),
        ),
        sa.Column("source_url", sa.String(4096)),
        sa.Column("final_url", sa.String(4096)),
        sa.Column("publisher", sa.String(255)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "source_url",
            "final_url",
            name="uq_document_occurrences_location",
        ),
    )
    op.create_index(
        "ix_document_occurrences_document_id",
        "document_occurrences",
        ["document_id"],
    )
    op.create_index(
        "ix_document_occurrences_source_id",
        "document_occurrences",
        ["source_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO document_occurrences "
            "(document_id, source_id, source_url, final_url, publisher, http_status, "
            "collected_at, response_metadata) "
            "SELECT id, source_id, source_url, final_url, publisher, http_status, "
            "collected_at, '{}' FROM source_documents"
        )
    )

    with op.batch_alter_table("review_batches") as batch:
        batch.add_column(sa.Column("export_sha256", sa.String(64)))
        batch.add_column(
            sa.Column(
                "schema_version",
                sa.String(32),
                nullable=False,
                server_default="2.0",
            )
        )
        batch.create_check_constraint(
            "ck_review_batches_status",
            "status IN ('exported', 'completed')",
        )

    op.create_table(
        "review_batch_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("review_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
        ),
        sa.Column("exported_status", sa.String(50), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "decision_id",
            sa.Integer(),
            sa.ForeignKey("review_decisions.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.CheckConstraint(
            f"exported_status IN ({REVIEW_VALUES})",
            name="ck_review_batch_items_exported_status",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "record_type",
            "record_id",
            name="uq_review_batch_items_record",
        ),
    )
    op.create_index(
        "ix_review_batch_items_batch_id", "review_batch_items", ["batch_id"]
    )
    op.create_index(
        "ix_review_batch_items_document_id", "review_batch_items", ["document_id"]
    )

    with op.batch_alter_table("source_documents") as batch:
        batch.create_check_constraint(
            "ck_source_documents_authenticity",
            f"authenticity_type IN ({AUTHENTICITY_VALUES})",
        )
        batch.create_check_constraint(
            "ck_source_documents_review_status",
            f"final_review_status IN ({REVIEW_VALUES})",
        )
        batch.create_check_constraint(
            "ck_source_documents_index_status",
            "knowledge_index_status IN ('not_indexed', 'indexed', 'index_failed')",
        )
        batch.create_check_constraint(
            "ck_source_documents_collection_status",
            "collection_status IN ('collected')",
        )
        batch.create_check_constraint(
            "ck_source_documents_parse_status",
            "parse_status IN ('pending', 'parsed', 'failed', 'requires_ocr')",
        )

    with op.batch_alter_table("regulations") as batch:
        batch.create_check_constraint(
            "ck_regulations_review_status",
            f"final_review_status IN ({REVIEW_VALUES})",
        )

    with op.batch_alter_table("penalties") as batch:
        batch.create_unique_constraint("uq_penalties_document_id", ["document_id"])
        batch.create_check_constraint(
            "ck_penalties_review_status",
            f"final_review_status IN ({REVIEW_VALUES})",
        )
        batch.create_check_constraint(
            "ck_penalties_original_wording",
            "(original_sales_wording_disclosed = true "
            "AND original_sales_wording IS NOT NULL "
            "AND length(trim(original_sales_wording)) > 0) "
            "OR (original_sales_wording_disclosed = false "
            "AND original_sales_wording IS NULL)",
        )

    with op.batch_alter_table("product_documents") as batch:
        batch.create_unique_constraint(
            "uq_product_documents_document_id", ["document_id"]
        )
        batch.create_check_constraint(
            "ck_product_documents_review_status",
            f"final_review_status IN ({REVIEW_VALUES})",
        )

    with op.batch_alter_table("evaluation_samples") as batch:
        batch.create_check_constraint(
            "ck_evaluation_samples_authenticity",
            f"authenticity_type IN ({AUTHENTICITY_VALUES})",
        )
        batch.create_check_constraint(
            "ck_evaluation_samples_review_status",
            f"final_review_status IN ({REVIEW_VALUES})",
        )

    with op.batch_alter_table("review_decisions") as batch:
        batch.create_check_constraint(
            "ck_review_decisions_evidence_quality",
            "evidence_quality IN ('A', 'B', 'C', 'D')",
        )
        batch.create_check_constraint(
            "ck_review_decisions_decision",
            f"decision IN ({REVIEW_VALUES})",
        )


def downgrade() -> None:
    with op.batch_alter_table("review_decisions") as batch:
        batch.drop_constraint(
            "ck_review_decisions_decision", type_="check"
        )
        batch.drop_constraint(
            "ck_review_decisions_evidence_quality", type_="check"
        )
    with op.batch_alter_table("evaluation_samples") as batch:
        batch.drop_constraint(
            "ck_evaluation_samples_review_status", type_="check"
        )
        batch.drop_constraint(
            "ck_evaluation_samples_authenticity", type_="check"
        )
    with op.batch_alter_table("product_documents") as batch:
        batch.drop_constraint(
            "ck_product_documents_review_status", type_="check"
        )
        batch.drop_constraint(
            "uq_product_documents_document_id", type_="unique"
        )
    with op.batch_alter_table("penalties") as batch:
        batch.drop_constraint(
            "ck_penalties_original_wording", type_="check"
        )
        batch.drop_constraint("ck_penalties_review_status", type_="check")
        batch.drop_constraint("uq_penalties_document_id", type_="unique")
    with op.batch_alter_table("regulations") as batch:
        batch.drop_constraint("ck_regulations_review_status", type_="check")
    with op.batch_alter_table("source_documents") as batch:
        batch.drop_constraint(
            "ck_source_documents_parse_status", type_="check"
        )
        batch.drop_constraint(
            "ck_source_documents_collection_status", type_="check"
        )
        batch.drop_constraint(
            "ck_source_documents_index_status", type_="check"
        )
        batch.drop_constraint(
            "ck_source_documents_review_status", type_="check"
        )
        batch.drop_constraint(
            "ck_source_documents_authenticity", type_="check"
        )
    op.drop_index(
        "ix_review_batch_items_document_id", table_name="review_batch_items"
    )
    op.drop_index(
        "ix_review_batch_items_batch_id", table_name="review_batch_items"
    )
    op.drop_table("review_batch_items")
    with op.batch_alter_table("review_batches") as batch:
        batch.drop_constraint("ck_review_batches_status", type_="check")
        batch.drop_column("schema_version")
        batch.drop_column("export_sha256")
    op.drop_index(
        "ix_document_occurrences_source_id", table_name="document_occurrences"
    )
    op.drop_index(
        "ix_document_occurrences_document_id", table_name="document_occurrences"
    )
    op.drop_table("document_occurrences")
