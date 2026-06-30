"""add ai job table

Revision ID: b8a9f0d1c2e3
Revises: af3f5c8d9e21
Create Date: 2026-05-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8a9f0d1c2e3"
down_revision: Union[str, Sequence[str], None] = "af3f5c8d9e21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ai_job",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("corpus_id", sa.String(length=128), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("result_text", sa.Text(), nullable=False),
        sa.Column("annotated_text", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column("events_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_job_project_id"), "ai_job", ["project_id"], unique=False)
    op.create_index(op.f("ix_ai_job_corpus_id"), "ai_job", ["corpus_id"], unique=False)
    op.create_index(op.f("ix_ai_job_kind"), "ai_job", ["kind"], unique=False)
    op.create_index(op.f("ix_ai_job_status"), "ai_job", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_ai_job_status"), table_name="ai_job")
    op.drop_index(op.f("ix_ai_job_kind"), table_name="ai_job")
    op.drop_index(op.f("ix_ai_job_corpus_id"), table_name="ai_job")
    op.drop_index(op.f("ix_ai_job_project_id"), table_name="ai_job")
    op.drop_table("ai_job")
