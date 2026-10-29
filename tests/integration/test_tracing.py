"""Trace-middleware и W3C traceparent (T-030).

БД не нужна: отдельное Starlette-приложение с `TraceMiddleware` отдаёт
`request.state` и contextvars, чтобы проверить генерацию/парсинг
trace-контекста, заголовок `X-Trace-Id` и `X-Request-Id`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.tracing import (
    TraceContext,
    TraceMiddleware,
    current_request_id,
    current_span_id,
    current_trace_id,
    new_trace_context,
    parse_traceparent,
    traceparent_header,
)

TRACE_HEADER = "X-Trace-Id"


def _app() -> Starlette:
    async def state(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "trace_id": getattr(request.state, "trace_id", None),
                "span_id": getattr(request.state, "span_id", None),
                "traceparent": getattr(request.state, "traceparent", None),
                "request_id": getattr(request.state, "request_id", None),
                "ctx_trace": current_trace_id(),
                "ctx_span": current_span_id(),
                "ctx_request": current_request_id(),
                "log_trace": structlog.contextvars.get_contextvars().get(
                    "trace_id"
                ),
            }
        )

    app = Starlette(routes=[Route("/state", state, methods=["GET"])])
    app.add_middleware(TraceMiddleware)
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_app())


def test_generates_trace_id_when_traceparent_absent(client: TestClient) -> None:
    response = client.get("/state")

    assert response.status_code == 200
    header = response.headers[TRACE_HEADER]
    trace_uuid = UUID(header)
    assert trace_uuid.version == 4

    body = response.json()
    assert body["trace_id"] == header
    assert body["ctx_trace"] == header
    assert body["log_trace"] == header
    assert len(body["span_id"]) == 16
    assert int(body["span_id"], 16) >= 0  # валидный hex
    assert body["ctx_span"] == body["span_id"]
    assert body["traceparent"] == f"00-{trace_uuid.hex}-{body['span_id']}-01"


def test_uses_incoming_traceparent(client: TestClient) -> None:
    trace_hex = uuid4().hex
    span_hex = "1234567890abcdef"
    traceparent = f"00-{trace_hex}-{span_hex}-01"

    response = client.get("/state", headers={"traceparent": traceparent})

    assert response.status_code == 200
    assert response.headers[TRACE_HEADER] == str(UUID(hex=trace_hex))
    body = response.json()
    assert body["trace_id"] == str(UUID(hex=trace_hex))
    assert body["span_id"] == span_hex
    assert body["traceparent"] == traceparent
    assert body["ctx_span"] == span_hex


@pytest.mark.parametrize(
    "traceparent",
    [
        "garbage",
        "00-too-short-01",
        # Версия кроме 00 по W3C должна игнорироваться.
        "01-00000000000000000000000000000001-0000000000000001-01",
        # Нулевые trace/span id невалидны.
        "00-" + "0" * 32 + "-1234567890abcdef-01",
        "00-" + "a" * 32 + "-" + "0" * 16 + "-01",
        "00-" + "a" * 32 + "-1234567890abcdef-01-extra",
    ],
)
def test_invalid_traceparent_is_replaced(
    client: TestClient, traceparent: str
) -> None:
    response = client.get("/state", headers={"traceparent": traceparent})

    assert response.status_code == 200
    header = response.headers[TRACE_HEADER]
    assert UUID(header).version == 4
    assert response.json()["trace_id"] == header


def test_uppercase_traceparent_is_accepted(client: TestClient) -> None:
    trace_hex = uuid4().hex
    span_hex = "abcdef1234567890"
    traceparent = f"00-{trace_hex.upper()}-{span_hex.upper()}-01"

    response = client.get("/state", headers={"traceparent": traceparent})

    assert response.headers[TRACE_HEADER] == str(UUID(hex=trace_hex))
    assert response.json()["span_id"] == span_hex


def test_request_id_header_is_propagated(client: TestClient) -> None:
    response = client.get("/state", headers={"X-Request-Id": "req-abc-123"})

    body = response.json()
    assert body["request_id"] == "req-abc-123"
    assert body["ctx_request"] == "req-abc-123"


def test_request_id_is_truncated_to_column_width(client: TestClient) -> None:
    response = client.get("/state", headers={"X-Request-Id": "x" * 100})

    assert len(response.json()["request_id"]) == 64


def test_trace_header_present_on_error_response(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert UUID(response.headers[TRACE_HEADER]).version == 4


def test_each_request_has_own_trace_id(client: TestClient) -> None:
    first = client.get("/state").headers[TRACE_HEADER]
    second = client.get("/state").headers[TRACE_HEADER]

    assert first != second


# --- чистые функции -------------------------------------------------------


def test_parse_traceparent_accepts_valid_value() -> None:
    trace_hex = uuid4().hex
    context = parse_traceparent(f"00-{trace_hex}-1234567890abcdef-01")

    assert context is not None
    assert context.trace_id == UUID(hex=trace_hex)
    assert context.span_id == "1234567890abcdef"
    assert context.flags == "01"


@pytest.mark.parametrize(
    "raw", [None, "", "  ", "garbage", "00-" + "0" * 32 + "-" + "a" * 16 + "-01"]
)
def test_parse_traceparent_rejects_invalid_value(raw: str | None) -> None:
    assert parse_traceparent(raw) is None


def test_new_trace_context_is_w3c_compatible() -> None:
    context = new_trace_context()

    assert context.trace_id.version == 4
    assert len(context.span_id) == 16
    assert int(context.span_id, 16) >= 0
    assert context.traceparent.startswith("00-")
    assert context.traceparent.endswith("-01")
    assert context.trace_id.hex in context.traceparent


def test_traceparent_header_formats_ids() -> None:
    trace_id = uuid4()

    header = traceparent_header(trace_id, "ABCDEF0123456789")

    assert header == f"00-{trace_id.hex}-abcdef0123456789-01"
    assert parse_traceparent(header) is not None


def test_trace_context_is_immutable_dataclass() -> None:
    context = TraceContext(trace_id=uuid4(), span_id="a" * 16)

    with pytest.raises(AttributeError):
        context.span_id = "b" * 16  # type: ignore[misc]


# --- KI-047: X-Trace-Id на необработанных 500 -----------------------------


def test_unhandled_500_has_trace_id_header() -> None:
    """ServerErrorMiddleware не пропускает ответ через TraceMiddleware,
    поэтому заголовок добавляет exception handler из app.main."""
    from fastapi import FastAPI

    from app.main import _unhandled_error_handler

    api = FastAPI()
    api.add_middleware(TraceMiddleware)
    api.add_exception_handler(Exception, _unhandled_error_handler)

    @api.get("/boom")
    async def boom() -> JSONResponse:  # pragma: no cover - всегда падает
        raise RuntimeError("boom")

    trace_hex = uuid4().hex
    traceparent = f"00-{trace_hex}-1234567890abcdef-01"
    with TestClient(api, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"traceparent": traceparent})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal Server Error",
        "code": "internal_error",
    }
    assert response.headers[TRACE_HEADER] == str(UUID(hex=trace_hex))
