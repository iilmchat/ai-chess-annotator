"""Semantic cache для ответов LLM (T-032, итерация 19).

Два уровня поиска:

1. **exact** — `fen_hash + level + prompt_hash`; дешёвый SELECT, работает
   без эмбеддера.
2. **semantic** — косинусное расстояние между `fen_embedding` запроса и
   записями с тем же `level + prompt_hash`; порог по умолчанию 0.05.

`prompt_hash` включает системный промпт, модель и style/question: без
него кэш вернул бы ответ на другой вопрос или после смены промпта.
`created_at` + TTL отсекает старые записи; `cleanup_expired_cache`
удаляет их физически.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Level, SemanticCache
from app.engine.models import MoveAnalysis
from app.ingestion.models import ParsedGame
from app.ingestion.parser import parse_fen
from app.llm.base import LLMUsage
from app.rag.embedder import Embedder, get_embedder, position_document

__all__ = [
    "CacheHit",
    "cache_usage",
    "chat_position_fen",
    "cleanup_expired_cache",
    "commentary_position_fen",
    "fen_hash",
    "lookup",
    "make_prompt_hash",
    "model_for_level",
    "store",
]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CacheHit:
    """Найденный ответ кэша."""

    text: str
    kind: str  # "exact" | "semantic"
    distance: float | None = None


def model_for_level(settings: Settings, level: str) -> str:
    """Имя модели, которое увидит LLM-клиент для уровня."""
    if level in {"beginner", "club"}:
        return settings.lora_model
    return settings.deepseek_model


def fen_hash(fen: str) -> str:
    """sha256 канонического FEN (после parse_fen)."""
    canonical = parse_fen(fen).fen()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_prompt_hash(
    level: str,
    model: str,
    system_prompt: str,
    *,
    style: str | None = None,
    question: str | None = None,
    history: str | None = None,
) -> str:
    """Отпечаток «уровень + модель + sysprompt + style/question/history»."""
    payload = "\x1f".join(
        (
            level,
            model,
            system_prompt,
            style or "",
            question or "",
            history or "",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fen_before(parsed: ParsedGame, ply: int) -> str:
    if ply <= 1:
        return parsed.initial_fen
    return parsed.moves[ply - 2].fen


def commentary_position_fen(
    parsed: ParsedGame, analyses: list[MoveAnalysis]
) -> str:
    """Позиция, по которой кэшируется комментарий.

    Берём позицию перед самым крупным ключевым моментом (максимальная
    потеря), иначе — финальную позицию партии.
    """
    if analyses:
        key = max(analyses, key=lambda item: item.delta_cp)
        return _fen_before(parsed, key.ply)
    if parsed.moves:
        return parsed.moves[-1].fen
    return parsed.initial_fen


def chat_position_fen(parsed: ParsedGame) -> str:
    """Позиция для чата: финальная позиция партии (или начальная)."""
    if parsed.moves:
        return parsed.moves[-1].fen
    return parsed.initial_fen


def cache_usage() -> LLMUsage:
    """Синтетический usage для ответа из кэша (0 токенов, 0 стоимости)."""
    return LLMUsage(
        provider="cache",
        model="semantic-cache",
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
    )


def _cutoff(settings: Settings) -> datetime:
    return datetime.now(UTC) - timedelta(days=settings.semantic_cache_ttl_days)


async def lookup(
    session: AsyncSession,
    *,
    fen: str,
    level: str,
    prompt_hash_value: str,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
) -> CacheHit | None:
    """Найти ответ кэша: exact, затем semantic (если разрешён)."""
    cfg = settings or get_settings()
    if not cfg.semantic_cache_enabled:
        return None
    try:
        canonical = parse_fen(fen).fen()
        level_enum = Level(level)
    except (ValueError, TypeError) as exc:
        logger.warning("semantic_cache_bad_key", error=str(exc))
        return None

    cutoff = _cutoff(cfg)
    exact = (
        await session.scalars(
            select(SemanticCache)
            .where(
                SemanticCache.fen_hash
                == hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                SemanticCache.level == level_enum,
                SemanticCache.prompt_hash == prompt_hash_value,
                SemanticCache.created_at >= cutoff,
            )
            .order_by(SemanticCache.created_at.desc())
            .limit(1)
        )
    ).first()
    if exact is not None:
        logger.info("semantic_cache_hit", kind="exact", level=level)
        return CacheHit(text=exact.response_text, kind="exact")

    try:
        emb = embedder or get_embedder(cfg)
        vector = await emb.embed(position_document(canonical))
        distance = SemanticCache.fen_embedding.cosine_distance(vector)
        row = (
            await session.execute(
                select(SemanticCache, distance.label("distance"))
                .where(
                    SemanticCache.level == level_enum,
                    SemanticCache.prompt_hash == prompt_hash_value,
                    SemanticCache.created_at >= cutoff,
                    distance < cfg.semantic_cache_similarity_threshold,
                )
                .order_by(distance)
                .limit(1)
            )
        ).first()
    except Exception as exc:  # кэш не должен ломать генерацию
        logger.warning("semantic_cache_lookup_failed", error=str(exc))
        return None

    if row is None:
        logger.info("semantic_cache_miss", level=level)
        return None
    entry, distance_value = row[0], float(row[1])
    logger.info(
        "semantic_cache_hit",
        kind="semantic",
        level=level,
        distance=round(distance_value, 6),
    )
    return CacheHit(
        text=entry.response_text,
        kind="semantic",
        distance=distance_value,
    )


async def store(
    session: AsyncSession,
    *,
    fen: str,
    level: str,
    prompt_hash_value: str,
    response_text: str,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
) -> bool:
    """Сохранить ответ в кэш (UPSERT по точному ключу).

    Возвращает ``False`` без исключения, если кэш выключен, текст пуст
    или эмбеддер недоступен.
    """
    cfg = settings or get_settings()
    if not cfg.semantic_cache_enabled:
        return False
    text = response_text.strip()
    if not text:
        return False
    try:
        canonical = parse_fen(fen).fen()
        level_enum = Level(level)
        emb = embedder or get_embedder(cfg)
        vector = await emb.embed(position_document(canonical))
        statement = (
            pg_insert(SemanticCache)
            .values(
                fen_hash=hashlib.sha256(
                    canonical.encode("utf-8")
                ).hexdigest(),
                fen_embedding=vector,
                level=level_enum,
                prompt_hash=prompt_hash_value,
                response_text=text,
                created_at=func.now(),
            )
            .on_conflict_do_update(
                constraint="uq_semantic_cache_key",
                set_={
                    "response_text": text,
                    "fen_embedding": vector,
                    "created_at": func.now(),
                },
            )
        )
        await session.execute(statement)
        logger.info("semantic_cache_stored", level=level)
        return True
    except Exception as exc:  # кэш best-effort
        logger.warning("semantic_cache_store_failed", error=str(exc))
        return False


async def cleanup_expired_cache(
    session: AsyncSession, *, settings: Settings | None = None
) -> int:
    """Удалить записи старше TTL; возвращает число удалённых строк."""
    cfg = settings or get_settings()
    result = await session.execute(
        delete(SemanticCache).where(
            SemanticCache.created_at < _cutoff(cfg)
        )
    )
    return int(cast(CursorResult[Any], result).rowcount or 0)
