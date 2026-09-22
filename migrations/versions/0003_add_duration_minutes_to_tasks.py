"""add duration_minutes to tasks

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('duration_minutes', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'duration_minutes')
