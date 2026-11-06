"""SSE-стриминг комментария и чата (T-032).

LLM подменяется фейком: проверяем формат событий, валидатор и запись
комментария. Реальная сеть/модель не нужны.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.chat.service import load_chat_system_prompt
from app.config import get_settings
from app.db.models import Commentary, Game
from app.errors import LLMNotConfiguredError
from app.i18n import ERROR_MESSAGES
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.main import create_app

#: Независимая от продакшен-детектора проверка: в тесте нельзя полагаться
#: на ту же реализацию, которую защищаем (KI-060, итерация 53).
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")

SHORT_PGN = (
    '[Event "Stream"]\n[White "White"]\n[Black "Black"]\n[Result "1/2-1/2"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 1/2-1/2"
)
LEGAL_CHUNKS = ["Партия ", "началась ", "с d4 и Nf6."]
ILLEGAL_CHUNKS = ["Белые ", "сыграли Qxf7."]
#: KI-060 (итерация 53): CJK-предложение между чистыми — клиент не должен
#: увидеть ни одного иероглифа, а сохранённый текст обязан быть очищенным.
CJK_SENTENCE = "这是中文。"
MIXED_CHUNKS = [
    "Партия ",
    "началась с d4. ",
    CJK_SENTENCE,
    "Затем ",
    "последовало Nf6.",
]
ONLY_CJK_CHUNKS = ["这是", "中文。"]
CLEAN_MIXED_TEXT = "Партия началась с d4. Затем последовало Nf6."


class FakeStreamingLLM(BaseLLMClient):
    """Фейк, отдающий заранее заданные чанки."""

    def __init__(self, chunks: list[str]) -> None:
        super().__init__(model="fake-stream")
        self.chunks = chunks
        self.prompts: list[str] = []
        self.system_prompts: list[str | None] = []

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        return LLMResult(
            text="".join(self.chunks),
            usage=LLMUsage(
                provider="fake", model=self.model, tokens_in=10, tokens_out=5
            ),
        )

    async def stream_generate(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
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


def _engine_available() -> bool:
    settings = get_settings()
    candidate = Path(settings.stockfish_path)
    return candidate.is_file() or shutil.which(settings.stockfish_path) is not None


#: Все тесты файла начинают с POST /v1/analyze: без Stockfish он отдаёт
#: 500 engine_not_found (KI-006), поэтому движок обязателен наравне с БД.
pytestmark = [
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен"),
    pytest.mark.skipif(not _engine_available(), reason="Stockfish не найден (KI-006)"),
]


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


def test_commentary_stream_filters_cjk_from_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060/итерация 53: иероглифы не попадают в SSE `delta` и в БД."""
    fake = FakeStreamingLLM(MIXED_CHUNKS)
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
            assert names[-1] == "done"
            deltas = "".join(
                str(payload["text"]) for name, payload in events if name == "delta"
            )
            assert deltas, "клиент не получил ни одного delta"
            assert _CJK_PATTERN.search(deltas) is None
            assert "马" not in deltas
            assert deltas == CLEAN_MIXED_TEXT
            rows = asyncio.run(_read_commentaries(analysis_id))
            assert len(rows) == 1
            assert rows[0].text == CLEAN_MIXED_TEXT
            assert _CJK_PATTERN.search(rows[0].text) is None
            assert rows[0].validated is True
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_commentary_stream_all_cjk_returns_error_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060: если после фильтра текста не осталось — error, записи нет."""
    fake = FakeStreamingLLM(ONLY_CJK_CHUNKS)
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
            assert "delta" not in names
            assert names[-1] == "error"
            assert events[-1][1]["code"] == "llm_empty"
            assert "китайские иероглифы" in str(events[-1][1]["detail"])
            assert asyncio.run(_read_commentaries(analysis_id)) == []
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_commentary_stream_sanitizes_dirty_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060: legacy cache-запись с иероглифами не отдаётся как есть."""
    dirty = (
        "Кэш-ответ с d4 и Nf6, остальной русский текст без иероглифов. "
        "这是中文。"
    )
    clean = "Кэш-ответ с d4 и Nf6, остальной русский текст без иероглифов."

    async def _fake_lookup(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(text=dirty)

    # Если очистка кэша не сработает, тест уйдёт в fake-LLM и упадёт на
    # model != semantic-cache — реальная сеть/LM Studio не нужна.
    fake = FakeStreamingLLM(LEGAL_CHUNKS)
    monkeypatch.setattr("app.commentary.streaming.lookup", _fake_lookup)
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
            assert events[0][0] == "meta"
            assert events[0][1]["model"] == "semantic-cache"
            assert names[-1] == "done"
            deltas = "".join(
                str(payload["text"]) for name, payload in events if name == "delta"
            )
            assert _CJK_PATTERN.search(deltas) is None
            assert deltas == clean
            rows = asyncio.run(_read_commentaries(analysis_id))
            assert len(rows) == 1
            assert rows[0].text == clean
            assert _CJK_PATTERN.search(rows[0].text) is None
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_commentary_stream_rejected_cache_falls_back_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060: cache-запись почти целиком из CJK → регенерация через LLM."""
    only_cjk = "这是中文。"

    async def _fake_lookup(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(text=only_cjk)

    fake = FakeStreamingLLM(LEGAL_CHUNKS)
    monkeypatch.setattr("app.commentary.streaming.lookup", _fake_lookup)
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
            assert events[0][1]["model"] == "fake-stream"
            deltas = "".join(
                str(payload["text"]) for name, payload in events if name == "delta"
            )
            assert deltas == "Партия началась с d4 и Nf6."
            assert _CJK_PATTERN.search(deltas) is None
            rows = asyncio.run(_read_commentaries(analysis_id))
            assert len(rows) == 1
            assert _CJK_PATTERN.search(rows[0].text) is None
            assert rows[0].model == "fake-stream"
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


def test_chat_stream_english_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-106/KI-058: meta.language и английский системный промпт."""
    fake = FakeStreamingLLM(["White played white."])
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
                    "message": "Who played white?",
                    "language": "en",
                },
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            assert events[0][0] == "meta"
            assert events[0][1]["language"] == "en"
            assert fake.system_prompts[0] == load_chat_system_prompt("en")
            assert "# User question" in fake.prompts[0]
            assert events[-1][0] == "done"
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_chat_stream_error_detail_localized_to_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSE `error` с известным кодом тоже локализуется (KI-058)."""
    def _raise(level: str, cfg: object) -> None:
        raise LLMNotConfiguredError("Для уровня gm не задан ключ")

    monkeypatch.setattr("app.chat.streaming.get_llm_client", _raise)
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/chat/stream",
                json={
                    "analysis_id": analysis_id,
                    "level": "club",
                    "message": "Who played white?",
                    "language": "en",
                },
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            assert events[-1][0] == "error"
            assert events[-1][1]["code"] == "llm_not_configured"
            assert (
                events[-1][1]["detail"]
                == ERROR_MESSAGES["llm_not_configured"]["en"]
            )
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_chat_stream_filters_cjk_from_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060/итерация 57: иероглифы не попадают в chat-SSE `delta`."""
    fake = FakeStreamingLLM(MIXED_CHUNKS)
    monkeypatch.setattr("app.chat.streaming.get_llm_client", lambda level, cfg: fake)
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/chat/stream",
                json={
                    "analysis_id": analysis_id,
                    "level": "club",
                    "message": "Что было в партии?",
                },
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            names = [name for name, _ in events]
            assert names[0] == "meta"
            assert names[-1] == "done"
            deltas = [
                str(payload["text"]) for name, payload in events if name == "delta"
            ]
            assert deltas, "клиент не получил ни одного delta"
            assert all(_CJK_PATTERN.search(chunk) is None for chunk in deltas)
            assert "".join(deltas) == CLEAN_MIXED_TEXT
            assert events[-1][1]["validated"] is True
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_chat_stream_all_cjk_returns_error_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060/итерация 57: CJK-only ответ чата — error без записи."""
    fake = FakeStreamingLLM(ONLY_CJK_CHUNKS)
    monkeypatch.setattr("app.chat.streaming.get_llm_client", lambda level, cfg: fake)
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/chat/stream",
                json={
                    "analysis_id": analysis_id,
                    "level": "club",
                    "message": "Какой был дебют?",
                },
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            names = [name for name, _ in events]
            assert "delta" not in names
            assert names[-1] == "error"
            assert events[-1][1]["code"] == "llm_empty"
            assert "китайские иероглифы" in str(events[-1][1]["detail"])
            assert asyncio.run(_read_commentaries(analysis_id)) == []
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_chat_stream_sanitizes_dirty_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060/итерация 57: legacy cache-запись чата не отдаётся с CJK."""
    dirty = (
        "Кэш-ответ с d4 и Nf6, остальной русский текст без иероглифов. "
        "这是中文。"
    )
    clean = "Кэш-ответ с d4 и Nf6, остальной русский текст без иероглифов."

    async def _fake_lookup(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(text=dirty)

    # Если очистка кэша не сработает, тест уйдёт в fake-LLM и упадёт на
    # model != semantic-cache — реальная сеть/LM Studio не нужна.
    fake = FakeStreamingLLM(LEGAL_CHUNKS)
    monkeypatch.setattr("app.chat.streaming.lookup", _fake_lookup)
    monkeypatch.setattr("app.chat.streaming.get_llm_client", lambda level, cfg: fake)
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/chat/stream",
                json={
                    "analysis_id": analysis_id,
                    "level": "club",
                    "message": "Что было в партии?",
                },
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            names = [name for name, _ in events]
            assert events[0][0] == "meta"
            assert events[0][1]["model"] == "semantic-cache"
            assert names[-1] == "done"
            deltas = [
                str(payload["text"]) for name, payload in events if name == "delta"
            ]
            assert all(_CJK_PATTERN.search(chunk) is None for chunk in deltas)
            assert "".join(deltas) == clean
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_chat_stream_rejected_cache_falls_back_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-060/итерация 57: cache-запись почти целиком из CJK → регенерация."""
    only_cjk = "这是中文。"

    async def _fake_lookup(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(text=only_cjk)

    fake = FakeStreamingLLM(LEGAL_CHUNKS)
    monkeypatch.setattr("app.chat.streaming.lookup", _fake_lookup)
    monkeypatch.setattr("app.chat.streaming.get_llm_client", lambda level, cfg: fake)
    with _client() as client:
        analyze = client.post("/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"})
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.post(
                "/v1/chat/stream",
                json={
                    "analysis_id": analysis_id,
                    "level": "club",
                    "message": "Что было в партии?",
                },
            )
            assert response.status_code == 200, response.text
            events = _events(response.text)
            assert events[0][1]["model"] == "fake-stream"
            deltas = [
                str(payload["text"]) for name, payload in events if name == "delta"
            ]
            assert "".join(deltas) == "Партия началась с d4 и Nf6."
            assert all(_CJK_PATTERN.search(chunk) is None for chunk in deltas)
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
