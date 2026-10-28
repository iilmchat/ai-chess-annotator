"""`POST /v1/ingest` — загрузка аннотированных партий в RAG (T-017).

Принимает PGN-файл (multipart/form-data), парсит **все** партии, строит
эмбеддинг позиции и сохраняет в `annotated_games`. Эндпоинт
административный: аутентификация появится вместе с JWT (KI-025), сейчас
он защищён только rate limiting.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.errors import InvalidPgnError
from app.rag.service import ingest_pgn_text

router = APIRouter(tags=["rag"])

#: Ограничение размера загружаемого PGN.
MAX_PGN_BYTES = 5 * 1024 * 1024


class IngestResponse(BaseModel):
    """Результат загрузки."""

    ingested: int = Field(ge=0)
    ids: list[int] = Field(default_factory=list)


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: Annotated[UploadFile, File(description="PGN-файл с аннотациями")],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: Annotated[str, Form(description="Источник партий")] = "upload",
) -> IngestResponse:
    """Загрузить партии из PGN в базу аннотаций.

    Raises:
        InvalidPgnError: пустой файл, превышен размер или нет партий (→ 400).
    """
    data = await file.read()
    if not data:
        raise InvalidPgnError("Пустой файл PGN")
    if len(data) > MAX_PGN_BYTES:
        raise InvalidPgnError(
            f"Файл больше {MAX_PGN_BYTES // (1024 * 1024)} МБ"
        )

    pgn_text = _decode(data)
    ids = await ingest_pgn_text(session, pgn_text, source=source)
    if not ids:
        raise InvalidPgnError("PGN не содержит партий с ходами")
    await session.commit()

    return IngestResponse(ingested=len(ids), ids=ids)


def _decode(data: bytes) -> str:
    """UTF-8, при неудаче — cp1251 (старые базы PGN)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")
