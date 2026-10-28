"""`POST /v1/chat` — диалог по сохранённому анализу партии (T-022).

Принимает `analysis_id`, уровень и вопрос пользователя. Контекст — тот же
«пакет фактов», что у комментария (метаданные, ключевые ходы, мотивы,
оценки), но текст запроса приходит от пользователя. Ответ LLM проходит
через валидатор SAN-ходов; при нелегальных ходах — retry, после чего
возвращается `502 llm_invalid_output`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import answer_question
from app.db.session import get_session

router = APIRouter(tags=["chat"])

#: Уровни ассистента.
LevelName = Literal["beginner", "club", "gm"]

#: Ограничение на вопрос пользователя (защита промпта от полотна).
MAX_MESSAGE_CHARS = 2_000


class ChatRequest(BaseModel):
    """Тело запроса `/v1/chat`."""

    analysis_id: int = Field(gt=0, description="ID партии из /v1/analyze")
    level: LevelName = Field(default="club", description="beginner | club | gm")
    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_CHARS,
        description="Вопрос по партии",
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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatResponse:
    """Ответить на вопрос пользователя по анализу партии.

    Raises:
        NotFoundError: партия не найдена или для неё нет анализа (→ 404).
        LLMNotConfiguredError: для уровня gm не задан ключ (→ 503).
        LLMTimeoutError: провайдер не ответил (→ 504).
        LLMValidationError: нелегальные ходы даже после retry (→ 502).
    """
    result = await answer_question(
        session,
        payload.analysis_id,
        payload.level,
        payload.message,
    )
    return ChatResponse(
        reply=result.reply,
        tokens_used=result.usage.tokens_in + result.usage.tokens_out,
    )
