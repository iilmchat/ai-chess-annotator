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

from collections.abc import Sequence

import chess

from app.engine.models import EngineLine, MoveAnalysis
from app.engine.policy import choose_recommended_move
from app.errors import InvalidLevelError
from app.ingestion.models import ParsedGame
from app.llm.tokens import estimate_tokens
from app.prompts import PROMPT_LEVELS

__all__ = [
    "KEY_MOTIF_IMPORTANCE",
    "MAX_FACT_PACKAGE_TOKENS",
    "MAX_KEY_MOMENTS",
    "MAX_KEY_MOMENTS_BY_LEVEL",
    "MOVES_HEAD_KEEP",
    "MOVES_TAIL_KEEP",
    "MOVES_TRUNCATED_NOTE",
    "NO_MOVE_DATA_INSTRUCTION",
    "PV_LEVELS",
    "PV_LIMIT",
    "build_fact_package",
]

#: Лимит пакета фактов в токенах (итерация 47). Без него треть «золотого
#: датасета» пропускалась в `train_lora.py` как `skipped_too_long`.
MAX_FACT_PACKAGE_TOKENS = 2048

#: Сколько ходов (позиций после хода) оставлять в начале длинной партии.
MOVES_HEAD_KEEP = 10

#: Сколько ходов оставлять в конце длинной партии.
MOVES_TAIL_KEEP = 10

#: Пометка о сокращении: модель не должна «достраивать» середину партии.
MOVES_TRUNCATED_NOTE = (
    "Партия сокращена по лимиту токенов: показаны начало, конец и ходы "
    "из ключевых моментов."
)

#: Инструкция для пакета без проверенных данных о ходе (итерация 48).
#: Локальная 3B-модель копировала фигуры из few-shot примеров и выдумывала
#: ходы, когда в пакете не было анализа; теперь такой случай описан явно.
NO_MOVE_DATA_INSTRUCTION = (
    "Если в пакете фактов нет информации о ходе, НЕ выдумывай ходы и "
    "НЕ упоминай фигуры. Пиши только общие слова о позиции."
)

#: Сколько ключевых моментов максимум попадает в пакет (базовый лимит).
MAX_KEY_MOMENTS = 10

#: Лимит по уровням (KI-030): локальной 4B-модели для `club` нужен
#: короткий пакет — только три самые крупные потери, без PV.
MAX_KEY_MOMENTS_BY_LEVEL: dict[str, int] = {
    "beginner": MAX_KEY_MOMENTS,
    "club": 3,
    "gm": MAX_KEY_MOMENTS,
}

#: Уровни, для которых в пакет попадают PV-линии. Для `club` они
#: перегружали контекст и провоцировали ошибки атрибуции (KI-030).
PV_LEVELS = frozenset({"gm"})

#: Мотив с такой важностью делает ход «ключевым» даже без ошибки.
KEY_MOTIF_IMPORTANCE = 4

#: Сколько ходов PV показываем (дальше модель начинает «додумывать»).
PV_LIMIT = 8

#: Классы, которые точно заслуживают комментария.
_KEY_CLASSES = frozenset({"blunder", "mistake", "inaccuracy"})


def build_fact_package(
    game: ParsedGame,
    analyses: Sequence[MoveAnalysis] | None = None,
    level: str = "club",
    max_tokens: int = MAX_FACT_PACKAGE_TOKENS,
) -> str:
    """Собрать пакет фактов для генерации комментария.

    Длинная партия не раздувает контекст: если пакет не помещается в
    ``max_tokens``, из списка SAN остаются первые и последние ходы плюс
    ходы ключевых моментов (итерация 47). Ключевые моменты сохраняются,
    пока хватает лимита.

    Args:
        game: разобранная партия (`ParsedGame`).
        analyses: результат `analyze_game_moves` (по одному на ход).
            ``None``/пустой список — внешний PGN без анализа движка
            (T-087): пакет содержит только проверенные ходы и метаданные.
        level: ``beginner`` / ``club`` / ``gm`` — влияет на рекомендации.
        max_tokens: лимит пакета в токенах (оценка `estimate_tokens`,
            ~4 символа на токен).

    Returns:
        Текст пакета: метаданные, ходы партии, ключевые моменты.

    Raises:
        InvalidLevelError: неизвестный уровень.
        ValueError: ``max_tokens < 1`` или пакет не помещается даже без
            ходов и ключевых моментов.
    """
    if level not in PROMPT_LEVELS:
        expected = ", ".join(sorted(PROMPT_LEVELS))
        raise InvalidLevelError(
            f"Неизвестный уровень {level!r}; ожидается один из: {expected}"
        )
    if max_tokens < 1:
        raise ValueError(
            f"max_tokens должен быть ≥ 1, получено {max_tokens}"
        )

    analysis_list = list(analyses or [])
    moment_limit = MAX_KEY_MOMENTS_BY_LEVEL[level]
    include_pv = level in PV_LEVELS
    key_moments = (
        _select_key_moments(analysis_list, limit=moment_limit)
        if analysis_list
        else []
    )

    def render(
        moves_text: str,
        moments: Sequence[MoveAnalysis],
        *,
        trimmed: bool,
    ) -> str:
        return _render_package(
            game,
            level=level,
            moves_text=moves_text,
            key_moments=moments,
            has_analysis=bool(analysis_list),
            moment_limit=moment_limit,
            include_pv=include_pv,
            moves_trimmed=trimmed,
        )

    package = render(_format_game_moves(game), key_moments, trimmed=False)
    if estimate_tokens(package) <= max_tokens:
        return package

    # Партия длинная: сужаем окно «начало + конец», пока пакет не влезет.
    # Шаг 2 сохраняет паритет: окно начинается с белых (нечётный ход).
    windows = [window for window in range(MOVES_HEAD_KEEP, 0, -2)]
    windows.append(0)
    for window in windows:
        kept = _kept_plies(
            game,
            key_moments,
            head=min(MOVES_HEAD_KEEP, window),
            tail=min(MOVES_TAIL_KEEP, window),
        )
        package = render(
            _format_game_moves(game, keeps=kept), key_moments, trimmed=True
        )
        if estimate_tokens(package) <= max_tokens:
            return package

    # Даже без ходов не влезло: ключевые моменты уже отсортированы по
    # убыванию потери, поэтому отбрасываем наименее важные с конца.
    remaining = list(key_moments)
    while remaining:
        remaining.pop()
        kept = _kept_plies(game, remaining, head=0, tail=0)
        package = render(
            _format_game_moves(game, keeps=kept), remaining, trimmed=True
        )
        if estimate_tokens(package) <= max_tokens:
            return package

    raise ValueError(
        "Пакет фактов не помещается в "
        f"{max_tokens} токенов даже без ходов и ключевых моментов; "
        "увеличьте max_tokens"
    )


def _render_package(
    game: ParsedGame,
    *,
    level: str,
    moves_text: str,
    key_moments: Sequence[MoveAnalysis],
    has_analysis: bool,
    moment_limit: int,
    include_pv: bool,
    moves_trimmed: bool,
) -> str:
    """Собрать текст пакета из готового списка ходов и ключевых моментов."""
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
    lines.append(f"Ходов: {full_moves}")
    lines.append(f"Уровень объяснения: {level}")
    lines.append("")

    lines.append("# Ходы партии (SAN)")
    lines.append(moves_text)
    if moves_trimmed:
        lines.append(MOVES_TRUNCATED_NOTE)
    lines.append("")

    if not has_analysis:
        # Внешний PGN: анализа движка нет, поэтому нельзя выдавать
        # выдуманные оценки за проверенные факты (T-087).
        lines.append("# Ключевые моменты")
        lines.append(
            "Анализ движка недоступен: партия загружена из внешнего PGN "
            "без анализа ходов."
        )
        lines.append("")
        lines.append(
            "Все ходы выше взяты из PGN и проверены. Оценок и лучших ходов "
            "движка в пакете нет — не выдумывай их."
        )
        lines.append(NO_MOVE_DATA_INSTRUCTION)
        return "\n".join(lines).strip() + "\n"

    lines.append(
        f"# Ключевые моменты (до {moment_limit}, по убыванию потери)"
    )
    if not key_moments:
        lines.append("Ключевых моментов не найдено.")
    for number, analysis in enumerate(key_moments, start=1):
        lines.extend(
            _format_key_moment(
                number, analysis, game, level, include_pv=include_pv
            )
        )
        lines.append("")
    lines.append(
        "Все ходы, оценки и мотивы выше получены от движка и проверены. "
        "Не добавляй ходы и оценки, которых здесь нет."
    )
    lines.append(NO_MOVE_DATA_INSTRUCTION)
    return "\n".join(lines).strip() + "\n"


def _kept_plies(
    game: ParsedGame,
    key_moments: Sequence[MoveAnalysis],
    *,
    head: int,
    tail: int,
) -> set[int]:
    """Ходы для сокращённого списка: начало, конец и ключевые моменты.

    Окна выравниваются по белым: фрагмент не должен начинаться с хода
    чёрных без номера хода.
    """
    plies = [move.ply for move in game.moves]
    if not plies:
        return set()
    known = set(plies)
    kept: set[int] = set(plies[:head]) if head else set()
    if tail:
        kept.update(plies[-tail:])
    for analysis in key_moments:
        partner = analysis.ply - 1 if analysis.ply % 2 == 0 else analysis.ply + 1
        kept.update(
            ply for ply in (analysis.ply, partner) if ply in known
        )
    for ply in sorted(kept):
        if ply % 2 == 0 and ply - 1 in known and ply - 1 not in kept:
            kept.add(ply - 1)
    return kept


def _select_key_moments(
    analyses: list[MoveAnalysis], *, limit: int = MAX_KEY_MOMENTS
) -> list[MoveAnalysis]:
    """Ошибки + тактически важные мотивы, не больше ``limit``."""
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
    return flagged[:limit]


def _format_key_moment(
    number: int,
    analysis: MoveAnalysis,
    game: ParsedGame,
    level: str,
    *,
    include_pv: bool = True,
) -> list[str]:
    ply = analysis.ply
    move_number = (ply + 1) // 2
    side_nom = "белые" if ply % 2 == 1 else "чёрные"
    side_gen = "белых" if ply % 2 == 1 else "чёрных"

    lines = [
        f"## {number}. Ход {move_number} {side_gen}: "
        f"{analysis.played_move_san}"
    ]
    lines.append(f"- Сыграно: {analysis.played_move_san} ({side_nom})")
    lines.append(f"- Оценка после хода (за белых): {_format_eval(analysis)}")
    if analysis.best_move_known:
        lines.append(
            f"- Лучший ход движка: {analysis.best_move_san}; "
            f"потеря {analysis.delta_cp} cp; класс: {analysis.classification}"
        )
    else:
        lines.append(
            f"- Потеря относительно лучшей линии: {analysis.delta_cp} cp; "
            f"класс: {analysis.classification}"
        )
        lines.append(
            "- Лучший ход движка неизвестен: в PGN есть только оценка."
        )

    fen_before = _fen_before(game, ply)
    recommended = _recommended(analysis, fen_before, level)
    if recommended is not None:
        lines.append(
            f"- Рекомендация для {level}: "
            f"{_uci_to_san(fen_before, recommended.move_uci)}"
        )

    if include_pv:
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
    """Позиция перед ходом ``ply`` (1-based)."""
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


def _format_game_moves(
    game: ParsedGame, *, keeps: set[int] | None = None
) -> str:
    """Ходы партии в формате `1. d4 Nf6 2. c4 …`.

    ``keeps`` — какие ходы оставить (``None`` — все). Пропуск между
    оставленными ходами отмечается многоточием; номер печатается только
    перед ходом белых, поэтому фрагмент всегда начинается с белых.
    """
    parts: list[str] = []
    previous: int | None = None
    for move in game.moves:
        if keeps is not None and move.ply not in keeps:
            continue
        if previous is not None and move.ply != previous + 1:
            parts.append("…")
        if move.ply % 2 == 1:
            parts.append(f"{(move.ply + 1) // 2}.")
        parts.append(move.san)
        previous = move.ply
    return " ".join(parts) if parts else "нет ходов"
