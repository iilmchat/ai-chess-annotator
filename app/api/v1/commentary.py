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

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.audit import actor_from_request, append_audit, hash_text
from app.commentary import RagContextBuilder, edit_commentary, generate_commentary
from app.commentary.streaming import stream_commentary
from app.db.models import Commentary, Level
from app.db.session import get_session

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
) -> CommentaryResponse:
    """Сгенерировать комментарий и сохранить его в БД.

    Raises:
        NotFoundError: партия не найдена или для неё нет анализа (→ 404).
        LLMNotConfiguredError: для уровня gm не задан ключ (→ 503).
        LLMTimeoutError: провайдер не ответил (→ 504).
        LLMValidationError: нелегальные ходы даже после retry (→ 502).
    """
    rag_context = _rag_builder(request) if payload.level == "gm" else None
    result = await generate_commentary(
        session,
        payload.analysis_id,
        payload.level,
        payload.style,
        rag_context=rag_context,
    )

    row = Commentary(
        game_id=payload.analysis_id,
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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventSourceResponse:
    """Стримить комментарий через SSE (T-032).

    События: `meta` → `delta`* → `done` | `error`. Валидатор проверяет
    итоговый текст; при нелегальных ходах запись не создаётся.
    """
    return EventSourceResponse(
        stream_commentary(
            session,
            payload.analysis_id,
            payload.level,
            payload.style,
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
) -> CommentaryPatchResponse:
    """Отредактировать комментарий человеком (T-021b).

    Обновляет `commentaries.text`, выставляет `human_edited = true` и
    сохраняет пару «оригинал → правка» в `edits` — датасет для будущего
    дообучения LLM (T-104).

    Raises:
        NotFoundError: комментарий не найден (→ 404).
    """
    result = await edit_commentary(session, commentary_id, payload.text)
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


def _rag_builder(request: Request) -> RagContextBuilder | None:
    """RAG-контекст подключается в lifespan (T-017); без него — None."""
    builder = getattr(request.app.state, "rag_context_builder", None)
    return builder if callable(builder) else None
