"""add paper external id table

Revision ID: af3f5c8d9e21
Revises: 3354597374aa
Create Date: 2026-05-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "af3f5c8d9e21"
down_revision: Union[str, Sequence[str], None] = "3354597374aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_external_id",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("id_type", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["paper_id"], ["paper.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_id", "provider", "id_type", "external_id", name="uq_paper_external_id_paper"),
    )
    op.create_index(op.f("ix_paper_external_id_paper_id"), "paper_external_id", ["paper_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_paper_external_id_paper_id"), table_name="paper_external_id")
    op.drop_table("paper_external_id")
