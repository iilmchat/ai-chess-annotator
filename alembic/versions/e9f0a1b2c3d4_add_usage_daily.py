"""usage_daily — durable-агрегаты расхода токенов за день (T-031b)

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-29 13:50:00.000000

Итерация 28: FinOps durable aggregates (DESIGN_ADMIN_AND_OPS §4.5).

* `usage_daily`: один ряд на субъект (`subject_type`: `user`|`ip`,
  `subject_key`: id/IP) и UTC-день. Redis остаётся горячим счётчиком для
  429; эта таблица — durable-история для FinOps-отчётов и fallback в
  `/v1/users/me/usage`.
* Пишется best-effort upsert-ом из `record_token_usage`:
  `ON CONFLICT (subject_type, subject_key, day) DO UPDATE` —
  токены/стоимость прибавляются, `requests + 1`.
* `UNIQUE (subject_type, subject_key, day)` + индексы под оба порядка
  чтения: fallback по субъекту/дню и отчёты/retention по дню/типу.
* CHECK-и `>= 0` защищают агрегаты от отрицательных дельт.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_daily",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("subject_type", sa.String(length=8), nullable=False),
        sa.Column("subject_key", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "tokens_in",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "tokens_out",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "requests",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "tokens_in >= 0", name="ck_usage_daily_tokens_in_nonnegative"
        ),
        sa.CheckConstraint(
            "tokens_out >= 0", name="ck_usage_daily_tokens_out_nonnegative"
        ),
        sa.CheckConstraint(
            "cost_usd >= 0", name="ck_usage_daily_cost_usd_nonnegative"
        ),
        sa.CheckConstraint(
            "requests >= 0", name="ck_usage_daily_requests_nonnegative"
        ),
        sa.UniqueConstraint(
            "subject_type",
            "subject_key",
            "day",
            name="uq_usage_daily_subject_day",
        ),
    )
    op.create_index(
        "ix_usage_daily_day_subject",
        "usage_daily",
        ["day", "subject_type"],
        unique=False,
    )
    op.create_index(
        "ix_usage_daily_subject_day",
        "usage_daily",
        ["subject_type", "subject_key", "day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_usage_daily_subject_day", table_name="usage_daily")
    op.drop_index("ix_usage_daily_day_subject", table_name="usage_daily")
    op.drop_table("usage_daily")
