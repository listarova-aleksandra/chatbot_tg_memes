"""quiz question media type

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('quiz_questions', sa.Column('media_type', sa.String(length=10), server_default='gif', nullable=False))


def downgrade() -> None:
    op.drop_column('quiz_questions', 'media_type')
