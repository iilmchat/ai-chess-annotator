"""`GET /v1/games/{id}` — сохранённый анализ без повторного Stockfish (T-031).

Отдаёт ту же структуру, что `POST /v1/analyze`, плюс `analysis_level`
(под какой уровень считался анализ) и сохранённые `commentaries`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.analyze import (
    AnalyzeMeta,
    AnalyzeResponse,
    LevelName,
    _build_moves,
    _summarize,
)
from app.commentary.service import load_analyses
from app.db.models import Commentary, Game, Level
from app.db.session import get_session
from app.errors import NotFoundError
from app.ingestion import parse_pgn

router = APIRouter(tags=["games"])


class CommentaryItem(BaseModel):
    """Сохранённый комментарий в карточке партии."""

    id: int
    level: LevelName
    text: str
    model: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    validated: bool
    human_edited: bool
    created_at: datetime


class GameDetailResponse(AnalyzeResponse):
    """Анализ + уровень + комментарии."""

    commentaries: list[CommentaryItem] = Field(default_factory=list)
    created_at: datetime


class GameListItem(BaseModel):
    """Краткая строка истории партий."""

    id: int
    white: str | None
    black: str | None
    event: str | None
    level: LevelName
    created_at: datetime


@router.get("/games", response_model=list[GameListItem])
async def list_games(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=50, description="Сколько партий")] = 20,
) -> list[GameListItem]:
    """Последние `limit` партий (по умолчанию 20)."""
    rows = (
        await session.scalars(
            select(Game).order_by(Game.created_at.desc(), Game.id.desc()).limit(limit)
        )
    ).all()
    items: list[GameListItem] = []
    for game in rows:
        white = black = event = None
        try:
            meta = parse_pgn(game.pgn).metadata
            white, black, event = meta.white, meta.black, meta.event
        except Exception:
            pass
        items.append(
            GameListItem(
                id=game.id,
                white=white,
                black=black,
                event=event,
                level=(game.analysis_level or Level.club).value,
                created_at=game.created_at,
            )
        )
    return items


@router.get("/games/{game_id}", response_model=GameDetailResponse)
async def get_game(
    game_id: Annotated[int, Path(gt=0, description="ID партии из /v1/analyze")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GameDetailResponse:
    """Вернуть сохранённый анализ партии.

    Raises:
        NotFoundError: партия не найдена или для неё нет анализа (→ 404).
    """
    game = await session.get(Game, game_id)
    if game is None:
        raise NotFoundError(f"Партия {game_id} не найдена")

    parsed = parse_pgn(game.pgn)
    analyses = await load_analyses(session, game, parsed)
    if not analyses:
        raise NotFoundError(
            f"Для партии {game_id} нет анализа ходов — сначала вызовите /v1/analyze"
        )

    level = game.analysis_level or Level.club
    commentary_rows = (
        await session.scalars(
            select(Commentary)
            .where(Commentary.game_id == game.id)
            .order_by(Commentary.id)
        )
    ).all()

    return GameDetailResponse(
        analysis_id=str(game.id),
        status="completed",
        meta=AnalyzeMeta(
            white=parsed.metadata.white,
            black=parsed.metadata.black,
            eco=parsed.eco_code,
        ),
        initial_fen=parsed.initial_fen,
        analysis_level=level.value,
        moves_analyzed=len(analyses),
        time_taken_ms=game.analysis_time_ms or 0,
        summary=_summarize(analyses),
        moves=_build_moves(parsed, analyses, level.value),
        commentaries=[
            CommentaryItem(
                id=row.id,
                level=row.level.value,
                text=row.text,
                model=row.model,
                tokens_in=row.tokens_in or 0,
                tokens_out=row.tokens_out or 0,
                validated=row.validated,
                human_edited=row.human_edited,
                created_at=row.created_at,
            )
            for row in commentary_rows
        ],
        created_at=game.created_at,
    )
