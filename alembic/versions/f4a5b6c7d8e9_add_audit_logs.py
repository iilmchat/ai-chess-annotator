"""audit_logs: append-only журнал с hash-chain (T-035, итерация 18).

Триггер запрещает UPDATE/DELETE: изменение строки ломает цепочку и
обнаруживается `verify_audit_chain`, а сама мутация отклоняется БД.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("current_hash", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "ix_audit_logs_timestamp", "audit_logs", ["timestamp"]
    )
    op.create_index(
        "ix_audit_logs_action_timestamp",
        "audit_logs",
        ["action", "timestamp"],
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_logs_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only';
        END $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_reject_mutation();
        """
    )
    # Row-level trigger не ловит TRUNCATE — нужен statement-level.
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_reject_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_immutable ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_reject_mutation()")
