"""KI-034/T-032b: изоляция semantic_cache по истории партии (unit)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.chat import ChatRequest
from app.api.v1.commentary import CommentaryRequest
from app.cache.semantic import analysis_hash, lookup, make_prompt_hash
from app.config import Settings
from app.engine.models import MoveAnalysis
from app.ingestion import ParsedGame, parse_pgn

# Две разные партии, которые приходят в одну и ту же финальную позицию.
PGN_A = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *"
PGN_B = "1. Nf3 Nc6 2. e4 e5 3. Bb5 a6 *"
FEN = "8/8/8/8/8/8/8/K6k w - - 0 1"


def _analyses(parsed: ParsedGame) -> list[MoveAnalysis]:
    return [
        MoveAnalysis(
            ply=move.ply,
            played_move_san=move.san,
            played_move_uci="a1a1",
            eval_before=0,
            eval_after=0,
            best_move_uci="a1a1",
            best_move_san=move.san,
            delta_cp=0,
            classification="good",
        )
        for move in parsed.moves
    ]


def test_endpoints_expose_use_cache_flag() -> None:
    assert CommentaryRequest(analysis_id=1).use_cache is True
    assert CommentaryRequest(analysis_id=1, use_cache=False).use_cache is False
    assert ChatRequest(analysis_id=1, message="question").use_cache is True
    assert (
        ChatRequest(analysis_id=1, message="question", use_cache=False).use_cache
        is False
    )


def test_transposed_games_share_fen_but_not_analysis_hash() -> None:
    parsed_a = parse_pgn(PGN_A)
    parsed_b = parse_pgn(PGN_B)

    assert parsed_a.final_fen == parsed_b.final_fen
    assert [move.san for move in parsed_a.moves] != [
        move.san for move in parsed_b.moves
    ]
    assert analysis_hash(parsed_a, _analyses(parsed_a)) != analysis_hash(
        parsed_b, _analyses(parsed_b)
    )


def test_analysis_hash_is_deterministic_and_ignores_metadata() -> None:
    first = parse_pgn(PGN_A)
    second = parse_pgn(PGN_A)

    assert analysis_hash(first, _analyses(first)) == analysis_hash(
        second, _analyses(second)
    )
    renamed = first.model_copy(
        update={"metadata": first.metadata.model_copy(update={"white": "Kasparov"})}
    )
    assert analysis_hash(renamed, _analyses(first)) == analysis_hash(
        first, _analyses(first)
    )


def test_prompt_hash_includes_analysis_hash() -> None:
    parsed_a = parse_pgn(PGN_A)
    parsed_b = parse_pgn(PGN_B)
    hash_a = analysis_hash(parsed_a, _analyses(parsed_a))
    hash_b = analysis_hash(parsed_b, _analyses(parsed_b))

    prompt_a = make_prompt_hash(
        "club", "model", "system", analysis_hash_value=hash_a
    )
    prompt_b = make_prompt_hash(
        "club", "model", "system", analysis_hash_value=hash_b
    )
    prompt_a_again = make_prompt_hash(
        "club", "model", "system", analysis_hash_value=hash_a
    )

    assert prompt_a != prompt_b
    assert prompt_a == prompt_a_again
    assert prompt_a != make_prompt_hash("club", "model", "system")


@pytest.mark.asyncio()
async def test_exact_only_lookup_does_not_call_embedder() -> None:
    session = MagicMock()
    exact_result = MagicMock()
    exact_result.first.return_value = None
    session.scalars = AsyncMock(return_value=exact_result)
    session.execute = AsyncMock()
    embedder = MagicMock()
    embedder.embed = AsyncMock(
        side_effect=AssertionError("эмбеддер не должен вызываться")
    )
    cfg = Settings(semantic_cache_enabled=True, embedding_provider="hash")

    hit = await lookup(
        session,
        fen=FEN,
        level="club",
        prompt_hash_value="other-game",
        settings=cfg,
        embedder=embedder,
        allow_semantic=False,
    )

    assert hit is None
    session.execute.assert_not_awaited()
    embedder.embed.assert_not_awaited()


@pytest.mark.asyncio()
async def test_semantic_lookup_still_calls_embedder_when_allowed() -> None:
    session = MagicMock()
    exact_result = MagicMock()
    exact_result.first.return_value = None
    session.scalars = AsyncMock(return_value=exact_result)
    vector_result = MagicMock()
    vector_result.first.return_value = None
    session.execute = AsyncMock(return_value=vector_result)
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[1.0] + [0.0] * 767)
    cfg = Settings(
        semantic_cache_enabled=True,
        embedding_provider="hash",
        semantic_cache_similarity_threshold=0.05,
    )

    hit = await lookup(
        session,
        fen=FEN,
        level="club",
        prompt_hash_value="same-game",
        settings=cfg,
        embedder=embedder,
    )

    assert hit is None
    embedder.embed.assert_awaited_once()
    session.execute.assert_awaited_once()
