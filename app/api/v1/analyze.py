"""`POST /v1/analyze` — разбор партии движком и сохранение в БД (T-009).

Контракт (SPEC.md §5): принимает PGN + уровень, возвращает структурированный
анализ **без LLM** (быстро) и `analysis_id` для следующих вызовов.

Позиции анализируются через пул движков (`EnginePool`), поэтому партия из
40 ходов укладывается в SLA SPEC §9 (KI-014).

Партии с FEN-стартом поддерживаются через PGN-заголовки
``[SetUp "1"] [FEN "..."]``: ingestion читает их и отдаёт
``ParsedGame.initial_fen``.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

import chess
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.models import Game, Level, Move, User
from app.db.models import Motif as MotifRow
from app.db.session import get_session
from app.engine.models import MoveAnalysis, MoveClassification
from app.engine.policy import choose_recommended_move
from app.engine.pool import EnginePool
from app.errors import EngineError
from app.ingestion import ParsedGame, parse_pgn
from app.motifs.models import Motif

router = APIRouter(tags=["analyze"])

#: Верхняя граница тела PGN (защита от гигантских запросов).
MAX_PGN_CHARS = 100_000

LevelName = Literal["beginner", "club", "gm"]


class AnalyzeRequest(BaseModel):
    """Тело запроса `/v1/analyze`."""

    pgn: str = Field(
        min_length=1,
        max_length=MAX_PGN_CHARS,
        description="PGN партии (допускается FEN-старт через [SetUp]/[FEN])",
    )
    level: LevelName = Field(
        default="club", description="Уровень ассистента: beginner | club | gm"
    )


class AnalyzeMeta(BaseModel):
    """Метаданные разобранной партии."""

    white: str | None = None
    black: str | None = None
    eco: str | None = None


class AnalyzeSummary(BaseModel):
    """Сводка по «плохим» ходам (для быстрого обзора партии)."""

    blunders: int = 0
    mistakes: int = 0
    inaccuracies: int = 0


class RecommendedMove(BaseModel):
    """Рекомендация Policy Layer, адаптированная под уровень пользователя."""

    san: str = Field(min_length=1, description="Ход в SAN")
    uci: str = Field(min_length=4, description="Ход в UCI")
    eval_cp: int | None = Field(
        default=None, description="Оценка линии в сантипешках (None при мате)"
    )
    mate: int | None = Field(
        default=None, description="Мат в N ходов, если линия матовая"
    )


class AnalyzedMove(BaseModel):
    """Один полуход в ответе: оценка, класс, мотивы и рекомендация."""

    ply: int = Field(ge=1)
    san: str
    uci: str
    fen: str = Field(description="FEN позиции после хода")
    eval_cp: int | None = Field(
        default=None, description="Оценка позиции после хода (с точки зрения белых)"
    )
    mate: int | None = Field(default=None, description="Мат в N ходов (за белых)")
    best_move_san: str
    delta_cp: int = Field(ge=0)
    classification: MoveClassification
    motifs: list[Motif] = Field(default_factory=list)
    recommended_move: RecommendedMove | None = None


class AnalyzeResponse(BaseModel):
    """Ответ `/v1/analyze`."""

    analysis_id: str = Field(description="ID сохранённой партии (games.id)")
    status: Literal["completed"] = "completed"
    meta: AnalyzeMeta
    initial_fen: str = Field(description="Стартовая позиция партии (FEN)")
    analysis_level: LevelName = Field(description="Уровень, под который считался анализ")
    moves_analyzed: int = Field(ge=0)
    time_taken_ms: int = Field(ge=0)
    summary: AnalyzeSummary
    moves: list[AnalyzedMove] = Field(
        default_factory=list,
        description="Разбор по ходам: оценки, мотивы, рекомендация Policy Layer",
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    payload: AnalyzeRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> AnalyzeResponse:
    """Разобрать PGN, оценить ходы движком и сохранить партию.

    Требует авторизации (T-028): сохранённая партия принадлежит вызывающему
    пользователю (`games.user_id`), что и обеспечивает изоляцию данных
    (KI-035).

    Raises:
        InvalidPgnError: PGN пуст, не распознан или содержит нелегальный ход (→ 400).
        EngineError: пул движков недоступен или Stockfish сломан (→ 500).
    """
    started = time.perf_counter()

    parsed = parse_pgn(payload.pgn)
    pool = _engine_pool(request)
    analyses = await pool.analyze_game_moves(
        parsed.moves, payload.level, initial_fen=parsed.initial_fen
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    analysis_id = await _persist(
        session,
        payload.pgn,
        parsed,
        analyses,
        level=payload.level,
        elapsed_ms=elapsed_ms,
        user_id=user.id,
    )

    return AnalyzeResponse(
        analysis_id=str(analysis_id),
        status="completed",
        meta=AnalyzeMeta(
            white=parsed.metadata.white,
            black=parsed.metadata.black,
            eco=parsed.eco_code,
        ),
        initial_fen=parsed.initial_fen,
        analysis_level=payload.level,
        moves_analyzed=len(analyses),
        time_taken_ms=elapsed_ms,
        summary=_summarize(analyses),
        moves=_build_moves(parsed, analyses, payload.level),
    )


def _engine_pool(request: Request) -> EnginePool:
    """Пул движков из lifespan-состояния приложения."""
    pool = getattr(request.app.state, "engine_pool", None)
    if not isinstance(pool, EnginePool):
        raise EngineError("Пул движков не инициализирован (lifespan не выполнен)")
    return pool


async def _persist(
    session: AsyncSession,
    pgn: str,
    parsed: ParsedGame,
    analyses: list[MoveAnalysis],
    *,
    level: str,
    elapsed_ms: int,
    user_id: int,
) -> int:
    """Сохранить партию и оценки ходов одной транзакцией.

    Args:
        user_id: владелец партии (`games.user_id`) — вызывающий пользователь.

    Returns:
        `games.id` сохранённой партии.
    """
    game = Game(
        user_id=user_id,
        pgn=pgn,
        eco_code=parsed.eco_code,
        analysis_level=Level(level),
        analysis_time_ms=elapsed_ms,
        phase_breakdown=parsed.phase_breakdown.model_dump(),
    )
    session.add(game)
    await session.flush()

    move_rows: list[tuple[Move, MoveAnalysis]] = []
    for index, analysis in enumerate(analyses):
        eval_cp, eval_mate = _db_eval(analysis)
        move_rows.append(
            (
                Move(
                    game_id=game.id,
                    ply=analysis.ply,
                    san=analysis.played_move_san,
                    fen=parsed.moves[index].fen,
                    eval_cp=eval_cp,
                    eval_mate=eval_mate,
                    best_move_san=analysis.best_move_san,
                    delta_cp=analysis.delta_cp,
                    classification=analysis.classification,
                    engine_depth=analysis.engine_depth,
                    # KI-018: PV-линии позиции ДО хода (альтернативы игрока).
                    multipv=[line.model_dump(mode="json") for line in analysis.lines]
                    or None,
                ),
                analysis,
            )
        )
    session.add_all([row[0] for row in move_rows])
    await session.flush()

    # Мотивы (T-010): привязка к move_id, каскадно удаляются вместе с ходом.
    motif_rows: list[MotifRow] = [
        MotifRow(
            move_id=move.id,
            type=motif.type,
            description=motif.description,
            importance=motif.importance,
        )
        for move, analysis in move_rows
        for motif in analysis.motifs
    ]
    if motif_rows:
        session.add_all(motif_rows)

    await session.commit()
    return game.id


def _db_eval(analysis: MoveAnalysis) -> tuple[int | None, int | None]:
    """Оценка **с точки зрения белых** после хода: ``(eval_cp, eval_mate)``."""
    if analysis.mate_after is not None:
        return None, analysis.mate_after
    mover_sign = 1 if analysis.ply % 2 == 1 else -1
    return analysis.eval_after * mover_sign, None


def _build_moves(
    parsed: ParsedGame, analyses: list[MoveAnalysis], level: str
) -> list[AnalyzedMove]:
    """Собрать блок `moves` ответа: оценки, мотивы и рекомендации Policy.

    Policy вызывается для позиции **до** хода (`fen_before`) и линий
    `analysis.lines` — то есть отвечает на вопрос «что стоило сыграть этому
    игроку под его уровень», а не «что лучше в получившейся позиции».
    """
    fens_before = [parsed.initial_fen, *[move.fen for move in parsed.moves[:-1]]]
    result: list[AnalyzedMove] = []
    for analysis, fen_before in zip(analyses, fens_before, strict=True):
        eval_cp, eval_mate = _db_eval(analysis)
        result.append(
            AnalyzedMove(
                ply=analysis.ply,
                san=analysis.played_move_san,
                uci=analysis.played_move_uci,
                fen=parsed.moves[analysis.ply - 1].fen,
                eval_cp=eval_cp,
                mate=eval_mate,
                best_move_san=analysis.best_move_san,
                delta_cp=analysis.delta_cp,
                classification=analysis.classification,
                motifs=list(analysis.motifs),
                recommended_move=_recommended(analysis, fen_before, level),
            )
        )
    return result


def _recommended(
    analysis: MoveAnalysis, fen_before: str, level: str
) -> RecommendedMove | None:
    """Выбрать рекомендацию под уровень или вернуть ``None`` без линий."""
    if not analysis.lines:
        return None
    line = choose_recommended_move(analysis.lines, level, fen=fen_before)
    return RecommendedMove(
        san=_uci_to_san(fen_before, line.move_uci),
        uci=line.move_uci,
        eval_cp=line.score_cp,
        mate=line.mate,
    )


def _uci_to_san(fen: str, uci: str) -> str:
    """UCI → SAN на позиции; при неожиданной ошибке отдаём UCI как есть."""
    try:
        board = chess.Board(fen)
        return board.san(board.parse_uci(uci))
    except ValueError:
        return uci


def _summarize(analyses: list[MoveAnalysis]) -> AnalyzeSummary:
    """Посчитать blunder/mistake/inaccuracy по классификациям."""
    blunders = sum(1 for item in analyses if item.classification == "blunder")
    mistakes = sum(1 for item in analyses if item.classification == "mistake")
    inaccuracies = sum(1 for item in analyses if item.classification == "inaccuracy")
    return AnalyzeSummary(
        blunders=blunders, mistakes=mistakes, inaccuracies=inaccuracies
    )
