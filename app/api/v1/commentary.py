"""`POST /v1/commentary` — сгенерированный и провалидированный комментарий (T-016).

Принимает `analysis_id` (сохранённая партия), уровень и опциональный стиль.
Восстанавливает анализ из БД, собирает пакет фактов, генерирует текст,
проверяет ходы валидатором (при нарушениях — до двух retry) и сохраняет
комментарий в `commentaries`.

Если LLM так и не выдала ответ без нелегальных ходов — возвращается
`502 llm_invalid_output`, и **ничего не сохраняется**: пользователь не
должен увидеть выдуманные ходы.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.audit import actor_from_request, append_audit, hash_text
from app.auth.access import get_accessible_commentary, get_accessible_game
from app.auth.deps import get_current_user, get_current_verified_user
from app.commentary import RagContextBuilder, edit_commentary, generate_commentary
from app.commentary.streaming import stream_commentary
from app.config import Settings, get_settings
from app.db.models import Commentary, Game, Level, Move, User
from app.db.session import get_session
from app.errors import (
    InvalidPgnError,
    NotFoundError,
    PdfExportError,
    PdfFontUnavailableError,
)
from app.export.board import BoardDiagram, MoveSnapshot, select_diagrams
from app.export.pdf import CommentaryPdfData, build_commentary_pdf
from app.ingestion import STARTING_FEN, parse_pgn
from app.llm.calls import attach_commentary_id, llm_call_context, stream_in_call_context

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["commentary"])

#: Уровни ассистента.
LevelName = Literal["beginner", "club", "gm"]

#: Ограничение на пользовательский «стиль» (защита промпта от полотна).
MAX_STYLE_CHARS = 200

#: Ограничение на длину ручной правки комментария.
MAX_COMMENTARY_CHARS = 20_000


class CommentaryRequest(BaseModel):
    """Тело запроса `/v1/commentary`."""

    analysis_id: int = Field(gt=0, description="ID партии из /v1/analyze")
    level: LevelName = Field(default="club", description="beginner | club | gm")
    style: str | None = Field(
        default=None,
        max_length=MAX_STYLE_CHARS,
        description="Пожелание по стилю (например, «короче, для новичка»)",
    )


class CommentaryResponse(BaseModel):
    """Ответ `/v1/commentary`."""

    commentary_id: int
    text: str
    tokens_used: int = Field(ge=0)
    validated: bool
    model: str
    attempts: int = Field(ge=1, description="Сколько раз обращались к LLM")


class CommentaryPatchRequest(BaseModel):
    """Тело `PATCH /v1/commentaries/{id}`."""

    text: str = Field(
        min_length=1,
        max_length=MAX_COMMENTARY_CHARS,
        description="Новый текст комментария",
    )

    @field_validator("text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("text не может состоять из пробелов")
        return text


class CommentaryPatchResponse(BaseModel):
    """Обновлённый комментарий + ссылка на запись в датасете правок."""

    commentary_id: int
    game_id: int
    level: LevelName
    text: str
    human_edited: bool
    validated: bool
    model: str
    edit_id: int = Field(description="id записи в таблице edits")
    created_at: datetime


@router.post("/commentary", response_model=CommentaryResponse)
async def create_commentary(
    payload: CommentaryRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_verified_user)],
) -> CommentaryResponse:
    """Сгенерировать комментарий и сохранить его в БД.

    Доступ к партии проверяется до любого обращения к LLM (KI-035).

    Raises:
        NotFoundError: партия не найдена, чужая или для неё нет анализа (→ 404).
        LLMNotConfiguredError: для уровня gm не задан ключ (→ 503).
        LLMTimeoutError: провайдер не ответил (→ 504).
        LLMValidationError: нелегальные ходы даже после retry (→ 502).
    """
    await get_accessible_game(session, payload.analysis_id, user)
    rag_context = _rag_builder(request) if payload.level == "gm" else None
    # T-030: метрики вызова LLM в llm_calls (операция + владелец + партия).
    with llm_call_context(
        operation="commentary",
        actor_id=user.id,
        game_id=payload.analysis_id,
        prompt_slug=payload.level,
    ):
        result = await generate_commentary(
            session,
            payload.analysis_id,
            payload.level,
            payload.style,
            rag_context=rag_context,
            settings=getattr(request.app.state, "settings", None),
        )

    row = Commentary(
        game_id=payload.analysis_id,
        user_id=user.id,
        level=Level(payload.level),
        text=result.text,
        model=result.usage.model,
        tokens_in=result.usage.tokens_in,
        tokens_out=result.usage.tokens_out,
        cost_usd=result.usage.cost_usd,
        validated=result.validated,
    )
    session.add(row)
    await session.commit()
    # KI-046: id комментария появился — привязываем записи llm_calls этого
    # trace (best-effort, фоновая задача; ответ не ждёт UPDATE-а).
    attach_commentary_id(row.id)

    return CommentaryResponse(
        commentary_id=row.id,
        text=result.text,
        tokens_used=result.usage.tokens_in + result.usage.tokens_out,
        validated=result.validated,
        model=result.usage.model,
        attempts=result.attempts,
    )


@router.post("/commentary/stream")
async def create_commentary_stream(
    payload: CommentaryRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_verified_user)],
) -> EventSourceResponse:
    """Стримить комментарий через SSE (T-032).

    События: `meta` → `delta`* → `done` | `error`. Валидатор проверяет
    итоговый текст; при нелегальных ходах запись не создаётся. Доступ к
    партии проверяется до запуска стрима (и до LLM) — KI-035.
    """
    await get_accessible_game(session, payload.analysis_id, user)
    # T-030: генератор исполняется после возврата ответа, поэтому контекст
    # вызова LLM выставляется обёрткой вокруг итерации стрима.
    return EventSourceResponse(
        stream_in_call_context(
            stream_commentary(
                session,
                payload.analysis_id,
                payload.level,
                payload.style,
                settings=getattr(request.app.state, "settings", None),
                user_id=user.id,
            ),
            operation="commentary",
            actor_id=user.id,
            game_id=payload.analysis_id,
            prompt_slug=payload.level,
        )
    )


@router.patch(
    "/commentaries/{commentary_id}",
    response_model=CommentaryPatchResponse,
)
async def patch_commentary(
    commentary_id: Annotated[
        int, Path(gt=0, description="ID комментария из /v1/commentary")
    ],
    payload: CommentaryPatchRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CommentaryPatchResponse:
    """Отредактировать комментарий человеком (T-021b).

    Обновляет `commentaries.text`, выставляет `human_edited = true` и
    сохраняет пару «оригинал → правка» в `edits` — датасет для будущего
    дообучения LLM (T-104). Правка принадлежит вызывающему пользователю
    (`edits.user_id`), доступ проверяется по владельцу партии (KI-035).

    Raises:
        NotFoundError: комментарий не найден или его партия недоступна (→ 404).
    """
    await get_accessible_commentary(session, commentary_id, user)
    result = await edit_commentary(
        session, commentary_id, payload.text, user_id=user.id
    )
    await append_audit(
        session,
        actor=actor_from_request(request),
        action="commentary.update",
        target_id=str(commentary_id),
        details={
            "commentary_id": commentary_id,
            "edit_id": result.edit_id,
            "level": result.commentary.level.value,
            "before_sha256": hash_text(result.original_text),
            "after_sha256": hash_text(payload.text),
            "before_len": len(result.original_text),
            "after_len": len(payload.text),
        },
    )
    await session.commit()
    row = result.commentary
    return CommentaryPatchResponse(
        commentary_id=row.id,
        game_id=row.game_id,
        level=row.level.value,
        text=row.text,
        human_edited=row.human_edited,
        validated=row.validated,
        model=row.model,
        edit_id=result.edit_id,
        created_at=row.created_at,
    )


@router.get("/commentaries/{commentary_id}/export/pdf")
async def export_commentary_pdf(
    commentary_id: Annotated[
        int, Path(gt=0, description="ID комментария из /v1/commentary")
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    """Скачать комментарий в PDF (T-107).

    PDF собирается серверно (`app.export.pdf`, fpdf2 + TTF-шрифт):
    шапка с игроками/турниром, PGN-ходы, текст комментария и метаданные.
    Генерация CPU-bound и выполняется в `asyncio.to_thread`, чтобы не
    блокировать event loop.

    Доступ — как к комментарию: владелец партии или администратор;
    чужая запись → 404 (KI-035).

    Raises:
        NotFoundError: комментария/партии нет или нет доступа (→ 404).
        PdfFontUnavailableError: в окружении нет TTF-шрифта (→ 503).
        PdfExportError: не удалось сформировать PDF (→ 500).
    """
    commentary = await get_accessible_commentary(session, commentary_id, user)
    game = await session.get(Game, commentary.game_id)
    if game is None:  # pragma: no cover - FK commentary.game_id гарантирует
        raise NotFoundError(f"Партия комментария {commentary_id} не найдена")

    move_rows = list(
        await session.scalars(
            select(Move)
            .where(Move.game_id == game.id)
            .order_by(Move.ply)
        )
    )
    # Метаданные игроков/турнира — из PGN; битый legacy-PGN не должен
    # ломать экспорт: шапка просто останется с прочерками.
    parsed = None
    try:
        parsed = parse_pgn(game.pgn)
    except InvalidPgnError:
        logger.warning("pdf_export_invalid_pgn", game_id=game.id)
    metadata = parsed.metadata if parsed is not None else None
    # T-107b: финальная позиция + критические моменты. Любая ошибка выбора
    # диаграмм (битый FEN/неожиданный SAN) не должна ломать PDF: просто
    # отдаём документ без картинок.
    diagrams: tuple[BoardDiagram, ...] = ()
    try:
        diagrams = select_diagrams(
            tuple(
                MoveSnapshot(
                    ply=move.ply,
                    san=move.san,
                    fen=move.fen,
                    classification=move.classification,
                    delta_cp=move.delta_cp,
                )
                for move in move_rows
            ),
            initial_fen=(
                parsed.initial_fen if parsed is not None else STARTING_FEN
            ),
        )
    except Exception as exc:
        logger.warning(
            "pdf_board_diagrams_failed",
            commentary_id=commentary_id,
            error=type(exc).__name__,
        )

    data = CommentaryPdfData(
        commentary_id=commentary.id,
        white=metadata.white if metadata else None,
        black=metadata.black if metadata else None,
        event=metadata.event if metadata else None,
        date=metadata.date if metadata else None,
        result=metadata.result if metadata else None,
        level=commentary.level.value,
        model=commentary.model,
        moves=tuple(move.san for move in move_rows),
        commentary_text=commentary.text,
        diagrams=diagrams,
        validated=commentary.validated,
        human_edited=commentary.human_edited,
        generated_at=commentary.created_at,
    )
    try:
        pdf_bytes = await asyncio.to_thread(
            build_commentary_pdf, data, settings
        )
    except PdfFontUnavailableError:
        raise
    except Exception as exc:
        logger.exception(
            "commentary_pdf_failed", commentary_id=commentary_id
        )
        raise PdfExportError(
            "Не удалось сформировать PDF комментария"
        ) from exc

    logger.info(
        "commentary_pdf_exported",
        commentary_id=commentary.id,
        bytes=len(pdf_bytes),
    )
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="commentary-{commentary.id}.pdf"'
            ),
            "Cache-Control": "no-store",
        },
    )


def _rag_builder(request: Request) -> RagContextBuilder | None:
    """RAG-контекст подключается в lifespan (T-017); без него — None."""
    builder = getattr(request.app.state, "rag_context_builder", None)
    return builder if callable(builder) else None
