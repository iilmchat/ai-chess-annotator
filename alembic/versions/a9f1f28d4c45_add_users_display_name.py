"""users: add display_name for profile (T-082)

Revision ID: a9f1f28d4c45
Revises: dcf1da1421cd
Create Date: 2026-10-30 16:28:34.200861

Отображаемое имя пользователя (T-082): необязательное поле профиля,
меняется через `PATCH /auth/me`. `nullable=True` намеренно: у существующих
пользователей имени нет, UI показывает email. `server_default` не нужен —
колонка не участвует в NOT NULL-ограничениях и не требует backfill-а.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9f1f28d4c45'
down_revision: Union[str, None] = 'dcf1da1421cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('display_name', sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'display_name')
