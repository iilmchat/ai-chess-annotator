"""Тесты промптов и «пакета фактов» (T-014)."""

from __future__ import annotations

import pytest

from app.engine.models import EngineLine, MoveAnalysis
from app.errors import InvalidLevelError
from app.ingestion import parse_pgn
from app.llm.prompts import (
    KEY_MOTIF_IMPORTANCE,
    MAX_KEY_MOMENTS,
    MAX_KEY_MOMENTS_BY_LEVEL,
    PV_LEVELS,
    _select_key_moments,
    build_fact_package,
)
from app.motifs.models import Motif
from app.prompts import (
    PROMPT_LEVELS,
    load_system_prompt,
    resolve_language,
)

SHORT_PGN = """[White "Test White"]
[Black "Test Black"]
[Event "Test Event"]
[Date "2026.03.30"]
[Result "1-0"]

1. d4 Nf6 2. c4 e6 3. Nc3 Bb4 1-0
"""


def _line(move_uci: str = "e2e4", score_cp: int = 30) -> EngineLine:
    return EngineLine(
        multipv=1,
        depth=12,
        score_cp=score_cp,
        move_uci=move_uci,
        pv=[move_uci, "e7e5", "g1f3"],
    )


def _analysis(
    ply: int,
    san: str,
    uci: str,
    *,
    delta: int = 10,
    classification: str = "good",
    motifs: list[Motif] | None = None,
    eval_after: int = 20,
) -> MoveAnalysis:
    return MoveAnalysis(
        ply=ply,
        played_move_san=san,
        played_move_uci=uci,
        eval_before=eval_after + delta,
        eval_after=eval_after,
        best_move_uci="e2e4",
        best_move_san="e4",
        delta_cp=delta,
        classification=classification,  # type: ignore[arg-type]
        engine_depth=12,
        lines=[_line()],
        motifs=motifs or [],
    )


def _short_analyses() -> list[MoveAnalysis]:
    motif = Motif(
        type="king_safety",
        description="Король белых в центре (e1), ферзи на доске",
        importance=4,
    )
    return [
        _analysis(1, "d4", "d2d4", delta=5, eval_after=22),
        _analysis(2, "Nf6", "g8f6", delta=0, classification="best"),
        _analysis(
            3,
            "c4",
            "c2c4",
            delta=150,
            classification="mistake",
            motifs=[motif],
        ),
    ]


# --- файлы промптов -------------------------------------------------------


@pytest.mark.parametrize("level", sorted(PROMPT_LEVELS))
async def test_prompt_exists_and_has_rules(level: str) -> None:
    text = await load_system_prompt(level)

    assert len(text) > 300
    assert "SAN" in text
    assert "ЗАПРЕЩЕНО" in text
    assert "Markdown" in text, "промпт обязан запрещать разметку внутри ходов"


async def test_prompt_unknown_level_raises() -> None:
    with pytest.raises(InvalidLevelError):
        await load_system_prompt("master")


@pytest.mark.parametrize("level", sorted(PROMPT_LEVELS))
async def test_english_prompt_exists_and_has_rules(level: str) -> None:
    """T-106: для каждого уровня есть английский prompt-файл."""
    text = await load_system_prompt(level, language="en")

    assert len(text) > 300
    assert "SAN" in text
    assert "Markdown" in text
    assert "FORBIDDEN" in text or "STRICT RULES" in text
    assert "English" in text


async def test_prompts_differ_by_language() -> None:
    ru = await load_system_prompt("club")
    en = await load_system_prompt("club", language="en")
    assert ru != en


async def test_unknown_language_raises() -> None:
    with pytest.raises(ValueError, match="языка"):
        await load_system_prompt("club", language="de")


def test_resolve_language_precedence() -> None:
    assert resolve_language("en", "ru-RU", "ru") == "en"
    assert resolve_language(None, "en-US,en;q=0.9", "ru") == "en"
    assert resolve_language(None, "ru-RU,ru;q=0.9", "en") == "ru"
    assert resolve_language(None, None, "en") == "en"
    assert resolve_language("de", "fr-FR", "ru") == "ru"


async def test_prompts_are_distinct() -> None:
    beginner = await load_system_prompt("beginner")
    club = await load_system_prompt("club")
    gm = await load_system_prompt("gm")

    assert len({beginner, club, gm}) == 3


# --- пакет фактов ---------------------------------------------------------


def test_fact_package_contains_metadata_and_moves() -> None:
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, _short_analyses(), "club")

    assert "Белые: Test White" in package
    assert "Чёрные: Test Black" in package
    assert "Событие: Test Event" in package
    assert "Результат: 1-0" in package
    assert "Ходов: 3 (6 полуходов)" in package
    assert "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4" in package
    assert "Уровень объяснения: club" in package


def test_fact_package_lists_key_moments_with_motifs_and_recommendation() -> None:
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, _short_analyses(), "club")

    assert "# Ключевые моменты" in package
    assert "mistake" in package
    assert "потеря 150 cp" in package
    assert "Рекомендация для club" in package
    assert "king_safety" in package
    assert "PV (до 8 ходов):" not in package, (
        "для club PV отключены: они перегружали контекст (KI-030)"
    )
    assert "**" not in package, "в пакете не должно быть Markdown-разметки ходов"


def test_fact_package_respects_key_moment_limit() -> None:
    game = parse_pgn(
        "1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 5. Nf3 Nf6 "
        "6. Ng1 Ng8 7. Nf3 Nf6 8. Ng1 Ng8 1/2-1/2"
    )
    analyses = [
        _analysis(
            ply,
            move.san,
            "g1f3",
            delta=200,
            classification="blunder",
        )
        for ply, move in enumerate(game.moves, start=1)
    ]
    assert len(analyses) >= 15

    package = build_fact_package(game, analyses, "beginner")

    headings = [line for line in package.splitlines() if line.startswith("## ")]
    assert len(headings) == MAX_KEY_MOMENTS


def test_club_fact_package_is_compact() -> None:
    game = parse_pgn(
        "1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 5. Nf3 Nf6 "
        "6. Ng1 Ng8 7. Nf3 Nf6 8. Ng1 Ng8 1/2-1/2"
    )
    analyses = [
        _analysis(
            ply,
            move.san,
            "g1f3",
            delta=100 + ply * 10,
            classification="blunder",
        )
        for ply, move in enumerate(game.moves, start=1)
    ]

    package = build_fact_package(game, analyses, "club")

    headings = [line for line in package.splitlines() if line.startswith("## ")]
    assert len(headings) == MAX_KEY_MOMENTS_BY_LEVEL["club"] == 3
    assert "PV (до" not in package
    assert "club" not in PV_LEVELS


def test_gm_fact_package_keeps_pv_and_more_moments() -> None:
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, _short_analyses(), "gm")

    assert "PV (до 8 ходов):" in package
    assert "gm" in PV_LEVELS


async def test_club_prompt_has_few_shot_examples() -> None:
    text = await load_system_prompt("club")

    assert "few-shot" in text.lower()
    assert "Хорошо:" in text
    assert "НЕТ конкретных ходов" in text, (
        "примеры не должны содержать SAN-ходы: 4B-модель копирует их в ответ"
    )
    assert "ПЕРЕД ОТВЕТОМ ПРОВЕРЬ" in text


def test_select_key_moments_prioritises_delta() -> None:
    analyses = [
        _analysis(1, "d4", "d2d4", delta=5),
        _analysis(2, "Nf6", "g8f6", delta=300, classification="blunder"),
        _analysis(3, "c4", "c2c4", delta=120, classification="mistake"),
    ]

    selected = _select_key_moments(analyses)

    assert [item.ply for item in selected] == [2, 3]


def test_key_motif_importance_is_high() -> None:
    assert KEY_MOTIF_IMPORTANCE >= 4


def test_fact_package_rejects_unknown_level() -> None:
    game = parse_pgn(SHORT_PGN)

    with pytest.raises(InvalidLevelError):
        build_fact_package(game, _short_analyses(), "master")
