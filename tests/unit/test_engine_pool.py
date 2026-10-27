"""Тесты пула движков (T-009 / KI-014).

Реальный Stockfish здесь не нужен: проверяется **логика пула** —
параллелизм, сохранение порядка, распространение ошибок, закрытие.
Сам движок и SLA проверяются в `tests/integration/test_api_analyze.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.config import get_settings
from app.engine import EngineService
from app.engine.models import EngineLine
from app.engine.pool import MAX_POOL_SIZE, EnginePool
from app.errors import EngineError


def _line(move_uci: str = "e2e4") -> list[EngineLine]:
    return [
        EngineLine(multipv=1, depth=10, score_cp=20, move_uci=move_uci, pv=[move_uci])
    ]


@pytest.fixture
async def pool() -> AsyncIterator[EnginePool]:
    instance = EnginePool(size=3)
    try:
        yield instance
    finally:
        await instance.aclose()


def test_pool_size_from_settings_and_limit() -> None:
    assert EnginePool().size == get_settings().engine_pool_size
    assert EnginePool(size=2).size == 2
    assert EnginePool(size=MAX_POOL_SIZE + 5).size == MAX_POOL_SIZE

    with pytest.raises(ValueError, match="≥ 1"):
        EnginePool(size=0)


async def test_analyze_positions_is_parallel_and_ordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"active": 0, "max_active": 0}

    async def fake_analyze(
        self: EngineService,
        fen: str,
        level: str,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> list[EngineLine]:
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        # Глубина кодирует индекс позиции, чтобы проверить сохранение порядка.
        index = int(fen.rsplit("-", 1)[1])
        return [
            EngineLine(
                multipv=1, depth=index, score_cp=20, move_uci="e2e4", pv=["e2e4"]
            )
        ]

    monkeypatch.setattr(EngineService, "analyze", fake_analyze)
    pool = EnginePool(size=3)
    try:
        results = await pool.analyze_positions([f"fen-{index}" for index in range(9)], "club")
    finally:
        await pool.aclose()

    assert len(results) == 9
    assert [lines[0].depth for lines in results] == list(range(9))
    assert state["max_active"] == 3, "пул должен держать не больше size поисков"


async def test_analyze_positions_empty() -> None:
    pool = EnginePool(size=2)
    try:
        assert await pool.analyze_positions([], "club") == []
    finally:
        await pool.aclose()


async def test_analyze_positions_propagates_first_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_analyze(self: EngineService, fen: str, level: str, **_: Any) -> list[EngineLine]:
        await asyncio.sleep(0.01)
        if fen == "bad":
            raise EngineError("движок сломался")
        return _line()

    monkeypatch.setattr(EngineService, "analyze", fake_analyze)
    pool = EnginePool(size=2)
    try:
        with pytest.raises(EngineError, match="сломался"):
            await pool.analyze_positions(["ok", "bad", "ok"], "club")
    finally:
        await pool.aclose()


async def test_analyze_game_moves_empty_short_circuit() -> None:
    pool = EnginePool(size=2)
    try:
        assert await pool.analyze_game_moves([], "club") == []
    finally:
        await pool.aclose()


async def test_aclose_closes_every_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    async def fake_aclose(self: EngineService) -> None:
        closed.append(id(self))

    monkeypatch.setattr(EngineService, "aclose", fake_aclose)
    pool = EnginePool(size=3)

    await pool.aclose()

    assert len(closed) == 3
    assert len(set(closed)) == 3
