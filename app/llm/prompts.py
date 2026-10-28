"""Сборка «пакета фактов» для LLM (T-014).

Пакет — это текстовый контекст с **проверенными** данными: метаданные партии,
ходы, ключевые моменты (классификация, оценка, мотивы, лучший ход, PV,
рекомендация Policy под уровень). LLM не должна ничего «достраивать» — все
факты для текста берутся отсюда.

Почему текст, а не JSON: модель-инструкция лучше держит структуру, когда
заголовки и списки читаются как конспект; JSON провоцирует «продолжение
схемы» и лишние поля. Формат стабилен и покрыт тестами.
"""

from __future__ import annotations

import chess

from app.engine.models import EngineLine, MoveAnalysis
from app.engine.policy import choose_recommended_move
from app.errors import InvalidLevelError
from app.ingestion.models import ParsedGame
from app.prompts import PROMPT_LEVELS

__all__ = [
    "KEY_MOTIF_IMPORTANCE",
    "MAX_KEY_MOMENTS",
    "PV_LIMIT",
    "build_fact_package",
]

#: Сколько ключевых моментов максимум попадает в пакет.
MAX_KEY_MOMENTS = 10

#: Мотив с такой важностью делает ход «ключевым» даже без ошибки.
KEY_MOTIF_IMPORTANCE = 4

#: Сколько полуходов PV показываем (дальше модель начинает «додумывать»).
PV_LIMIT = 8

#: Классы, которые точно заслуживают комментария.
_KEY_CLASSES = frozenset({"blunder", "mistake", "inaccuracy"})


def build_fact_package(
    game: ParsedGame, analyses: list[MoveAnalysis], level: str
) -> str:
    """Собрать пакет фактов для генерации комментария.

    Args:
        game: разобранная партия (`ParsedGame`).
        analyses: результат `analyze_game_moves` (по одному на полуход).
        level: ``beginner`` / ``club`` / ``gm`` — влияет на рекомендации.

    Returns:
        Текст пакета: метаданные, ходы партии, ключевые моменты.

    Raises:
        InvalidLevelError: неизвестный уровень.
    """
    if level not in PROMPT_LEVELS:
        expected = ", ".join(sorted(PROMPT_LEVELS))
        raise InvalidLevelError(
            f"Неизвестный уровень {level!r}; ожидается один из: {expected}"
        )

    lines: list[str] = ["# Партия"]
    meta = game.metadata
    lines.append(f"Белые: {meta.white or 'неизвестно'}")
    lines.append(f"Чёрные: {meta.black or 'неизвестно'}")
    if meta.event:
        lines.append(f"Событие: {meta.event}")
    if meta.date:
        lines.append(f"Дата: {meta.date}")
    lines.append(f"Результат: {meta.result or 'неизвестен'}")
    if game.eco_code:
        lines.append(f"ECO: {game.eco_code}")
    full_moves = (game.ply_count + 1) // 2
    lines.append(f"Ходов: {full_moves} ({game.ply_count} полуходов)")
    lines.append(f"Уровень объяснения: {level}")
    lines.append("")

    lines.append("# Ходы партии (SAN)")
    lines.append(_format_game_moves(game))
    lines.append("")

    key_moments = _select_key_moments(analyses)
    lines.append(
        f"# Ключевые моменты (до {MAX_KEY_MOMENTS}, по убыванию потери)"
    )
    if not key_moments:
        lines.append("Ключевых моментов не найдено.")
    for number, analysis in enumerate(key_moments, start=1):
        lines.extend(_format_key_moment(number, analysis, game, level))
        lines.append("")

    lines.append(
        "Все ходы, оценки и мотивы выше получены от движка и проверены. "
        "Не добавляй ходы и оценки, которых здесь нет."
    )
    return "\n".join(lines).strip() + "\n"


def _select_key_moments(analyses: list[MoveAnalysis]) -> list[MoveAnalysis]:
    """Ошибки + тактически важные мотивы, не больше `MAX_KEY_MOMENTS`."""
    flagged = [
        analysis
        for analysis in analyses
        if analysis.classification in _KEY_CLASSES
        or any(motif.importance >= KEY_MOTIF_IMPORTANCE for motif in analysis.motifs)
    ]
    if not flagged:
        # Партия без ошибок: показываем самые «острые» ходы по дельте.
        flagged = list(analyses)
    flagged.sort(
        key=lambda item: (
            item.delta_cp,
            max((motif.importance for motif in item.motifs), default=0),
        ),
        reverse=True,
    )
    return flagged[:MAX_KEY_MOMENTS]


def _format_key_moment(
    number: int, analysis: MoveAnalysis, game: ParsedGame, level: str
) -> list[str]:
    ply = analysis.ply
    move_number = (ply + 1) // 2
    side = "белые" if ply % 2 == 1 else "чёрные"
    prefix = f"{move_number}." if ply % 2 == 1 else f"{move_number}..."

    lines = [f"## {number}. Полуход {ply} ({prefix}), {side}: {analysis.played_move_san}"]
    lines.append(f"- Оценка после хода: {_format_eval(analysis)}")
    lines.append(
        f"- Лучший ход движка: {analysis.best_move_san}; "
        f"потеря {analysis.delta_cp} cp; класс: {analysis.classification}"
    )

    fen_before = _fen_before(game, ply)
    recommended = _recommended(analysis, fen_before, level)
    if recommended is not None:
        lines.append(
            f"- Рекомендация для {level}: "
            f"{_uci_to_san(fen_before, recommended.move_uci)}"
        )

    pv = _pv_san(fen_before, analysis.lines)
    if pv:
        lines.append(f"- PV (до {PV_LIMIT} ходов): {' '.join(pv)}")

    if analysis.motifs:
        lines.append("- Мотивы:")
        for motif in analysis.motifs:
            lines.append(f"  - [{motif.importance}] {motif.type}: {motif.description}")
    return lines


def _format_eval(analysis: MoveAnalysis) -> str:
    """Оценка после хода — с точки зрения белых (для текста комментария)."""
    if analysis.mate_after is not None:
        side = "белых" if analysis.mate_after > 0 else "чёрных"
        return f"мат в {abs(analysis.mate_after)} за {side}"
    white_sign = 1 if analysis.ply % 2 == 1 else -1
    cp = analysis.eval_after * white_sign
    return f"{cp / 100:+.2f} ({cp:+d} cp) за белых"


def _fen_before(game: ParsedGame, ply: int) -> str:
    """Позиция перед полуходом ``ply`` (1-based)."""
    if ply <= 1:
        return game.initial_fen
    return game.moves[ply - 2].fen


def _recommended(
    analysis: MoveAnalysis, fen_before: str, level: str
) -> EngineLine | None:
    """Рекомендация Policy; ``None``, если линий нет или ход посчитать нельзя."""
    if not analysis.lines:
        return None
    try:
        return choose_recommended_move(analysis.lines, level, fen=fen_before)
    except ValueError:
        return None


def _pv_san(
    fen: str, lines: list[EngineLine], *, limit: int = PV_LIMIT
) -> list[str]:
    """PV лучшей линии в SAN; обрывается, если ход не проигрывается."""
    if not lines:
        return []
    pv_uci = lines[0].pv
    board = chess.Board(fen)
    result: list[str] = []
    for uci in list(pv_uci)[:limit]:
        try:
            move = board.parse_uci(str(uci))
            result.append(board.san(move))
            board.push(move)
        except ValueError:
            break
    return result


def _uci_to_san(fen: str, uci: str) -> str:
    """UCI → SAN; при неожиданной ошибке возвращает UCI как есть."""
    try:
        board = chess.Board(fen)
        return board.san(board.parse_uci(uci))
    except ValueError:
        return uci


def _format_game_moves(game: ParsedGame) -> str:
    """Ходы партии в формате `1. d4 Nf6 2. c4 …`."""
    parts: list[str] = []
    for move in game.moves:
        if move.ply % 2 == 1:
            parts.append(f"{(move.ply + 1) // 2}.")
        parts.append(move.san)
    return " ".join(parts) if parts else "нет ходов"
