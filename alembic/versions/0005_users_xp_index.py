"""index on users.xp for the leaderboard

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 12:00:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_users_xp'), 'users', ['xp'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_xp'), table_name='users')
