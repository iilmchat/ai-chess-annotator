"""Тесты промптов и «пакета фактов» (T-014)."""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

import pytest

from app.cache.semantic import make_prompt_hash
from app.chat.service import CHAT_SYSTEM_PROMPT, load_chat_system_prompt
from app.engine.models import EngineLine, MoveAnalysis
from app.errors import InvalidLevelError
from app.ingestion import parse_pgn
from app.llm.prompts import (
    KEY_MOTIF_IMPORTANCE,
    MAX_FACT_PACKAGE_TOKENS,
    MAX_KEY_MOMENTS,
    MAX_KEY_MOMENTS_BY_LEVEL,
    MOVES_HEAD_KEEP,
    MOVES_TAIL_KEEP,
    MOVES_TRUNCATED_NOTE,
    NO_MOVE_DATA_INSTRUCTION,
    PV_LEVELS,
    _select_key_moments,
    build_fact_package,
)
from app.llm.tokens import estimate_tokens
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
        classification=classification,
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


def _long_pgn(cycles: int = 50) -> str:
    """Легальная «карусель» коней: 2 полных хода (4 хода) за цикл."""
    moves: list[str] = []
    number = 1
    for _ in range(cycles):
        moves.append(f"{number}. Nf3 Nf6")
        number += 1
        moves.append(f"{number}. Ng1 Ng8")
        number += 1
    return " ".join(moves) + " 1/2-1/2"


def _long_analyses(
    game: Any, key_plies: tuple[int, ...] = ()
) -> list[MoveAnalysis]:
    """Анализ всех ходов; ``key_plies`` — будущие ключевые моменты."""
    analyses = [
        _analysis(ply, move.san, "g1f3", delta=5)
        for ply, move in enumerate(game.moves, start=1)
    ]
    for ply in key_plies:
        analyses[ply - 1] = _analysis(
            ply,
            analyses[ply - 1].played_move_san,
            "g1f3",
            delta=400,
            classification="blunder",
        )
    return analyses


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


# --- промпт чата (T-106/KI-058) -------------------------------------------


def test_chat_prompt_russian_is_default_constant() -> None:
    assert load_chat_system_prompt() == CHAT_SYSTEM_PROMPT
    assert load_chat_system_prompt("ru") == CHAT_SYSTEM_PROMPT


def test_chat_prompt_english_exists_and_has_rules() -> None:
    text = load_chat_system_prompt("en")

    assert len(text) > 200
    assert "SAN" in text
    assert "Markdown" in text
    assert "Do not invent" in text
    assert "system instructions" in text
    assert "English" in text


def test_chat_prompt_differs_by_language() -> None:
    assert load_chat_system_prompt("en") != CHAT_SYSTEM_PROMPT


def test_chat_prompt_hash_differs_by_language() -> None:
    """Разный системный промпт → разный prompt_hash (кэш не смешивается)."""
    ru_hash = make_prompt_hash(
        "club", "model", CHAT_SYSTEM_PROMPT, question="Who played white?"
    )
    en_hash = make_prompt_hash(
        "club",
        "model",
        load_chat_system_prompt("en"),
        question="Who played white?",
    )

    assert ru_hash != en_hash


# --- объяснение причин (итерация 38) ---------------------------------------


def _read_prompt_resource(name: str) -> str:
    """Прочитать .txt-промпт напрямую (БД может содержать старую версию)."""
    return (
        resources.files("app.prompts")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("level", sorted(PROMPT_LEVELS))
def test_russian_prompt_file_requires_explanation(level: str) -> None:
    text = _read_prompt_resource(f"{level}.txt")

    assert "ОБЪЯСНЯЙ ПРИЧИНЫ" in text, (
        "каждый commentary-промпт обязан требовать объяснение причин"
    )
    assert "потому что" in text, "в промпте нужен пример объяснения «почему»"


@pytest.mark.parametrize("level", sorted(PROMPT_LEVELS))
def test_english_prompt_file_requires_explanation(level: str) -> None:
    text = _read_prompt_resource(f"{level}_en.txt")

    assert "ALWAYS EXPLAIN WHY" in text, (
        "every commentary prompt must require an explanation"
    )
    assert "because" in text, "the prompt needs an example explanation"


def test_chat_prompt_english_requires_explanation() -> None:
    text = _read_prompt_resource("chat_en.txt")

    assert "ALWAYS EXPLAIN WHY" in text
    assert "because" in text


# --- пакет фактов ---------------------------------------------------------


def test_fact_package_contains_metadata_and_moves() -> None:
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, _short_analyses(), "club")

    assert "Белые: Test White" in package
    assert "Чёрные: Test Black" in package
    assert "Событие: Test Event" in package
    assert "Результат: 1-0" in package
    assert "Ходов: 3" in package
    assert "1. d4 Nf6 2. c4 e6 3. Nc3 Bb4" in package
    assert "Уровень объяснения: club" in package


@pytest.mark.parametrize("level", sorted(PROMPT_LEVELS))
def test_fact_package_has_no_ply_wording(level: str) -> None:
    """Итерация 46: в пакете — «Ход N белых/чёрных», без слова «полуход»."""
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, _short_analyses(), level)

    assert "полуход" not in package.lower()
    assert "Ход 2 белых: c4" in package


def test_fact_package_uses_move_number_for_black() -> None:
    game = parse_pgn(SHORT_PGN)
    analysis = _analysis(
        2, "Nf6", "g8f6", delta=200, classification="blunder"
    )

    package = build_fact_package(game, [analysis], "club")

    assert "## 1. Ход 1 чёрных: Nf6" in package
    assert "(полуход 2)" not in package
    assert "полуход" not in package.lower()


async def test_generated_prompt_has_no_ply_wording() -> None:
    game = parse_pgn(SHORT_PGN)
    system_prompt = await load_system_prompt("club")
    package = build_fact_package(game, _short_analyses(), "club")

    prompt = f"{system_prompt}\n{package}"

    assert "полуход" not in prompt.lower()


def test_fact_package_without_analyses_marks_engine_data_unavailable() -> None:
    """T-087: внешний PGN без анализа не должен выдавать оценки за факты."""
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, level="club")

    assert "# Партия" in package
    assert "# Ходы партии (SAN)" in package
    assert "# Ключевые моменты" in package
    assert "Анализ движка недоступен" in package
    assert "Оценок и лучших ходов движка в пакете нет" in package
    assert "Ключевых моментов не найдено" not in package
    assert "Все ходы, оценки и мотивы выше получены от движка" not in package


def test_fact_package_without_best_move_is_honest() -> None:
    """T-092: из Lichess PGN известна оценка, но не лучший ход."""
    game = parse_pgn(SHORT_PGN)
    analysis = _short_analyses()[0].model_copy(
        update={
            "best_move_known": False,
            "delta_cp": 120,
            "classification": "mistake",
            "lines": [],
        }
    )

    package = build_fact_package(game, [analysis], "club")

    assert "Потеря относительно лучшей линии: 120 cp" in package
    assert "Лучший ход движка неизвестен: в PGN есть только оценка" in package
    assert "Лучший ход движка: d4" not in package


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


# --- сокращение длинных партий (итерация 47) -------------------------------


@pytest.fixture(scope="module")
def long_game() -> Any:
    """1200 ходов: полный пакет фактов не влезает в 2048 токенов."""
    return parse_pgn(_long_pgn(cycles=300))


def test_default_fact_package_limit_is_2048() -> None:
    assert MAX_FACT_PACKAGE_TOKENS == 2048
    assert MOVES_HEAD_KEEP == MOVES_TAIL_KEEP == 10


def test_short_game_is_not_marked_as_truncated() -> None:
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, _short_analyses(), "club")

    assert MOVES_TRUNCATED_NOTE not in package
    assert "…" not in package


def test_long_game_package_fits_default_token_limit(long_game: Any) -> None:
    """1200 ходов: пакет обрезан до 2048 токенов, моменты на месте."""
    assert long_game.ply_count == 1200
    analyses = _long_analyses(long_game, key_plies=(1, 600, 1200))

    package = build_fact_package(long_game, analyses, "beginner")

    assert estimate_tokens(package) <= MAX_FACT_PACKAGE_TOKENS
    assert MOVES_TRUNCATED_NOTE in package
    moves_line = next(
        line
        for line in package.splitlines()
        if line.startswith("1. Nf3 Nf6")
    )
    assert "…" in moves_line
    assert "300. Ng1 Ng8" in moves_line, "ход ключевого момента потерян"
    assert moves_line.endswith("600. Ng1 Ng8"), "конец партии потерян"
    assert "150." not in moves_line, "середина партии не сокращена"
    headings = [line for line in package.splitlines() if line.startswith("## ")]
    assert len(headings) == 3, "ключевые моменты должны сохраняться"


def test_long_game_package_honours_custom_limit_and_keeps_moments() -> None:
    game = parse_pgn(_long_pgn(cycles=50))
    analyses = _long_analyses(game, key_plies=(1, 100, 200))

    package = build_fact_package(game, analyses, "club", max_tokens=500)

    assert estimate_tokens(package) <= 500
    assert "…" in package
    moves_line = next(
        line
        for line in package.splitlines()
        if line.startswith("1. Nf3 Nf6")
    )
    assert "50. Ng1 Ng8" in moves_line
    assert "100. Ng1 Ng8" in moves_line
    headings = [line for line in package.splitlines() if line.startswith("## ")]
    assert len(headings) == 3


def test_long_game_without_analyses_is_also_truncated(long_game: Any) -> None:
    """Внешний PGN без анализа движка тоже не должен раздувать контекст."""
    package = build_fact_package(long_game, level="club", max_tokens=1500)

    assert estimate_tokens(package) <= 1500
    assert MOVES_TRUNCATED_NOTE in package
    assert "Анализ движка недоступен" in package


def test_fact_package_rejects_non_positive_max_tokens() -> None:
    game = parse_pgn(SHORT_PGN)

    with pytest.raises(ValueError, match="max_tokens"):
        build_fact_package(game, _short_analyses(), "club", max_tokens=0)


def test_gm_fact_package_keeps_pv_and_more_moments() -> None:
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, _short_analyses(), "gm")

    assert "PV (до 8 ходов):" in package
    assert "gm" in PV_LEVELS


def test_club_prompt_has_few_shot_examples() -> None:
    text = _read_prompt_resource("club.txt")

    assert "few-shot" in text.casefold()
    assert "Хорошо:" in text
    assert "НЕТ ни одного конкретного хода" in text, (
        "примеры не должны содержать SAN-ходы: 3B-модель копирует их в ответ"
    )
    assert "ПЕРЕД ОТВЕТОМ ПРОВЕРЬ" in text


#: Фразы из few-shot примеров, которые локальная 3B-модель копировала в
#: комментарий (итерация 48). Их не должно быть ни в одном промпте.
_COPIED_FEW_SHOT_PHRASES = (
    "отвести слона",
    "конь в центр",
    "слон уходит с защиты короля",
    "12-й ход",
    "18-й ход",
    "31 cp",
    "250 cp",
    "retreat the bishop",
    "knight to the centre",
    "bishop leaves the king",
)


@pytest.mark.parametrize(
    "name",
    [f"{level}.txt" for level in sorted(PROMPT_LEVELS)]
    + [f"{level}_en.txt" for level in sorted(PROMPT_LEVELS)],
)
def test_prompt_files_have_no_copied_few_shot_phrases(name: str) -> None:
    # Переносы строк не должны ломать поиск фразы: «НЕ\n упоминай».
    text = " ".join(_read_prompt_resource(name).casefold().split())

    for phrase in _COPIED_FEW_SHOT_PHRASES:
        assert phrase.casefold() not in text, (
            f"{name}: фраза «{phrase}» из few-shot примера провоцирует "
            "копирование хода/фигуры (итерация 48)"
        )


@pytest.mark.parametrize(
    ("name", "invent", "pieces", "general"),
    [
        (
            "club.txt",
            "не выдумывай ходы",
            "не упоминай фигуры",
            "общие слова о позиции",
        ),
        (
            "club_en.txt",
            "do not invent moves",
            "do not mention pieces",
            "general words about the position",
        ),
    ],
)
def test_club_prompt_forbids_inventing_moves_without_facts(
    name: str, invent: str, pieces: str, general: str
) -> None:
    # Переносы строк не должны ломать поиск фразы: «do NOT invent\n moves».
    text = " ".join(_read_prompt_resource(name).casefold().split())

    assert invent in text
    assert pieces in text
    assert general in text


def test_club_few_shot_block_has_no_moves_or_pieces() -> None:
    text = _read_prompt_resource("club.txt")
    block = text.split("ПРИМЕРЫ ФОРМАТА", 1)[1].split(
        "ПЕРЕД ОТВЕТОМ", 1
    )[0]

    assert not re.search(r"\b[a-h][1-8]\b", block), (
        "в few-shot примерах не должно быть клеток/SAN-ходов"
    )
    for piece in ("слон", "конь", "ферзь", "ладья"):
        assert piece not in block.casefold()


def test_club_en_few_shot_block_has_no_moves_or_pieces() -> None:
    text = _read_prompt_resource("club_en.txt")
    block = text.split("FEW-SHOT FORMAT EXAMPLES", 1)[1].split(
        "BEFORE YOU ANSWER", 1
    )[0]

    assert not re.search(r"\b[a-h][1-8]\b", block), (
        "few-shot examples must not contain squares or SAN moves"
    )
    for piece in ("bishop", "knight", "queen", "rook"):
        assert piece not in block.casefold()


def test_fact_package_without_analysis_forbids_inventing_moves() -> None:
    game = parse_pgn(SHORT_PGN)

    package = build_fact_package(game, level="club")

    assert NO_MOVE_DATA_INSTRUCTION in package
    assert "НЕ выдумывай ходы" in package
    assert "НЕ упоминай фигуры" in package
    assert "общие слова о позиции" in package


def test_generated_prompt_without_analysis_forbids_inventing_moves() -> None:
    game = parse_pgn(SHORT_PGN)
    system_prompt = _read_prompt_resource("club.txt")
    package = build_fact_package(game, level="club")

    prompt = f"{system_prompt}\n{package}"

    assert "НЕ выдумывай ходы" in prompt
    assert "НЕ упоминай фигуры" in prompt


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
