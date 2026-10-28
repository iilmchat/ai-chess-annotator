"""Сервис генерации комментария (T-016).

Поток: `analysis_id` → партия и ходы из БД → «пакет фактов» → LLM →
валидатор (с retry) → текст.

Почему отдельный модуль, а не всё в эндпоинте: генерация — это сценарий
(несколько шагов, ретраи, валидация), который хочется тестировать без HTTP
и без реальной модели.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import chess
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Commentary, Edit, Game, Move
from app.db.models import Motif as MotifRow
from app.engine.models import (
    EngineLine,
    MoveAnalysis,
    MoveClassification,
)
from app.engine.validator import extract_moves_from_text, validate_moves
from app.errors import LLMValidationError, NotFoundError
from app.ingestion import ParsedGame, parse_pgn
from app.llm import BaseLLMClient, LLMUsage, get_llm_client
from app.llm.prompts import build_fact_package
from app.motifs.models import Motif
from app.prompts import load_system_prompt

__all__ = [
    "EditedCommentary",
    "GeneratedCommentary",
    "RagContextBuilder",
    "edit_commentary",
    "generate_commentary",
    "load_analyses",
    "load_game",
    "pv_positions",
]

#: Функция, добавляющая RAG-контекст к пакету фактов (T-017).
RagContextBuilder = Callable[
    [ParsedGame, list[MoveAnalysis]], Awaitable[str | None]
]


@dataclass(frozen=True, slots=True)
class GeneratedCommentary:
    """Результат генерации: текст, расход токенов, число попыток."""

    text: str
    usage: LLMUsage
    validated: bool
    attempts: int


@dataclass(frozen=True, slots=True)
class EditedCommentary:
    """Результат ручной правки: комментарий, `edits.id` и старый текст."""

    commentary: Commentary
    edit_id: int
    original_text: str


async def edit_commentary(
    session: AsyncSession,
    commentary_id: int,
    new_text: str,
    *,
    editor_id: int | None = None,
) -> EditedCommentary:
    """Обновить текст комментария и сохранить правку в датасет `edits`.

    Оригинальный текст берётся из `commentaries.text` **до** изменения;
    потомки (`edits`) видят пару «было → стало» и не зависят от текущего
    состояния комментария. `human_edited` выставляется в `True`.

    Args:
        session: сессия БД.
        commentary_id: `commentaries.id`.
        new_text: новый текст (уже провалидирован API-слоем).
        editor_id: пользователь-редактор; пока аутентификации нет — ``None``.

    Raises:
        NotFoundError: комментарий не найден (→ 404).
    """
    row = await session.get(Commentary, commentary_id)
    if row is None:
        raise NotFoundError(f"Комментарий {commentary_id} не найден")

    original_text = row.text
    row.text = new_text
    row.human_edited = True
    edit = Edit(
        commentary_id=row.id,
        original_text=original_text,
        edited_text=new_text,
        editor_id=editor_id,
    )
    session.add(edit)
    await session.flush()
    return EditedCommentary(
        commentary=row, edit_id=edit.id, original_text=original_text
    )


async def load_game(session: AsyncSession, game_id: int) -> Game:
    """Достать партию или поднять `NotFoundError` (→ 404)."""
    game = await session.get(Game, game_id)
    if game is None:
        raise NotFoundError(f"Партия {game_id} не найдена")
    return game


async def load_analyses(
    session: AsyncSession, game: Game, parsed: ParsedGame
) -> list[MoveAnalysis]:
    """Восстановить `MoveAnalysis` из БД (moves + motifs + multipv).

    В БД хранятся оценки с точки зрения белых (`eval_cp`/`eval_mate`) и
    `delta_cp`; для `MoveAnalysis` нужны оценки с точки зрения сделавшего
    ход, поэтому знак восстанавливается по чётности полухода.
    """
    rows = (
        await session.scalars(
            select(Move).where(Move.game_id == game.id).order_by(Move.ply)
        )
    ).all()
    if not rows:
        return []

    move_ids = [row.id for row in rows]
    motif_rows = (
        await session.scalars(select(MotifRow).where(MotifRow.move_id.in_(move_ids)))
    ).all()
    motifs_by_move: dict[int, list[Motif]] = {}
    for motif_row in motif_rows:
        try:
            motif = Motif(
                type=motif_row.type,
                description=motif_row.description or motif_row.type,
                importance=motif_row.importance or 1,
            )
        except ValidationError:
            continue
        motifs_by_move.setdefault(motif_row.move_id, []).append(motif)

    uci_by_ply = _played_uci(parsed)
    analyses: list[MoveAnalysis] = []
    for row in rows:
        mate_after = row.eval_mate
        if mate_after is not None:
            eval_after = 0
        else:
            sign = 1 if row.ply % 2 == 1 else -1
            eval_after = (row.eval_cp or 0) * sign
        delta_cp = row.delta_cp or 0
        analyses.append(
            MoveAnalysis(
                ply=row.ply,
                played_move_san=row.san,
                played_move_uci=uci_by_ply.get(row.ply, "a1a1"),
                eval_before=eval_after + delta_cp,
                eval_after=eval_after,
                best_move_uci=_uci_for_san(uci_by_ply, row, parsed),
                best_move_san=row.best_move_san or row.san,
                delta_cp=delta_cp,
                classification=cast(MoveClassification, row.classification or "good"),
                engine_depth=row.engine_depth,
                mate_after=mate_after,
                lines=_parse_lines(row.multipv),
                motifs=motifs_by_move.get(row.id, []),
            )
        )
    return analyses


async def generate_commentary(
    session: AsyncSession,
    game_id: int,
    level: str,
    style: str | None = None,
    *,
    settings: Settings | None = None,
    client: BaseLLMClient | None = None,
    rag_context: RagContextBuilder | None = None,
) -> GeneratedCommentary:
    """Сгенерировать и провалидировать комментарий для сохранённой партии.

    Raises:
        NotFoundError: партии или её анализа нет в БД.
        LLMValidationError: LLM так и не выдала ответ без нелегальных ходов.
    """
    cfg = settings or get_settings()
    game = await load_game(session, game_id)
    parsed = parse_pgn(game.pgn)
    analyses = await load_analyses(session, game, parsed)
    if not analyses:
        raise NotFoundError(
            f"Для партии {game_id} нет анализа ходов — сначала вызовите /v1/analyze"
        )

    package = build_fact_package(parsed, analyses, level)
    if rag_context is not None:
        extra = await rag_context(parsed, analyses)
        if extra:
            package = f"{package}\n\n{extra}"

    system_prompt = await load_system_prompt(level, session)
    if style:
        system_prompt = f"{system_prompt}\n\nДОПОЛНИТЕЛЬНЫЙ СТИЛЬ: {style}"

    llm = client if client is not None else get_llm_client(level, cfg)
    llm.set_level(level)
    owns_client = client is None
    try:
        text, usage, attempts = await _generate_with_validation(
            llm, package, system_prompt, parsed, analyses, cfg.commentary_max_retries
        )
    finally:
        if owns_client:
            await llm.aclose()

    return GeneratedCommentary(
        text=text, usage=usage, validated=True, attempts=attempts
    )


async def _generate_with_validation(
    llm: BaseLLMClient,
    package: str,
    system_prompt: str,
    parsed: ParsedGame,
    analyses: list[MoveAnalysis],
    max_retries: int,
) -> tuple[str, LLMUsage, int]:
    """Сгенерировать текст; при нелегальных ходах — переспросить (до retry)."""
    mainline = [move.san for move in parsed.moves]
    # Модель имеет право цитировать PV из пакета фактов — добавляем позиции
    # этих линий в «разрешённое» множество, иначе валидатор ложно ругается.
    extra_fens = pv_positions(parsed, analyses)
    illegal: list[str] = []
    for attempt in range(1, max_retries + 2):
        prompt = package if not illegal else _retry_prompt(package, illegal)
        result = await llm.complete(prompt, system_prompt=system_prompt)
        moves = extract_moves_from_text(result.text)
        legal, illegal = validate_moves(
            moves,
            parsed.initial_fen,
            mainline_san=mainline,
            extra_fens=extra_fens,
        )
        if legal:
            return result.text, result.usage, attempt

    raise LLMValidationError(
        "LLM вернула нелегальные ходы после "
        f"{max_retries + 1} попыток: {', '.join(illegal)}"
    )


def pv_positions(
    parsed: ParsedGame, analyses: list[MoveAnalysis], *, limit: int = 8
) -> list[str]:
    """Позиции из PV-линий движка (по позиции «до хода» каждого анализа).

    Нужны валидатору: если модель цитирует вариант из пакета фактов, его
    ходы легальны в этих позициях, хотя и не входят в mainline партии.
    """
    positions: list[str] = []
    for analysis in analyses:
        if not analysis.lines:
            continue
        fen_before = (
            parsed.initial_fen
            if analysis.ply <= 1
            else parsed.moves[analysis.ply - 2].fen
        )
        board = chess.Board(fen_before)
        for uci in analysis.lines[0].pv[:limit]:
            try:
                move = board.parse_uci(uci)
            except ValueError:
                break
            board.push(move)
            positions.append(board.fen())
    return positions


def _retry_prompt(package: str, illegal: list[str]) -> str:
    bad = ", ".join(illegal)
    return (
        f"{package}\n\n"
        "ВНИМАНИЕ: в предыдущем ответе были ходы, которых нет в пакете фактов: "
        f"{bad}. Перепиши комментарий, используя ТОЛЬКО ходы из пакета. "
        "Не упоминай ходы, которых там нет."
    )


def _played_uci(parsed: ParsedGame) -> dict[int, str]:
    """UCI сыгранных ходов по номеру полухода (реплей SAN по доске)."""
    board = chess.Board(parsed.initial_fen)
    result: dict[int, str] = {}
    for move in parsed.moves:
        try:
            played = board.parse_san(move.san)
        except ValueError:
            break
        board.push(played)
        result[move.ply] = played.uci()
    return result


def _uci_for_san(
    uci_by_ply: dict[int, str], row: Move, parsed: ParsedGame
) -> str:
    """UCI для лучшего хода (лучший SAN может отличаться от сыгранного)."""
    if row.best_move_san is None or row.best_move_san == row.san:
        return uci_by_ply.get(row.ply, "a1a1")

    fen_before = (
        parsed.initial_fen
        if row.ply <= 1
        else parsed.moves[row.ply - 2].fen
    )
    try:
        board = chess.Board(fen_before)
        return board.parse_san(row.best_move_san).uci()
    except ValueError:
        return uci_by_ply.get(row.ply, "a1a1")


def _parse_lines(raw: list[dict[str, object]] | None) -> list[EngineLine]:
    """JSONB `moves.multipv` → `EngineLine` (битые элементы пропускаем)."""
    if not raw:
        return []
    lines: list[EngineLine] = []
    for item in raw:
        try:
            lines.append(EngineLine.model_validate(item))
        except ValidationError:
            continue
    return lines
