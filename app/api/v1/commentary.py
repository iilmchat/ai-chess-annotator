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
from app.auth.access import get_accessible_commentary, get_accessible_game
from app.auth.deps import get_current_user
from app.commentary import RagContextBuilder, edit_commentary, generate_commentary
from app.commentary.streaming import stream_commentary
from app.db.models import Commentary, Level, User
from app.db.session import get_session
from app.llm.calls import attach_commentary_id, llm_call_context, stream_in_call_context

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
    user: Annotated[User, Depends(get_current_user)],
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
    user: Annotated[User, Depends(get_current_user)],
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


def _rag_builder(request: Request) -> RagContextBuilder | None:
    """RAG-контекст подключается в lifespan (T-017); без него — None."""
    builder = getattr(request.app.state, "rag_context_builder", None)
    return builder if callable(builder) else None
