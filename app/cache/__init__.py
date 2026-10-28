"""Semantic cache: точный и векторный поиск ответов LLM (T-032)."""

from app.cache.semantic import (
    CacheHit,
    cache_usage,
    chat_position_fen,
    cleanup_expired_cache,
    commentary_position_fen,
    fen_hash,
    lookup,
    make_prompt_hash,
    model_for_level,
    store,
)

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
