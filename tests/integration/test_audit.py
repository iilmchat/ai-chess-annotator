"""Hash-chain аудита и append-only триггер (T-035, итерация 18)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.audit import (
    GENESIS_HASH,
    append_audit,
    canonical_details,
    compute_audit_hash,
    verify_audit_chain,
)
from app.config import get_settings
from app.db.models import AuditLog


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


def test_hash_is_deterministic() -> None:
    ts = datetime(2026, 10, 29, 1, 2, 3, tzinfo=UTC)
    details = canonical_details({"b": 2, "a": 1})

    first = compute_audit_hash(
        GENESIS_HASH, ts, "admin", "prompt.update", "1", details
    )
    second = compute_audit_hash(
        GENESIS_HASH, ts, "admin", "prompt.update", "1", details
    )

    assert first == second
    assert len(first) == 64
    assert details == '{"a":1,"b":2}'


async def test_valid_chain_passes(session: AsyncSession) -> None:
    await append_audit(
        session, actor="test", action="test.one", details={"n": 1}
    )
    await append_audit(
        session, actor="test", action="test.two", details={"n": 2}
    )

    result = await verify_audit_chain(session)

    assert result.ok is True
    assert result.checked >= 2
    assert result.errors == ()


async def test_broken_row_is_detected(session: AsyncSession) -> None:
    row = AuditLog(
        timestamp=datetime.now(UTC),
        actor="tamper",
        action="test.broken",
        target_id=None,
        details={},
        prev_hash=GENESIS_HASH,
        current_hash="f" * 64,
    )
    session.add(row)
    await session.flush()

    result = await verify_audit_chain(session)

    assert result.ok is False
    assert any(str(row.id) in error for error in result.errors)


async def test_truncate_is_rejected_by_trigger(session: AsyncSession) -> None:
    await append_audit(session, actor="test", action="test.no_truncate")

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text("TRUNCATE audit_logs"))

    await session.rollback()


async def test_update_is_rejected_by_trigger(session: AsyncSession) -> None:
    row = await append_audit(
        session, actor="test", action="test.immutable", details={}
    )

    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(
            update(AuditLog)
            .where(AuditLog.id == row.id)
            .values(actor="hacked")
        )

    await session.rollback()
