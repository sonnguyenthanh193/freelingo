"""Add eval_status column to exercises for async evaluation tracking.

Revision ID: 0051_exercise_eval_status
Revises: 0050_dashboard_banner
"""

import sqlalchemy as sa

from alembic import op

revision = "0051_exercise_eval_status"
down_revision = "0050_dashboard_banner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exercises",
        sa.Column("eval_status", sa.String(length=20), nullable=False, server_default="completed"),
    )


def downgrade() -> None:
    op.drop_column("exercises", "eval_status")
