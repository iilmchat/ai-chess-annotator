"""`POST /v1/chat` — диалог по сохранённому анализу партии (T-022).

Принимает `analysis_id`, уровень и вопрос пользователя. Контекст — тот же
«пакет фактов», что у комментария (метаданные, ключевые ходы, мотивы,
оценки), но текст запроса приходит от пользователя. Ответ LLM проходит
через валидатор SAN-ходов; при нелегальных ходах — retry, после чего
возвращается `502 llm_invalid_output`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.audit import record_injection_attempt
from app.auth.access import get_accessible_game
from app.auth.deps import get_current_user
from app.chat import answer_question
from app.chat.streaming import stream_chat
from app.db.models import User
from app.db.session import get_session
from app.errors import PromptInjectionError
from app.security.guardrails import inspect_user_input, sanitize_user_input

router = APIRouter(tags=["chat"])

#: Уровни ассистента.
LevelName = Literal["beginner", "club", "gm"]

#: Ограничение на вопрос пользователя (защита промпта от полотна).
MAX_MESSAGE_CHARS = 2_000


class ChatHistoryItem(BaseModel):
    """Одна предыдущая реплика чата."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    """Тело запроса `/v1/chat`."""

    analysis_id: int = Field(gt=0, description="ID партии из /v1/analyze")
    level: LevelName = Field(default="club", description="beginner | club | gm")
    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_CHARS,
        description="Вопрос по партии",
    )
    history: list[ChatHistoryItem] = Field(
        default_factory=list,
        max_length=20,
        description="Предыдущие реплики (для контекста)",
    )

    @field_validator("message")
    @classmethod
    def _strip_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message не может состоять из пробелов")
        return message


class ChatResponse(BaseModel):
    """Ответ `/v1/chat`."""

    reply: str
    tokens_used: int = Field(ge=0)


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> EventSourceResponse:
    """Стримить ответ чата через SSE (T-032).

    События: `meta` → `delta`* → `done` | `error`. Итоговый текст
    проверяется SAN-валидатором. Guardrails и история проверяются до
    обращения к БД за партией; доступ к партии — до LLM (KI-035).
    """
    question = await _guard_message(request, payload.message)
    history = await _history(request, payload)
    await get_accessible_game(session, payload.analysis_id, user)
    return EventSourceResponse(
        stream_chat(
            session,
            payload.analysis_id,
            payload.level,
            question,
            history=history,
            settings=getattr(request.app.state, "settings", None),
        )
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> ChatResponse:
    """Ответить на вопрос пользователя по анализу партии.

    Guardrails проверяются до обращения к БД за партией (как и раньше),
    доступ к партии — до любого обращения к LLM (KI-035).

    Raises:
        NotFoundError: партия не найдена, чужая или для неё нет анализа (→ 404).
        LLMNotConfiguredError: для уровня gm не задан ключ (→ 503).
        LLMTimeoutError: провайдер не ответил (→ 504).
        LLMValidationError: нелегальные ходы даже после retry (→ 502).
    """
    question = await _guard_message(request, payload.message)
    history = await _history(request, payload)
    await get_accessible_game(session, payload.analysis_id, user)
    result = await answer_question(
        session,
        payload.analysis_id,
        payload.level,
        question,
        history=history,
        settings=getattr(request.app.state, "settings", None),
    )
    return ChatResponse(
        reply=result.reply,
        tokens_used=result.usage.tokens_in + result.usage.tokens_out,
    )


async def _history(
    request: Request, payload: ChatRequest
) -> list[dict[str, str]]:
    """История для промпта: user-реплики проходят guardrails заново."""
    result: list[dict[str, str]] = []
    for item in payload.history:
        if item.role == "user":
            content = await _guard_message(request, item.content)
        else:
            content = sanitize_user_input(item.content)
        if content:
            result.append({"role": item.role, "content": content})
    return result


async def _guard_message(request: Request, message: str) -> str:
    """Проверить вопрос и записать заблокированную инъекцию в аудит."""
    decision = inspect_user_input(message)
    if not decision.blocked:
        return decision.text
    await record_injection_attempt(
        request,
        rule_ids=decision.rule_ids,
        message_hash=decision.message_hash,
        message_len=decision.original_len,
    )
    raise PromptInjectionError(
        "Запрос заблокирован: обнаружена попытка prompt injection",
        rule_ids=decision.rule_ids,
    )
