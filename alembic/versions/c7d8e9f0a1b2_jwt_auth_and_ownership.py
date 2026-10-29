"""JWT auth and per-user ownership (T-028, KI-035)

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-29 10:05:00.000000

Итерация 24: JWT-аутентификация и изоляция данных.

* `users`: `role` (нативный enum `admin_role`: user/admin/auditor),
  `is_active`, `password_hash`, `last_login_at`. Backfill существующих —
  `role = 'user'`, `is_active = true`; `password_hash` NULL (вход появится
  после регистрации/выдачи пароля).
* `commentaries.user_id` и `edits.user_id` — владелец/автор для изоляции
  данных; FK `ON DELETE SET NULL`, NULL допустим для legacy-записей.
* Индексы: частичный `ix_users_role` (только не-user), `ix_games_user_id_created`
  для истории «мои партии», `ix_commentaries_user_id`, `ix_edits_user_id`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: тип создаётся/удаляется вручную здесь же.
admin_role_enum = postgresql.ENUM(
    "user", "admin", "auditor", name="admin_role", create_type=False
)


def upgrade() -> None:
    admin_role_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "role",
            admin_role_enum,
            nullable=False,
            server_default=sa.text("'user'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_users_role",
        "users",
        ["role"],
        unique=False,
        postgresql_where=sa.text("role <> 'user'"),
    )

    op.add_column(
        "commentaries", sa.Column("user_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_commentaries_user_id_users",
        "commentaries",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_commentaries_user_id", "commentaries", ["user_id"], unique=False
    )

    op.add_column("edits", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_edits_user_id_users",
        "edits",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_edits_user_id", "edits", ["user_id"], unique=False)

    op.create_index(
        "ix_games_user_id_created",
        "games",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_games_user_id_created", table_name="games")
    op.drop_index("ix_edits_user_id", table_name="edits")
    op.drop_constraint("fk_edits_user_id_users", "edits", type_="foreignkey")
    op.drop_column("edits", "user_id")

    op.drop_index("ix_commentaries_user_id", table_name="commentaries")
    op.drop_constraint(
        "fk_commentaries_user_id_users", "commentaries", type_="foreignkey"
    )
    op.drop_column("commentaries", "user_id")

    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "is_active")
    op.drop_column("users", "role")

    admin_role_enum.drop(op.get_bind(), checkfirst=True)
