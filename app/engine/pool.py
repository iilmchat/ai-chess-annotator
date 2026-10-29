"""Пул процессов Stockfish для параллельного анализа партии (T-009).

Зачем: SLA из SPEC §9 — партия из 40 ходов (≈ 81 позиция) на уровне «клуб»
за ≤ 30 с. Один процесс даёт ~1 с на позицию (`movetime` — wall-clock
бюджет), т.е. ~81 с последовательно (KI-014). Пул из `N` независимых
процессов режет время примерно в `N` раз, потому что поиски идут
параллельно и не делят один UCI-канал.

Устройство:

* на старте создаются `N` `EngineService` (процессы поднимаются лениво, при
  первом обращении);
* свободные сервисы лежат в `asyncio.Queue`, задача забирает один на время
  поиска и возвращает обратно;
* порядок результатов соответствует порядку позиций (важно для сопоставления
  ходов);
* ошибка одного поиска не «съедает» остальные результаты: все задачи
  доводятся до конца, после чего поднимается первая ошибка.

Пул не привязан к loop до первого использования, поэтому создаётся в
lifespan приложения (и в тестах — на каждый `TestClient`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

from app.config import Settings, get_settings
from app.engine.cache import EngineCache
from app.engine.models import EngineLine, MoveAnalysis
from app.engine.service import (
    EngineService,
    build_move_analyses,
    prepare_positions,
)
from app.errors import EngineError
from app.ingestion.models import ParsedMove

__all__ = ["MAX_POOL_SIZE", "EnginePool"]

#: Верхняя граница размера пула: больше процессов не даёт выигрыша на
#: типичном хосте и только ест память (хеш Stockfish — десятки МБ).
MAX_POOL_SIZE = 8


class EnginePool:
    """`N` процессов Stockfish + очередь свободных.

    Args:
        settings: настройки (по умолчанию — `get_settings()`).
        size: размер пула; по умолчанию `settings.engine_pool_size`.
        executable: путь к бинарнику (для тестов).
        cache: кэш результатов Stockfish (T-108); ``None`` — без кэша.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        size: int | None = None,
        executable: Path | None = None,
        cache: EngineCache | None = None,
    ) -> None:
        cfg = settings or get_settings()
        requested = cfg.engine_pool_size if size is None else size
        if requested < 1:
            raise ValueError("size пула должен быть ≥ 1")

        self._size = min(requested, MAX_POOL_SIZE)
        self._services = [
            EngineService(cfg, executable=executable, cache=cache)
            for _ in range(self._size)
        ]
        self._idle: asyncio.Queue[EngineService] = asyncio.Queue()
        for service in self._services:
            self._idle.put_nowait(service)

    @property
    def size(self) -> int:
        """Фактический размер пула (после ограничения `MAX_POOL_SIZE`)."""
        return self._size

    async def analyze(self, fen: str, level: str) -> list[EngineLine]:
        """Проанализировать одну позицию на свободном движке."""
        service = await self._idle.get()
        try:
            return await service.analyze(fen, level)
        finally:
            self._idle.put_nowait(service)

    async def analyze_positions(
        self, fens: Sequence[str], level: str
    ) -> list[list[EngineLine]]:
        """Проанализировать позиции параллельно, сохранив порядок.

        Args:
            fens: FEN-позиции в порядке анализа.
            level: уровень ассистента.

        Returns:
            Линии MultiPV для каждой позиции (в том же порядке).

        Raises:
            Exception: первая ошибка любого из поисков (остальные задачи
                успевают завершиться и вернуть движки в пул).
        """
        if not fens:
            return []

        results: list[list[EngineLine] | None] = [None] * len(fens)
        errors: list[Exception] = []

        async def worker(index: int, fen: str) -> None:
            try:
                results[index] = await self.analyze(fen, level)
            except Exception as exc:
                errors.append(exc)

        await asyncio.gather(*(worker(i, f) for i, f in enumerate(fens)))

        if errors:
            raise errors[0]

        completed: list[list[EngineLine]] = []
        for index, item in enumerate(results):
            if item is None:
                raise EngineError(f"Нет результата анализа для позиции #{index}")
            completed.append(item)
        return completed

    async def analyze_game_moves(
        self,
        moves: Sequence[ParsedMove],
        level: str,
        *,
        initial_fen: str | None = None,
    ) -> list[MoveAnalysis]:
        """Параллельный аналог `EngineService.analyze_game_moves`."""
        if not moves:
            return []

        boards, played_uci = prepare_positions(moves, initial_fen=initial_fen)
        lines_by_position = await self.analyze_positions(
            [position.fen() for position in boards], level
        )
        return build_move_analyses(
            moves,
            boards,
            played_uci,
            lines_by_position,
            classify=EngineService.classify_move,
        )

    async def aclose(self) -> None:
        """Остановить все процессы пула (идемпотентно)."""
        await asyncio.gather(
            *(service.aclose() for service in self._services), return_exceptions=True
        )

    async def __aenter__(self) -> EnginePool:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
