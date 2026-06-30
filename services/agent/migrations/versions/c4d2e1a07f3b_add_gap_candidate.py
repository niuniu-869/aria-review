"""add gap_candidate (research-kernel A: scratchpad / value verdict / HITL)

Revision ID: c4d2e1a07f3b
Revises: eb2f7c71c456
Create Date: 2026-06-16 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d2e1a07f3b'
down_revision: Union[str, Sequence[str], None] = 'eb2f7c71c456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'gap_candidate',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('gap_id', sa.String(length=64), nullable=False),
        sa.Column('run_id', sa.String(length=64), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('theme', sa.Text(), nullable=True),
        sa.Column('statement', sa.Text(), nullable=True),
        sa.Column('lens', sa.String(length=16), nullable=True),
        sa.Column('supporting_papers', sa.JSON(), nullable=True),
        sa.Column('counter_evidence', sa.JSON(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=True),
        sa.Column('value_verdict', sa.JSON(), nullable=True),
        sa.Column('evidence_pack', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_gap_candidate_gap_id'), 'gap_candidate', ['gap_id'], unique=True)
    op.create_index(op.f('ix_gap_candidate_run_id'), 'gap_candidate', ['run_id'], unique=False)
    op.create_index(op.f('ix_gap_candidate_project_id'), 'gap_candidate', ['project_id'], unique=False)
    op.create_index(op.f('ix_gap_candidate_status'), 'gap_candidate', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_gap_candidate_status'), table_name='gap_candidate')
    op.drop_index(op.f('ix_gap_candidate_project_id'), table_name='gap_candidate')
    op.drop_index(op.f('ix_gap_candidate_run_id'), table_name='gap_candidate')
    op.drop_index(op.f('ix_gap_candidate_gap_id'), table_name='gap_candidate')
    op.drop_table('gap_candidate')
