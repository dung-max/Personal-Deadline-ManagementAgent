"""create pending_confirmations

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create pending_confirmations table.
    # Columns from genai_core Base (id, created_at, updated_at) plus
    # PendingConfirmation-specific fields (user_id, execution_command,
    # expires_at, status).
    #
    # NOTE: updated_at is nullable here (inherited from Base) whereas
    # tasks/reminders override it to NOT NULL.  This matches the model
    # definition in models.py:27-45.
    op.create_table(
        'pending_confirmations',
        sa.Column('id', sa.UUID(), primary_key=True, nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('execution_command', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
    )
    op.create_index(
        'ix_pending_confirmations_user_id',
        'pending_confirmations',
        ['user_id'],
        unique=False,
    )
    op.create_index(
        'ix_pending_confirmations_status',
        'pending_confirmations',
        ['status'],
        unique=False,
    )
    op.create_index(
        'ix_pending_confirmations_expires_at',
        'pending_confirmations',
        ['expires_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_pending_confirmations_expires_at',
        table_name='pending_confirmations',
    )
    op.drop_index(
        'ix_pending_confirmations_status',
        table_name='pending_confirmations',
    )
    op.drop_index(
        'ix_pending_confirmations_user_id',
        table_name='pending_confirmations',
    )
    op.drop_table('pending_confirmations')
