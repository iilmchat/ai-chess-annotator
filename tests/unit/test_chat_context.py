"""Контекст выбранного полухода в промпте чата (T-072).

Проверяем, что строка «пользователь смотрит на ход N» попадает именно в
user-промпт, локализована (ru/en) и молча исчезает для ``None``,
отрицательных и отсутствующих в анализе полуходов. ``0`` — стартовая
позиция: отдельная строка без номера хода.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.chat.service import (
    CHAT_SYSTEM_PROMPT,
    answer_question,
    build_chat_prompt,
    format_selected_ply_context,
)
from app.engine.models import MoveAnalysis
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage

PGN = "1. e4 e5 1/2-1/2"

#: Полуходы 13-го хода: 25.Nf3 (белые) и 26...Re8 (чёрные).
ANALYSES = [
    MoveAnalysis(
        ply=25,
        played_move_san="Nf3",
        played_move_uci="g1f3",
        eval_before=30,
        eval_after=30,
        best_move_uci="g1f3",
        best_move_san="Nf3",
        delta_cp=0,
        classification="good",
    ),
    MoveAnalysis(
        ply=26,
        played_move_san="Re8",
        played_move_uci="e8e7",
        eval_before=-40,
        eval_after=-40,
        best_move_uci="e8e7",
        best_move_san="Re8",
        delta_cp=0,
        classification="good",
    ),
]


def test_russian_context_line_has_move_number_side_and_eval() -> None:
    prompt = build_chat_prompt(
        "FACTS", "Почему плохо?", analyses=ANALYSES, selected_ply=26
    )

    assert "Пользователь сейчас смотрит на ход 13... Re8" in prompt
    assert "13-й ход чёрных" in prompt
    assert "оценка +0.40 за белых" in prompt
    assert "«этот ход»" in prompt
    assert "«а лучше?»" in prompt
    # Контекст — часть user-промпта и стоит до вопроса.
    assert prompt.index("Re8") < prompt.index("# Вопрос пользователя")


def test_english_context_line_has_move_number_side_and_eval() -> None:
    prompt = build_chat_prompt(
        "FACTS",
        "Why is it bad?",
        analyses=ANALYSES,
        selected_ply=26,
        language="en",
    )

    assert "The user is currently looking at move 13... Re8" in prompt
    assert "Black's 13th move" in prompt
    assert "eval +0.40 for White" in prompt
    assert '"this move"' in prompt
    assert prompt.index("Re8") < prompt.index("# User question")


def test_white_move_uses_dot_and_masculine_side() -> None:
    prompt = build_chat_prompt(
        "FACTS", "А лучше?", analyses=ANALYSES, selected_ply=25
    )

    assert "смотрит на ход 13. Nf3" in prompt
    assert "13-й ход белых" in prompt
    assert "оценка +0.30" in prompt


def test_none_negative_and_out_of_range_add_no_line() -> None:
    for ply in (None, -1, 27, 999):
        prompt = build_chat_prompt(
            "FACTS", "Почему плохо?", analyses=ANALYSES, selected_ply=ply
        )
        assert "Пользователь" not in prompt
        assert "Re8" not in prompt
        assert "Nf3" not in prompt


def test_zero_means_start_position() -> None:
    prompt_ru = build_chat_prompt(
        "FACTS", "Что дальше?", analyses=ANALYSES, selected_ply=0
    )
    prompt_en = build_chat_prompt(
        "FACTS",
        "What is next?",
        analyses=ANALYSES,
        selected_ply=0,
        language="en",
    )

    assert "Пользователь смотрит на стартовую позицию." in prompt_ru
    assert "The user is looking at the starting position." in prompt_en
    assert "ход 13" not in prompt_ru


def test_context_is_not_part_of_system_prompt() -> None:
    prompt = build_chat_prompt(
        "FACTS", "Почему плохо?", analyses=ANALYSES, selected_ply=26
    )

    assert "Re8" in prompt
    assert "Re8" not in CHAT_SYSTEM_PROMPT
    assert "Пользователь сейчас смотрит" not in CHAT_SYSTEM_PROMPT


def test_format_selected_ply_context_directly() -> None:
    assert format_selected_ply_context(0, []) == (
        "Пользователь смотрит на стартовую позицию."
    )
    assert format_selected_ply_context(None, ANALYSES) == ""
    assert format_selected_ply_context(-5, ANALYSES) == ""
    assert format_selected_ply_context(26, []) == ""
    assert "13-й ход чёрных" in format_selected_ply_context(26, ANALYSES)


def test_chat_system_prompt_explains_reasons() -> None:
    assert "объясняй причины" in CHAT_SYSTEM_PROMPT.lower()
    assert "потому что" in CHAT_SYSTEM_PROMPT
    assert "Слабое поле f6" in CHAT_SYSTEM_PROMPT


async def test_prompt_hash_includes_selected_ply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Контекст хода меняет prompt_hash; None и вне анализа — не меняют."""

    class EchoLLM(BaseLLMClient):
        def __init__(self) -> None:
            super().__init__(model="unit-model")

        async def complete(
            self, prompt: str, system_prompt: str | None = None
        ) -> LLMResult:
            return LLMResult(
                text="e4",
                usage=LLMUsage(
                    provider="unit", model="unit-model", tokens_in=1, tokens_out=1
                ),
            )

    hashes: list[str] = []

    async def fake_lookup(
        _session: object,
        *,
        fen: str,
        level: str,
        prompt_hash_value: str,
        settings: object = None,
    ) -> None:
        hashes.append(prompt_hash_value)
        return None

    async def fake_store(_session: object, **kwargs: object) -> bool:
        return False

    async def fake_load_game(_session: object, _game_id: int) -> SimpleNamespace:
        return SimpleNamespace(pgn=PGN)

    async def fake_load_analyses(
        _session: object, _game: object, _parsed: object
    ) -> list[MoveAnalysis]:
        return ANALYSES

    monkeypatch.setattr("app.chat.service.lookup", fake_lookup)
    monkeypatch.setattr("app.chat.service.store", fake_store)
    monkeypatch.setattr("app.chat.service.load_game", fake_load_game)
    monkeypatch.setattr("app.chat.service.load_analyses", fake_load_analyses)
    monkeypatch.setattr(
        "app.chat.service.build_fact_package", lambda *_: "FACTS"
    )

    for ply in (None, 99, 26):
        await answer_question(
            object(),  # type: ignore[arg-type]  # сессия не нужна: БД замокана
            1,
            "club",
            "Почему плохо?",
            client=EchoLLM(),
            selected_ply=ply,
        )

    assert hashes[0] == hashes[1]
    assert hashes[0] != hashes[2]
