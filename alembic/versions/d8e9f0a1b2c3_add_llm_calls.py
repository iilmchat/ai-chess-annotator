"""llm_calls — durable-история вызовов LLM (T-030)

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-29 12:40:00.000000

Итерация 27: LLMOps-core (DESIGN_ADMIN_AND_OPS §4.4).

* Таблица `llm_calls`: один ряд — один вызов провайдера LLM. Тексты
  промптов/ответов не хранятся (privacy) — только операция, модель,
  токены, стоимость, латентность и статус.
* `level` — существующий нативный enum `level`, nullable (NULL для
  warmup/classifier). Тип здесь не создаётся/не удаляется.
* FK `actor_id`/`game_id`/`commentary_id` → `ON DELETE SET NULL`: история
  вызовов переживает удаление пользователя/партии/комментария.
* `UNIQUE (trace_id, span_id)` — защита от дублей при ретраях доставки;
  каждый вызов LLM получает собственный `span_id`.
* Индексы: BRIN по `created_at` (append-only история), `(model,
  created_at DESC)` и `(level, created_at DESC)` для админ-выборок
  «последние вызовы».
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: тип `level` создан миграцией init_schema и общий с
# games/commentaries/semantic_cache; здесь только используем его.
level_enum = postgresql.ENUM(
    "beginner", "club", "gm", name="level", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("span_id", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("game_id", sa.BigInteger(), nullable=True),
        sa.Column("commentary_id", sa.BigInteger(), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("level", level_enum, nullable=True),
        sa.Column("prompt_slug", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column(
            "tokens_in",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "tokens_out",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "cache_hit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "tokens_in >= 0", name="ck_llm_calls_tokens_in_nonnegative"
        ),
        sa.CheckConstraint(
            "tokens_out >= 0", name="ck_llm_calls_tokens_out_nonnegative"
        ),
        sa.CheckConstraint(
            "cost_usd >= 0", name="ck_llm_calls_cost_usd_nonnegative"
        ),
        sa.CheckConstraint(
            "latency_ms >= 0", name="ck_llm_calls_latency_ms_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_llm_calls_actor_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
            name="fk_llm_calls_game_id_games",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["commentary_id"],
            ["commentaries.id"],
            name="fk_llm_calls_commentary_id_commentaries",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "trace_id", "span_id", name="uq_llm_calls_trace_span"
        ),
    )
    op.create_index(
        "ix_llm_calls_created_brin",
        "llm_calls",
        ["created_at"],
        unique=False,
        postgresql_using="brin",
    )
    op.create_index(
        "ix_llm_calls_model_created",
        "llm_calls",
        [sa.text("model"), sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_llm_calls_level_created",
        "llm_calls",
        [sa.text("level"), sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_calls_level_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_model_created", table_name="llm_calls")
    op.drop_index("ix_llm_calls_created_brin", table_name="llm_calls")
    op.drop_table("llm_calls")
