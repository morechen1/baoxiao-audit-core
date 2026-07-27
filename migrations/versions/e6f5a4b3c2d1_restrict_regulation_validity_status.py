"""restrict regulation validity status to unverified values

Revision ID: e6f5a4b3c2d1
Revises: d5e4f3a2b1c0
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f5a4b3c2d1"
down_revision: str | None = "d5e4f3a2b1c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    regulations = sa.table(
        "regulations",
        sa.column("validity_status", sa.String(50)),
    )
    op.execute(
        regulations.update()
        .where(
            regulations.c.validity_status.is_not(None),
            regulations.c.validity_status != "unknown",
        )
        .values(validity_status="unknown")
    )
    with op.batch_alter_table("regulations") as batch:
        batch.alter_column(
            "validity_status",
            existing_type=sa.String(50),
            nullable=True,
            server_default="unknown",
        )
        batch.create_check_constraint(
            "ck_regulations_validity_status",
            "validity_status IS NULL OR validity_status = 'unknown'",
        )


def downgrade() -> None:
    with op.batch_alter_table("regulations") as batch:
        batch.drop_constraint(
            "ck_regulations_validity_status",
            type_="check",
        )
        batch.alter_column(
            "validity_status",
            existing_type=sa.String(50),
            nullable=True,
            server_default=None,
        )
