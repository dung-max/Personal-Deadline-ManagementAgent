"""create tasks and reminders

Revision ID: 0001
Revises:
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create tasks table
    op.create_table(
        'tasks',
        sa.Column('id', sa.UUID(), primary_key=True, nullable=False),
        sa.Column('task_name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=False),
        sa.Column('priority', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_tasks_status', 'tasks', ['status'], unique=False)
    op.create_index('ix_tasks_deadline', 'tasks', ['deadline'], unique=False)

    # 2. Create reminders table
    op.create_table(
        'reminders',
        sa.Column('id', sa.UUID(), primary_key=True, nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('remind_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_reminders_task_id', 'reminders', ['task_id'], unique=False)
    op.create_index('ix_reminders_remind_at', 'reminders', ['remind_at'], unique=False)


def downgrade() -> None:
    # 1. Drop reminders table
    op.drop_index('ix_reminders_remind_at', table_name='reminders')
    op.drop_index('ix_reminders_task_id', table_name='reminders')
    op.drop_table('reminders')

    # 2. Drop tasks table
    op.drop_index('ix_tasks_deadline', table_name='tasks')
    op.drop_index('ix_tasks_status', table_name='tasks')
    op.drop_table('tasks')