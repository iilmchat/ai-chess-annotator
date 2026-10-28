"""RAG-контекст для комментария гроссмейстерского уровня (T-017).

Для 2–3 ключевых моментов партии (`blunder`/`mistake`/`inaccuracy`) ищет
похожие позиции в `annotated_games` и добавляет найденные аннотации в пакет
фактов отдельным разделом. Только для `gm`: новичку и клубу база
аннотаций не нужна (SPEC §3).

Сбой RAG не должен ломать генерацию: любая ошибка поиска логируется, и
контекст просто не добавляется.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import SessionLocal
from app.engine.models import MoveAnalysis
from app.ingestion.models import ParsedGame
from app.rag.service import find_similar_positions

__all__ = ["make_rag_context_builder"]

logger = structlog.get_logger(__name__)

_KEY_CLASSES = frozenset({"blunder", "mistake", "inaccuracy"})
_MAX_KEY_MOMENTS = 3
_MAX_PER_POSITION = 2

type RagContextBuilder = Callable[
    [ParsedGame, list[MoveAnalysis]], Awaitable[str | None]
]
type SessionFactory = Callable[[], AsyncSession]


def make_rag_context_builder(
    settings: Settings | None = None,
    *,
    session_factory: SessionFactory | None = None,
) -> RagContextBuilder:
    """Собрать функцию `(ParsedGame, analyses) -> str | None` для эндпоинта."""
    cfg = settings or get_settings()
    factory = session_factory or SessionLocal

    async def builder(
        parsed: ParsedGame, analyses: list[MoveAnalysis]
    ) -> str | None:
        if not cfg.rag_enabled:
            return None

        key_moments = [
            analysis
            for analysis in analyses
            if analysis.classification in _KEY_CLASSES
        ][:_MAX_KEY_MOMENTS]
        if not key_moments:
            return None

        chunks: list[str] = []
        async with factory() as session:
            for analysis in key_moments:
                fen = (
                    parsed.initial_fen
                    if analysis.ply <= 1
                    else parsed.moves[analysis.ply - 2].fen
                )
                try:
                    texts = await find_similar_positions(
                        fen, top_k=_MAX_PER_POSITION, session=session
                    )
                except Exception as exc:  # RAG не должен ломать генерацию
                    logger.warning("rag_lookup_failed", error=str(exc))
                    continue
                if texts:
                    chunks.append(
                        f"## Полуход {analysis.ply} ({analysis.played_move_san})\n"
                        + "\n".join(f"- {text}" for text in texts)
                    )

        if not chunks:
            return None
        return "# Похожие позиции из базы аннотаций (RAG)\n\n" + "\n\n".join(chunks)

    return builder
