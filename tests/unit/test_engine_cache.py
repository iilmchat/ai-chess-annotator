"""Unit-тесты ключа кэша движка (T-108) без БД и Stockfish.

SQL-часть (чтение/запись/чистка `engine_cache`) и повторный анализ —
в `tests/integration/test_engine_cache.py`.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.engine.cache import (
    EngineCache,
    EngineCacheKey,
    engine_fen_hash,
    normalize_fen_for_cache,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_normalize_ignores_fullmove_number() -> None:
    first = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    later = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 42"
    assert normalize_fen_for_cache(first) == normalize_fen_for_cache(later)


def test_normalize_keeps_halfmove_clock_and_side() -> None:
    base = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    halfmove = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 99 1"
    black = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"
    assert normalize_fen_for_cache(base) != normalize_fen_for_cache(halfmove)
    assert normalize_fen_for_cache(base) != normalize_fen_for_cache(black)


def test_engine_fen_hash_is_sha256_hex() -> None:
    digest = engine_fen_hash(START)
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert digest == engine_fen_hash(START)


def test_engine_cache_key_is_hashable_and_distinct() -> None:
    base = EngineCacheKey(
        fen_hash=engine_fen_hash(START),
        engine_version="Stockfish 17",
        depth=12,
        multipv=1,
    )
    same = EngineCacheKey(
        fen_hash=engine_fen_hash(START),
        engine_version="Stockfish 17",
        depth=12,
        multipv=1,
    )
    other_depth = EngineCacheKey(
        fen_hash=engine_fen_hash(START),
        engine_version="Stockfish 17",
        depth=18,
        multipv=3,
    )
    assert base == same
    assert base != other_depth
    assert len({base, same, other_depth}) == 2


def test_engine_cache_rejects_zero_ttl() -> None:
    settings = get_settings().model_copy(update={"engine_cache_ttl_days": 0})
    with pytest.raises(ValueError, match="≥ 1"):
        EngineCache(settings)


def test_engine_cache_defaults() -> None:
    settings = get_settings()
    assert settings.engine_cache_enabled is True
    assert settings.engine_cache_ttl_days == 30
