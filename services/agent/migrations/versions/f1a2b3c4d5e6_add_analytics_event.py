"""add_analytics_event (0.6.1 P0 漏斗观测埋点)

Revision ID: f1a2b3c4d5e6
Revises: b2c3d4e5f6a8
Create Date: 2026-07-06 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'analytics_event',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('event', sa.String(length=48), nullable=False),
        # 与模型 Mapped[dict] 契约一致：应用层恒写 body.props or {}，非空更利于聚合分析。
        sa.Column('props', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_analytics_event_user_id'), 'analytics_event', ['user_id'], unique=False)
    op.create_index(op.f('ix_analytics_event_project_id'), 'analytics_event', ['project_id'], unique=False)
    op.create_index(op.f('ix_analytics_event_event'), 'analytics_event', ['event'], unique=False)
    op.create_index(op.f('ix_analytics_event_created_at'), 'analytics_event', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_analytics_event_created_at'), table_name='analytics_event')
    op.drop_index(op.f('ix_analytics_event_event'), table_name='analytics_event')
    op.drop_index(op.f('ix_analytics_event_project_id'), table_name='analytics_event')
    op.drop_index(op.f('ix_analytics_event_user_id'), table_name='analytics_event')
    op.drop_table('analytics_event')
