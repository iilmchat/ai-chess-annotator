"""SSE-стриминг комментария и чата (T-032).

LLM подменяется фейком: проверяем формат событий, валидатор и запись
комментария. Реальная сеть/модель не нужны.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Commentary, Game
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.main import create_app

SHORT_PGN = (
    '[Event "Stream"]\n[White "White"]\n[Black "Black"]\n[Result "1/2-1/2"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 1/2-1/2"
)
LEGAL_CHUNKS = ["Партия ", "началась ", "с d4 и Nf6."]
ILLEGAL_CHUNKS = ["Белые ", "сыграли Qxf7."]


class FakeStreamingLLM(BaseLLMClient):
    """Фейк, отдающий заранее заданные чанки."""

    def __init__(self, chunks: list[str]) -> None:
        super().__init__(model="fake-stream")
        self.chunks = chunks

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        return LLMResult(
            text="".join(self.chunks),
            usage=LLMUsage(
                provider="fake", model=self.model, tokens_in=10, tokens_out=5
            ),
        )

    async def stream_generate(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        for chunk in self.chunks:
            yield chunk


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен")


def _client() -> TestClient:
    settings = get_settings().model_copy(
        update={"rate_limit_requests": 1000, "llm_warmup_enabled": False}
    )
    return TestClient(create_app(settings))


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    name: str | None = None
    data: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
        elif not line.strip() and name:
            events.append((name, json.loads("\n".join(data))))
            name = None
            data = []
    if name and data:
        events.append((name, json.loads("\n".join(data))))
    return events


def test_commentary_stream_sends_deltas_done_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeStreamingLLM(LEGAL_CHUNKS)
    monkeypatch.setattr(
        "app.commentary.streaming.get_llm_client", lambda level, cfg: fake
    )
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/commentary/stream",
                json={"analysis_id": analysis_id, "level": "club"},
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            names = [name for name, _ in events]
            assert names[0] == "meta"
            assert "delta" in names
            assert names[-1] == "done"
            done = events[-1][1]
            assert done["validated"] is True
            assert done["commentary_id"] > 0
            text = "".join(
                str(payload["text"]) for name, payload in events if name == "delta"
            )
            assert "d4" in text and "Nf6" in text
            rows = asyncio.run(_read_commentaries(analysis_id))
            assert len(rows) == 1
            assert rows[0].text == "Партия началась с d4 и Nf6."
            assert rows[0].validated is True
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_commentary_stream_returns_error_for_illegal_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeStreamingLLM(ILLEGAL_CHUNKS)
    monkeypatch.setattr(
        "app.commentary.streaming.get_llm_client", lambda level, cfg: fake
    )
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/commentary/stream",
                json={"analysis_id": analysis_id, "level": "club"},
            )
            events = _events(response.text)
            assert events[-1][0] == "error"
            assert events[-1][1]["code"] == "llm_invalid_output"
            assert "Qxf7" in events[-1][1]["illegal"]
            assert asyncio.run(_read_commentaries(analysis_id)) == []
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_chat_stream_sends_done(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeStreamingLLM(["Белыми ", "играли ", "белые."])
    monkeypatch.setattr("app.chat.streaming.get_llm_client", lambda level, cfg: fake)
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/chat/stream",
                json={
                    "analysis_id": analysis_id,
                    "level": "club",
                    "message": "Кто играл белыми?",
                },
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            assert events[0][0] == "meta"
            assert events[-1][0] == "done"
            assert events[-1][1]["tokens_used"] > 0
        finally:
            asyncio.run(_delete_game(analysis_id))


async def _read_commentaries(game_id: int) -> list[Commentary]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return list(
                (
                    await session.scalars(
                        select(Commentary)
                        .where(Commentary.game_id == game_id)
                        .order_by(Commentary.id)
                    )
                ).all()
            )
    finally:
        await engine.dispose()


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(Game.__table__.delete().where(Game.id == game_id))
    finally:
        await engine.dispose()
