"""RAG-контекст для комментария (T-017/T-018).

Для 2–3 ключевых моментов партии (`blunder`/`mistake`/`inaccuracy`) ищет
похожие позиции в `annotated_games` и добавляет найденные аннотации в пакет
фактов отдельным разделом. Полный retrieval — для `gm` (SPEC §3); для
уровня `club` он опционален (`RAG_CLUB_ENABLED`) и использует уменьшенный
`top_k`. Новичку база аннотаций не нужна.

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
from app.rag.cache import RetrievalCache
from app.rag.service import find_similar_positions

__all__ = ["make_rag_context_builder"]

logger = structlog.get_logger(__name__)

_KEY_CLASSES = frozenset({"blunder", "mistake", "inaccuracy"})
_MAX_KEY_MOMENTS = 3

type RagContextBuilder = Callable[
    [ParsedGame, list[MoveAnalysis], str], Awaitable[str | None]
]
type SessionFactory = Callable[[], AsyncSession]


def make_rag_context_builder(
    settings: Settings | None = None,
    *,
    session_factory: SessionFactory | None = None,
    cache: RetrievalCache | None = None,
) -> RagContextBuilder:
    """Собрать функцию `(ParsedGame, analyses, level) -> str | None`."""
    cfg = settings or get_settings()
    factory = session_factory or SessionLocal
    retrieval = cache if cache is not None else RetrievalCache(cfg)

    async def builder(
        parsed: ParsedGame,
        analyses: list[MoveAnalysis],
        level: str = "gm",
    ) -> str | None:
        if not cfg.rag_enabled:
            return None
        if level == "gm":
            top_k = cfg.rag_top_k
        elif level == "club" and cfg.rag_club_enabled:
            top_k = cfg.rag_club_top_k
        else:
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
                        fen, top_k=top_k, session=session, cache=retrieval
                    )
                except Exception as exc:  # RAG не должен ломать генерацию
                    logger.warning("rag_lookup_failed", error=str(exc))
                    continue
                if texts:
                    move_number = (analysis.ply + 1) // 2
                    side_gen = (
                        "белых" if analysis.ply % 2 == 1 else "чёрных"
                    )
                    chunks.append(
                        f"## Ход {move_number} {side_gen} "
                        f"({analysis.played_move_san})\n"
                        + "\n".join(f"- {text}" for text in texts)
                    )

        if not chunks:
            return None
        return "# Похожие позиции из базы аннотаций (RAG)\n\n" + "\n\n".join(chunks)

    return builder
