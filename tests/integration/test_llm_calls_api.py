"""Запись вызовов LLM в `llm_calls` через API (T-030).

LLM подменяется на уровне HTTP (`httpx.MockTransport`): реальный
`OpenAICompatibleClient` остаётся, поэтому проверяется вся цепочка
«эндпоинт → контекст вызова → recorder → llm_calls». Остальное настоящее:
FastAPI-приложение, PostgreSQL, Stockfish для `/v1/analyze`. Проверяем:

* строку `llm_calls` после sync- и stream-вызова (операция, пользователь,
  партия, модель, токены, стоимость, статус, prompt_slug/version);
* `trace_id` из входящего `traceparent` и заголовок `X-Trace-Id` в ответе;
* `UNIQUE (trace_id, span_id)`: два вызова в одном трейсе дают разные
  `span_id` (каждая попытка LLM — отдельный спан);
* битый `traceparent` → новый trace id, но запись всё равно создаётся.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Game, Level, LlmCall, Prompt, SemanticCache
from app.llm import OpenAICompatibleClient
from app.llm.calls import wait_for_pending_llm_calls
from app.llm.warmup import warmup_local_llm
from app.main import create_app

SHORT_PGN = (
    '[Event "LLMOps"]\n[White "White"]\n[Black "Black"]\n[Result "1/2-1/2"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 1/2-1/2"
)
#: Фраза без SAN-ходов: валидатор её принимает, retry не нужен.
SAFE_REPLY = "Партия началась спокойно."
CHAT_BODY = {
    "analysis_id": 1,
    "level": "club",
    "message": "Кто играл белыми?",
}
#: Модель фейкового провайдера (в llm_calls.model).
FAKE_MODEL = "fake-llm"
#: (in, out) токены из фейкового completion-ответа.
FAKE_TOKENS_IN = 11
FAKE_TOKENS_OUT = 7

Handler = Callable[[httpx.Request], httpx.Response]


def _completion_handler(requests: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": FAKE_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": SAFE_REPLY},
                    }
                ],
                "usage": {
                    "prompt_tokens": FAKE_TOKENS_IN,
                    "completion_tokens": FAKE_TOKENS_OUT,
                },
            },
        )

    return handler


def _stream_handler(requests: list[httpx.Request]) -> Handler:
    body = (
        'data: {"choices":[{"delta":{"content":"Партия "}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"началась спокойно."}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

    return handler


def _mock_llm(
    handler: Handler,
) -> tuple[OpenAICompatibleClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OpenAICompatibleClient(
        base_url="http://llm.test/v1",
        model=FAKE_MODEL,
        provider_name="fake",
        client=http_client,
        price_in_usd_per_1m=1.0,
        price_out_usd_per_1m=2.0,
    )
    return llm, http_client


async def _db_ping() -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def _db_available() -> bool:
    try:
        asyncio.run(_db_ping())
    except Exception:
        return False
    return True


def _stockfish_available() -> bool:
    return Path(get_settings().stockfish_path).is_file()


pytestmark = pytest.mark.skipif(
    not (_db_available() and _stockfish_available()),
    reason="PostgreSQL/Stockfish недоступны",
)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    settings = get_settings().model_copy(
        update={
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
        }
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _analyze(client: TestClient) -> int:
    response = client.post(
        "/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"}
    )
    assert response.status_code == 200, response.text
    return int(response.json()["analysis_id"])


def _test_user_id(client: TestClient) -> int:
    response = client.get("/auth/me")
    assert response.status_code == 200, response.text
    return int(response.json()["id"])


def _chat(client: TestClient, analysis_id: int, headers: dict[str, str]) -> None:
    body = dict(CHAT_BODY, analysis_id=analysis_id)
    response = client.post("/v1/chat", json=body, headers=headers)
    assert response.status_code == 200, response.text


async def _wait_for_calls(
    trace_id: UUID, *, expected: int = 1, timeout_s: float = 5.0
) -> list[LlmCall]:
    """Дождаться фоновой записи: recorder пишет в отдельной задаче."""
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            async with factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(LlmCall)
                            .where(LlmCall.trace_id == trace_id)
                            .order_by(LlmCall.id)
                        )
                    ).all()
                )
            if len(rows) >= expected or time.monotonic() >= deadline:
                return rows
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def _prompt_version(level: str) -> int | None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            version = await session.scalar(
                select(Prompt.version).where(Prompt.level == level)
            )
            return int(version) if version is not None else None
    finally:
        await engine.dispose()


async def _cleanup(
    trace_ids: list[UUID], game_id: int, http_client: httpx.AsyncClient
) -> None:
    await http_client.aclose()
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            if trace_ids:
                await connection.execute(
                    delete(LlmCall).where(LlmCall.trace_id.in_(trace_ids))
                )
            await connection.execute(
                Game.__table__.delete().where(Game.id == game_id)
            )
    finally:
        await engine.dispose()


def test_chat_sync_writes_llm_call_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.chat.service.get_llm_client", lambda level, cfg: llm
    )
    analysis_id = _analyze(client)
    actor_id = _test_user_id(client)
    trace_hex = uuid4().hex
    traceparent = f"00-{trace_hex}-0123456789abcdef-01"
    trace_id = UUID(hex=trace_hex)
    try:
        _chat(
            client,
            analysis_id,
            {
                "traceparent": traceparent,
                "X-Request-Id": "itest-request-1",
            },
        )
        assert len(requests) == 1
        rows = asyncio.run(_wait_for_calls(trace_id))
        version = asyncio.run(_prompt_version("club"))

        assert len(rows) == 1
        row = rows[0]
        assert row.trace_id == trace_id
        assert len(row.span_id) == 16
        assert row.request_id == "itest-request-1"
        assert row.operation == "chat"
        assert row.actor_id == actor_id
        assert row.game_id == analysis_id
        assert row.commentary_id is None
        assert row.provider == "fake"
        assert row.model == FAKE_MODEL
        assert row.level == Level.club
        assert row.prompt_slug == "club"
        assert row.prompt_version == version
        assert row.tokens_in == FAKE_TOKENS_IN
        assert row.tokens_out == FAKE_TOKENS_OUT
        assert float(row.cost_usd) == pytest.approx(0.000025)
        assert row.latency_ms >= 0
        assert row.cache_hit is False
        assert row.status == "ok"
        assert row.error_code is None
        assert row.created_at is not None
    finally:
        asyncio.run(_cleanup([trace_id], analysis_id, http_client))


def test_response_has_x_trace_id_matching_traceparent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.chat.service.get_llm_client", lambda level, cfg: llm
    )
    analysis_id = _analyze(client)
    trace_hex = uuid4().hex
    traceparent = f"00-{trace_hex}-aaaaaaaaaaaaaaaa-01"
    trace_id = UUID(hex=trace_hex)
    try:
        response = client.post(
            "/v1/chat",
            json=dict(CHAT_BODY, analysis_id=analysis_id),
            headers={"traceparent": traceparent},
        )
        assert response.status_code == 200, response.text
        assert response.headers["X-Trace-Id"] == str(trace_id)
        rows = asyncio.run(_wait_for_calls(trace_id))
        assert len(rows) == 1
        assert rows[0].trace_id == trace_id
    finally:
        asyncio.run(_cleanup([trace_id], analysis_id, http_client))


def test_two_calls_share_trace_but_not_span(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.chat.service.get_llm_client", lambda level, cfg: llm
    )
    analysis_id = _analyze(client)
    trace_hex = uuid4().hex
    traceparent = f"00-{trace_hex}-bbbbbbbbbbbbbbbb-01"
    trace_id = UUID(hex=trace_hex)
    headers = {"traceparent": traceparent}
    try:
        _chat(client, analysis_id, headers)
        _chat(client, analysis_id, headers)
        rows = asyncio.run(_wait_for_calls(trace_id, expected=2))

        assert len(rows) == 2
        assert {row.trace_id for row in rows} == {trace_id}
        assert len({row.span_id for row in rows}) == 2
        assert all(row.status == "ok" for row in rows)
    finally:
        asyncio.run(_cleanup([trace_id], analysis_id, http_client))


def test_invalid_traceparent_still_records_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.chat.service.get_llm_client", lambda level, cfg: llm
    )
    analysis_id = _analyze(client)
    trace_id: UUID | None = None
    try:
        response = client.post(
            "/v1/chat",
            json=dict(CHAT_BODY, analysis_id=analysis_id),
            headers={"traceparent": "not-a-traceparent"},
        )
        assert response.status_code == 200, response.text
        header = response.headers["X-Trace-Id"]
        trace_id = UUID(header)
        assert trace_id.version == 4

        rows = asyncio.run(_wait_for_calls(trace_id))
        assert len(rows) == 1
        assert rows[0].trace_id == trace_id
        assert rows[0].operation == "chat"
    finally:
        asyncio.run(
            _cleanup(
                [trace_id] if trace_id else [], analysis_id, http_client
            )
        )


def test_chat_stream_writes_llm_call_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_stream_handler(requests))
    monkeypatch.setattr(
        "app.chat.streaming.get_llm_client", lambda level, cfg: llm
    )
    analysis_id = _analyze(client)
    trace_hex = uuid4().hex
    traceparent = f"00-{trace_hex}-cccccccccccccccc-01"
    trace_id = UUID(hex=trace_hex)
    try:
        response = client.post(
            "/v1/chat/stream",
            json=dict(CHAT_BODY, analysis_id=analysis_id),
            headers={"traceparent": traceparent},
        )
        assert response.status_code == 200, response.text
        assert "done" in response.text
        assert response.headers["X-Trace-Id"] == str(trace_id)

        rows = asyncio.run(_wait_for_calls(trace_id))
        assert len(rows) == 1
        row = rows[0]
        assert row.operation == "chat"
        assert row.status == "ok"
        assert row.game_id == analysis_id
        assert row.level == Level.club
        assert row.tokens_in > 0
        assert row.tokens_out > 0
    finally:
        asyncio.run(_cleanup([trace_id], analysis_id, http_client))


# --- KI-046: warmup, commentary_id, cache_hit ------------------------------


def _cache_client() -> TestClient:
    """TestClient с включённым semantic cache (эмбеддер — hash)."""
    settings = get_settings().model_copy(
        update={
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": True,
            "embedding_provider": "hash",
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
        }
    )
    return TestClient(create_app(settings))


async def _wait_for_attached(
    trace_id: UUID, commentary_id: int, *, timeout_s: float = 5.0
) -> list[LlmCall]:
    """Дождаться фонового `attach_commentary_id` (UPDATE идёт после INSERT-а)."""
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            async with factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(LlmCall)
                            .where(LlmCall.trace_id == trace_id)
                            .order_by(LlmCall.id)
                        )
                    ).all()
                )
            if rows and all(row.commentary_id == commentary_id for row in rows):
                return rows
            if time.monotonic() >= deadline:
                return rows
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


async def _warmup_rows() -> list[LlmCall]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return list(
                (
                    await session.scalars(
                        select(LlmCall)
                        .where(
                            LlmCall.operation == "warmup",
                            LlmCall.provider == "fake",
                            LlmCall.model == FAKE_MODEL,
                        )
                        .order_by(LlmCall.id)
                    )
                ).all()
            )
    finally:
        await engine.dispose()


async def _delete_llm_call_ids(ids: list[int]) -> None:
    if not ids:
        return
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(LlmCall).where(LlmCall.id.in_(ids))
            )
    finally:
        await engine.dispose()


async def _max_cache_id() -> int:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            value = await session.scalar(
                select(func.coalesce(func.max(SemanticCache.id), 0))
            )
            return int(value or 0)
    finally:
        await engine.dispose()


async def _delete_cache_after(cache_id: int) -> None:
    """Убрать записи semantic cache, созданные тестом (id > отметки)."""
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(SemanticCache).where(SemanticCache.id > cache_id)
            )
    finally:
        await engine.dispose()


def _commentary_id_from_sse(body: str) -> int:
    match = re.search(r'"commentary_id":\s*(\d+)', body)
    assert match is not None, f"в SSE нет commentary_id: {body[-500:]}"
    return int(match.group(1))


def test_warmup_writes_llm_call_row() -> None:
    """KI-046: прогрев LLM пишется в llm_calls с operation=warmup."""
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    rows: list[LlmCall] = []

    async def run_warmup() -> bool:
        ok = await warmup_local_llm(client=llm)
        await wait_for_pending_llm_calls()
        return ok

    try:
        assert asyncio.run(run_warmup()) is True
        assert len(requests) == 1
        rows = asyncio.run(_warmup_rows())
        assert len(rows) == 1
        row = rows[0]
        assert row.operation == "warmup"
        assert row.provider == "fake"
        assert row.model == FAKE_MODEL
        assert row.status == "ok"
        assert row.tokens_in == FAKE_TOKENS_IN
        assert row.tokens_out == FAKE_TOKENS_OUT
        assert row.actor_id is None
        assert row.game_id is None
        assert row.commentary_id is None
        assert row.cache_hit is False
        assert row.level is None
        assert row.prompt_slug is None
        # Вне HTTP trace id сгенерирован recorder-ом (fallback, UUID v4).
        assert row.trace_id.version == 4
    finally:
        asyncio.run(_delete_llm_call_ids([row.id for row in rows]))
        asyncio.run(http_client.aclose())


def test_commentary_sync_attaches_commentary_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KI-046: sync-комментарий → llm_calls.commentary_id == id комментария."""
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.commentary.service.get_llm_client", lambda level, cfg: llm
    )
    analysis_id = _analyze(client)
    trace_hex = uuid4().hex
    trace_id = UUID(hex=trace_hex)
    try:
        response = client.post(
            "/v1/commentary",
            json={"analysis_id": analysis_id, "level": "club"},
            headers={"traceparent": f"00-{trace_hex}-0123456789abcdef-01"},
        )
        assert response.status_code == 200, response.text
        commentary_id = int(response.json()["commentary_id"])
        rows = asyncio.run(_wait_for_attached(trace_id, commentary_id))

        assert len(rows) == 1
        row = rows[0]
        assert row.operation == "commentary"
        assert row.commentary_id == commentary_id
        assert row.game_id == analysis_id
        assert row.actor_id is not None
    finally:
        asyncio.run(_cleanup([trace_id], analysis_id, http_client))


def test_commentary_stream_attaches_commentary_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KI-046: stream-комментарий → commentary_id проставляется после commit."""
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_stream_handler(requests))
    monkeypatch.setattr(
        "app.commentary.streaming.get_llm_client", lambda level, cfg: llm
    )
    analysis_id = _analyze(client)
    trace_hex = uuid4().hex
    trace_id = UUID(hex=trace_hex)
    try:
        response = client.post(
            "/v1/commentary/stream",
            json={"analysis_id": analysis_id, "level": "club"},
            headers={"traceparent": f"00-{trace_hex}-aaaaaaaaaaaaaaaa-01"},
        )
        assert response.status_code == 200, response.text
        assert "done" in response.text
        commentary_id = _commentary_id_from_sse(response.text)
        rows = asyncio.run(_wait_for_attached(trace_id, commentary_id))

        assert len(rows) == 1
        row = rows[0]
        assert row.operation == "commentary"
        assert row.commentary_id == commentary_id
        assert row.game_id == analysis_id
    finally:
        asyncio.run(_cleanup([trace_id], analysis_id, http_client))


def test_cache_hit_commentary_writes_llm_call_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-046: cache-hit комментария — строка llm_calls с cache_hit=True."""
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.commentary.service.get_llm_client", lambda level, cfg: llm
    )
    cache_mark = asyncio.run(_max_cache_id())
    with _cache_client() as client:
        analysis_id = _analyze(client)
        first_hex, second_hex = uuid4().hex, uuid4().hex
        first_trace = UUID(hex=first_hex)
        second_trace = UUID(hex=second_hex)
        try:
            first = client.post(
                "/v1/commentary",
                json={"analysis_id": analysis_id, "level": "club"},
                headers={"traceparent": f"00-{first_hex}-0123456789abcdef-01"},
            )
            assert first.status_code == 200, first.text
            assert len(requests) == 1

            second = client.post(
                "/v1/commentary",
                json={"analysis_id": analysis_id, "level": "club"},
                headers={"traceparent": f"00-{second_hex}-aaaaaaaaaaaaaaaa-01"},
            )
            assert second.status_code == 200, second.text
            assert len(requests) == 1, "второй запрос должен прийти из кэша"
            commentary_id = int(second.json()["commentary_id"])

            rows = asyncio.run(_wait_for_attached(second_trace, commentary_id))
            assert len(rows) == 1
            row = rows[0]
            assert row.trace_id == second_trace
            assert row.operation == "commentary"
            assert row.game_id == analysis_id
            assert row.commentary_id == commentary_id
            assert row.cache_hit is True
            assert row.provider == "cache"
            assert row.model == "semantic-cache"
            assert row.tokens_in == 0
            assert row.tokens_out == 0
            assert float(row.cost_usd) == 0.0
            assert row.status == "ok"
            assert row.prompt_slug == "club"
            assert row.prompt_version == asyncio.run(_prompt_version("club"))
            assert row.latency_ms >= 0
        finally:
            asyncio.run(
                _cleanup([first_trace, second_trace], analysis_id, http_client)
            )
            asyncio.run(_delete_cache_after(cache_mark))


def test_cache_hit_chat_writes_llm_call_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-046: cache-hit чата — строка llm_calls (operation=chat)."""
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.chat.service.get_llm_client", lambda level, cfg: llm
    )
    cache_mark = asyncio.run(_max_cache_id())
    with _cache_client() as client:
        analysis_id = _analyze(client)
        first_hex, second_hex = uuid4().hex, uuid4().hex
        first_trace = UUID(hex=first_hex)
        second_trace = UUID(hex=second_hex)
        body = dict(CHAT_BODY, analysis_id=analysis_id)
        try:
            _chat(
                client,
                analysis_id,
                {"traceparent": f"00-{first_hex}-0123456789abcdef-01"},
            )
            assert len(requests) == 1

            second = client.post(
                "/v1/chat",
                json=body,
                headers={"traceparent": f"00-{second_hex}-aaaaaaaaaaaaaaaa-01"},
            )
            assert second.status_code == 200, second.text
            assert len(requests) == 1, "второй вопрос должен прийти из кэша"

            rows = asyncio.run(_wait_for_calls(second_trace))
            assert len(rows) == 1
            row = rows[0]
            assert row.trace_id == second_trace
            assert row.operation == "chat"
            assert row.game_id == analysis_id
            assert row.cache_hit is True
            assert row.provider == "cache"
            assert row.model == "semantic-cache"
            assert row.tokens_in == 0
            assert row.tokens_out == 0
            assert row.status == "ok"
            assert row.prompt_slug == "club"
            assert row.prompt_version == asyncio.run(_prompt_version("club"))
        finally:
            asyncio.run(
                _cleanup([first_trace, second_trace], analysis_id, http_client)
            )
            asyncio.run(_delete_cache_after(cache_mark))


def test_stream_cache_hit_writes_cache_row_and_attaches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KI-046: cache-hit в SSE-комментарии — cache_hit=True и commentary_id."""
    requests: list[httpx.Request] = []
    llm, http_client = _mock_llm(_completion_handler(requests))
    monkeypatch.setattr(
        "app.commentary.service.get_llm_client", lambda level, cfg: llm
    )
    cache_mark = asyncio.run(_max_cache_id())
    with _cache_client() as client:
        analysis_id = _analyze(client)
        first_hex, second_hex = uuid4().hex, uuid4().hex
        first_trace = UUID(hex=first_hex)
        second_trace = UUID(hex=second_hex)
        try:
            # Seed: sync-комментарий с реальным (мок) LLM кладёт ответ в кэш.
            seed = client.post(
                "/v1/commentary",
                json={"analysis_id": analysis_id, "level": "club"},
                headers={"traceparent": f"00-{first_hex}-0123456789abcdef-01"},
            )
            assert seed.status_code == 200, seed.text
            assert len(requests) == 1

            streamed = client.post(
                "/v1/commentary/stream",
                json={"analysis_id": analysis_id, "level": "club"},
                headers={"traceparent": f"00-{second_hex}-aaaaaaaaaaaaaaaa-01"},
            )
            assert streamed.status_code == 200, streamed.text
            assert "done" in streamed.text
            commentary_id = _commentary_id_from_sse(streamed.text)

            rows = asyncio.run(_wait_for_attached(second_trace, commentary_id))
            assert len(rows) == 1
            row = rows[0]
            assert row.operation == "commentary"
            assert row.commentary_id == commentary_id
            assert row.cache_hit is True
            assert row.provider == "cache"
            assert row.model == "semantic-cache"
            assert row.tokens_in == 0
            assert row.tokens_out == 0
        finally:
            asyncio.run(
                _cleanup([first_trace, second_trace], analysis_id, http_client)
            )
            asyncio.run(_delete_cache_after(cache_mark))

