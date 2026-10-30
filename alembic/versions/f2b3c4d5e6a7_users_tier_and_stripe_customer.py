"""users: tier and Stripe customer for billing (T-083)

Revision ID: f2b3c4d5e6a7
Revises: a9f1f28d4c45
Create Date: 2026-10-30 22:58:00.000000

Итерация 40: монетизация Free → Pro.

* `users.tier` — тариф (`free`/`pro`), NOT NULL, server_default `'free'`,
  CHECK-ограничение. Источник истины для дневного лимита токенов
  (`TokenBudgetMiddleware` больше не смотрит на роль/PRO_IPS).
* `users.stripe_customer_id` — Stripe Customer, nullable UNIQUE: создаётся
  при первом checkout и переиспользуется для оплаты/портала. Несколько
  NULL допустимы (Postgres не считает NULL-ы равными).
* Backfill: admin/auditor получают `pro` — до итерации 40 их лимит был
  pro по роли; иначе служебные учётки внезапно упали бы в free. Новые
  пользователи регистрируются как `free` (server_default).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "a9f1f28d4c45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "tier",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'free'"),
        ),
    )
    # Naming convention из `Base.metadata` даёт итоговое имя `ck_users_tier`.
    op.create_check_constraint(
        "tier", "users", "tier IN ('free', 'pro')"
    )
    op.add_column(
        "users",
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_stripe_customer_id", "users", ["stripe_customer_id"]
    )
    # Сохранить прежнюю семантику для служебных учёток (роль → pro).
    op.execute("UPDATE users SET tier = 'pro' WHERE role IN ('admin', 'auditor')")


def downgrade() -> None:
    op.drop_constraint("uq_users_stripe_customer_id", "users", type_="unique")
    op.drop_column("users", "stripe_customer_id")
    # `op.f` — готовое имя: без него naming convention добавит `ck_users_`
    # повторно и попытается удалить `ck_users_ck_users_tier`.
    op.drop_constraint(op.f("ck_users_tier"), "users", type_="check")
    op.drop_column("users", "tier")
