"""Сервис генерации комментария (T-016).

Поток: `analysis_id` → партия и ходы из БД → «пакет фактов» → LLM →
валидатор (с retry) → текст.

KI-023 (итерация 37): retry-цикл проверяет не только легальность SAN-ходов,
но и семантические утверждения о позиции («слон на e6», «связка пешки f7»);
на последней попытке offending-предложения вырезаются вместо ошибки 502.

KI-060 (итерация 49): перед семантической проверкой текст проверяется на
китайские иероглифы. При дрейфе модель переспрашивается **один раз** с
подсказкой «отвечай только по-русски»; если иероглифы остались, предложения
с ними вырезаются, а при их слишком большой доле ответ отклоняется (502).

Почему отдельный модуль, а не всё в эндпоинте: генерация — это сценарий
(несколько шагов, ретраи, валидация), который хочется тестировать без HTTP
и без реальной модели.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import cast

import chess
import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import (
    cache_usage,
    commentary_position_fen,
    lookup,
    make_prompt_hash,
    model_for_level,
    store,
)
from app.commentary.semantic import (
    cjk_warning,
    count_cjk,
    has_cjk,
    sanitize_cjk,
    semantic_warning,
    strip_invalid_claims,
    validate_semantic_claims,
)
from app.config import Settings, get_settings
from app.db.models import Commentary, Edit, Game, Move
from app.db.models import Motif as MotifRow
from app.engine.models import (
    EngineLine,
    MoveAnalysis,
    MoveClassification,
)
from app.engine.validator import (
    extract_moves_from_text,
    validate_moves,
)
from app.errors import LLMValidationError, NotFoundError
from app.ingestion import ParsedGame, parse_pgn
from app.llm import BaseLLMClient, LLMUsage, get_llm_client
from app.llm.calls import record_cache_hit
from app.llm.prompts import build_fact_package
from app.motifs.models import Motif
from app.prompts import load_system_prompt, resolve_language

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

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GeneratedCommentary:
    """Результат генерации: текст, расход токенов, число попыток."""

    text: str
    usage: LLMUsage
    validated: bool
    attempts: int
    cached: bool = False


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
    user_id: int | None = None,
) -> EditedCommentary:
    """Обновить текст комментария и сохранить правку в датасет `edits`.

    Оригинальный текст берётся из `commentaries.text` **до** изменения;
    потомки (`edits`) видят пару «было → стало» и не зависят от текущего
    состояния комментария. `human_edited` выставляется в `True`.

    Args:
        session: сессия БД.
        commentary_id: `commentaries.id`.
        new_text: новый текст (уже провалидирован API-слоем).
        editor_id: пользователь-редактор (историческое поле, может быть NULL).
        user_id: владелец правки (`edits.user_id`, T-028/KI-035).

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
        user_id=user_id,
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
        await session.scalars(
            select(MotifRow)
            .where(MotifRow.move_id.in_(move_ids))
            # Детерминированный порядок: GET /v1/games/{id} сравнивается с
            # POST /v1/analyze поле-в-поле, а у хода может быть >1 мотива.
            .order_by(MotifRow.id)
        )
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
    language: str | None = None,
    settings: Settings | None = None,
    client: BaseLLMClient | None = None,
    rag_context: RagContextBuilder | None = None,
) -> GeneratedCommentary:
    """Сгенерировать и провалидировать комментарий для сохранённой партии.

    Args:
        language: ``ru``/``en`` (T-106); ``None`` — из `DEFAULT_LANGUAGE`.
            Выбор промпта и retry-подсказки зависит от языка.

    Raises:
        NotFoundError: партии или её анализа нет в БД.
        LLMValidationError: LLM так и не выдала ответ без нелегальных ходов.
    """
    cfg = settings or get_settings()
    lang = resolve_language(language, None, cfg.default_language)
    game = await load_game(session, game_id)
    parsed = parse_pgn(game.pgn)
    analyses = await load_analyses(session, game, parsed)
    if not analyses:
        raise NotFoundError(
            f"Для партии {game_id} нет анализа ходов — сначала вызовите /v1/analyze"
        )

    system_prompt = await load_system_prompt(level, session, language=lang)
    system_prompt = _with_style(system_prompt, style, lang)

    model = client.model if client is not None else model_for_level(cfg, level)
    prompt_hash_value = make_prompt_hash(
        level, model, system_prompt, style=style
    )
    position_fen = commentary_position_fen(parsed, analyses)
    lookup_started = time.perf_counter()
    hit = await lookup(
        session,
        fen=position_fen,
        level=level,
        prompt_hash_value=prompt_hash_value,
        settings=cfg,
    )
    lookup_ms = int((time.perf_counter() - lookup_started) * 1000)
    if hit is not None:
        # KI-046: cache-hit — тоже запись в llm_calls (0 токенов, 0 стоимости).
        record_cache_hit(latency_ms=lookup_ms)
        return GeneratedCommentary(
            text=hit.text,
            usage=cache_usage(),
            validated=True,
            attempts=1,
            cached=True,
        )

    package = build_fact_package(parsed, analyses, level)
    if rag_context is not None:
        extra = await rag_context(parsed, analyses)
        if extra:
            package = f"{package}\n\n{extra}"

    llm = client if client is not None else get_llm_client(level, cfg)
    llm.set_level(level)
    owns_client = client is None
    try:
        text, usage, attempts = await _generate_with_validation(
            llm,
            package,
            system_prompt,
            parsed,
            analyses,
            cfg.commentary_max_retries,
            language=lang,
            semantic_validation_enabled=cfg.semantic_validation_enabled,
        )
    finally:
        if owns_client:
            await llm.aclose()

    await store(
        session,
        fen=position_fen,
        level=level,
        prompt_hash_value=prompt_hash_value,
        response_text=text,
        settings=cfg,
    )
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
    *,
    language: str = "ru",
    semantic_validation_enabled: bool = True,
) -> tuple[str, LLMUsage, int]:
    """Сгенерировать текст; переспросить при нелегальных ходах или фактах.

    KI-023: после проверки легальности SAN выполняется семантическая
    проверка утверждений о позиции («слон на e6», «связка пешки f7»).
    Невалидные утверждения — повод переспросить; если проблема осталась на
    последней попытке, а ходы легальны, невалидные предложения вырезаются
    (:func:`strip_invalid_claims`), а не поднимается 502: пользователь
    получает безопасный, хоть и более короткий текст. Нелегальные ходы
    обрабатываются как раньше — retry, затем :class:`LLMValidationError`.

    KI-060: текст с китайскими иероглифами переспрашивается один раз с
    подсказкой «отвечай только на русском». Если иероглифы остались,
    предложения с ними вырезаются (:func:`sanitize_cjk`); при слишком
    большой доле или пустом остатке — :class:`LLMValidationError`.

    Args:
        llm: клиент LLM.
        package: пакет фактов (исходный промпт).
        system_prompt: системная роль.
        parsed: разобранная партия (mainline + стартовая позиция).
        analyses: анализы ходов (PV-линии для extra_fens).
        max_retries: сколько раз переспрашивать (0 — одна попытка).
        language: язык retry-подсказок (``ru``/``en``).
        semantic_validation_enabled: флаг ``SEMANTIC_VALIDATION_ENABLED``.

    Returns:
        ``(текст, расход, число_попыток)``.
    """
    mainline = [move.san for move in parsed.moves]
    # Модель имеет право цитировать PV из пакета фактов — добавляем позиции
    # этих линий в «разрешённое» множество, иначе валидатор ложно ругается.
    extra_fens = pv_positions(parsed, analyses)
    last_attempt = max_retries + 1
    illegal: list[str] = []
    semantic_problems: list[str] = []
    cjk_present = False
    cjk_retry_used = False
    for attempt in range(1, last_attempt + 1):
        prompt = _attempt_prompt(
            package, illegal, semantic_problems, language, cjk_present
        )
        result = await llm.complete(prompt, system_prompt=system_prompt)
        cjk_present = has_cjk(result.text)
        moves = extract_moves_from_text(result.text)
        legal, illegal = validate_moves(
            moves,
            parsed.initial_fen,
            mainline_san=mainline,
            extra_fens=extra_fens,
        )
        if not legal:
            # Семантические проблемы прошлого ответа сохраняем: в подсказке
            # следующей попытки уживаются оба предупреждения (combined).
            continue
        text = result.text
        if cjk_present:
            cjk_chars = count_cjk(result.text)
            if not cjk_retry_used and attempt < last_attempt:
                # KI-060: ровно один retry «ответь по-русски», затем чистим.
                cjk_retry_used = True
                logger.warning(
                    "commentary_cjk_detected",
                    attempt=attempt,
                    cjk_chars=cjk_chars,
                    retry_left=last_attempt - attempt,
                )
                continue
            outcome = sanitize_cjk(text)
            if outcome.rejected:
                # В лог — только счётчики, без текста LLM.
                logger.warning(
                    "commentary_cjk_dropped",
                    attempt=attempt,
                    cjk_chars=outcome.cjk_chars,
                    cjk_share=round(outcome.share, 3),
                    removed_sentences=outcome.removed_sentences,
                )
                raise LLMValidationError(
                    "LLM вернула китайские иероглифы после "
                    f"{attempt} попыток; ответ отклонён (KI-060)"
                )
            logger.warning(
                "commentary_cjk_stripped",
                attempt=attempt,
                cjk_chars=outcome.cjk_chars,
                cjk_share=round(outcome.share, 3),
                removed_sentences=outcome.removed_sentences,
            )
            text = outcome.text
        semantic_problems = []
        if not semantic_validation_enabled:
            return text, result.usage, attempt
        all_ok, semantic_problems = validate_semantic_claims(
            text,
            parsed.initial_fen,
            mainline_san=mainline,
            extra_fens=extra_fens,
        )
        if all_ok:
            return text, result.usage, attempt
        if attempt >= last_attempt:
            # Не 502: вырезаем предложения с выдуманными фигурами.
            stripped = strip_invalid_claims(text, semantic_problems)
            # В лог — только счётчики и факт обрезки, без текста LLM.
            logger.warning(
                "commentary_semantic_stripped",
                attempt=attempt,
                problems=len(semantic_problems),
                stripped=stripped != text,
            )
            return stripped, result.usage, attempt
        logger.warning(
            "commentary_semantic_rejected",
            attempt=attempt,
            problems=len(semantic_problems),
            retry_left=last_attempt - attempt,
        )

    raise LLMValidationError(
        "LLM вернула нелегальные ходы после "
        f"{last_attempt} попыток: {', '.join(illegal)}"
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


def _with_style(system_prompt: str, style: str | None, language: str) -> str:
    """Добавить пользовательский стиль к промпту на языке комментария."""
    if not style:
        return system_prompt
    label = "ADDITIONAL STYLE" if language == "en" else "ДОПОЛНИТЕЛЬНЫЙ СТИЛЬ"
    return f"{system_prompt}\n\n{label}: {style}"


def _attempt_prompt(
    package: str,
    illegal: Sequence[str],
    semantic_problems: Sequence[str],
    language: str,
    cjk_present: bool = False,
) -> str:
    """Собрать промпт попытки с учётом ошибок предыдущего ответа.

    KI-023: подсказки комбинируются. Если в прошлом ответе были нелегальные
    ходы — сохраняется прежний текст предупреждения о ходах; семантические
    проблемы (например, «слон на e6», которого нет) добавляются отдельным
    блоком. KI-060 добавляет третий блок — требование убрать китайские
    иероглифы. Пакет фактов идёт первым и не дублируется.
    """
    warnings: list[str] = []
    if illegal:
        warnings.append(_moves_warning(illegal, language))
    if semantic_problems:
        warnings.append(semantic_warning(semantic_problems, language))
    if cjk_present:
        warnings.append(cjk_warning(language))
    if not warnings:
        return package
    return "\n\n".join([package, *warnings])


def _moves_warning(illegal: Sequence[str], language: str = "ru") -> str:
    """Предупреждение о нелегальных ходах для повторной генерации."""
    bad = ", ".join(illegal)
    if language == "en":
        return (
            "WARNING: the previous answer contained moves that are not in the "
            f"fact sheet: {bad}. Rewrite the commentary using ONLY moves from "
            "the fact sheet. Do not mention moves that are not there."
        )
    return (
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
