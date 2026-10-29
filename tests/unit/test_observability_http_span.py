"""HTTP-спан `http_request`: маршрут и статус для spanmetrics (T-109b).

`TraceMiddleware` выставляет `http.request.method`, `http.route` и
`http.response.status_code` — по этим атрибутам коннектор `spanmetrics` в
OpenTelemetry Collector считает latency/error rate для дашборда Grafana.
Без них панели `/v1/commentary` и `/v1/analyze` были бы пустыми.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.tracing import TRACE_HEADER, TraceMiddleware


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


async def _ok(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def _boom(request: Request) -> PlainTextResponse:
    raise RuntimeError("boom")


def _span_attributes(exporter: InMemorySpanExporter) -> dict[str, object]:
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = spans[0].attributes
    assert attributes is not None
    return dict(attributes)


def test_middleware_sets_route_method_and_status() -> None:
    provider, exporter = _provider()
    app = Starlette(
        routes=[Route("/v1/commentary", _ok, methods=["POST"])]
    )
    try:
        with TestClient(
            TraceMiddleware(app, tracer=provider.get_tracer("test"))
        ) as client:
            response = client.post("/v1/commentary")
            assert response.status_code == 200
            assert TRACE_HEADER in response.headers
        attributes = _span_attributes(exporter)
        assert attributes["http.route"] == "/v1/commentary"
        assert attributes["http.request.method"] == "POST"
        assert attributes["http.response.status_code"] == 200
        spans = exporter.get_finished_spans()
        assert spans[0].name == "POST /v1/commentary"
    finally:
        provider.shutdown()


def test_middleware_uses_full_prefix_of_included_router() -> None:
    """FastAPI include_router: `http.route` — `/v1/analyze`, а не `/analyze`.

    В FastAPI 0.162 `scope["route"].path` — исходный путь без префикса;
    полный путь лежит в эффективном контексте, иначе панель дашборда
    фильтрует несуществующий маршрут.
    """
    provider, exporter = _provider()
    router = APIRouter()

    @router.post("/analyze")
    async def analyze(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = FastAPI(
        telemetry={"tracing": False, "metrics": False, "logs": False}
    )
    app.include_router(router, prefix="/v1")
    try:
        with TestClient(
            TraceMiddleware(app, tracer=provider.get_tracer("test"))
        ) as client:
            response = client.post("/v1/analyze")
            assert response.status_code == 200
        spans = [
            span
            for span in exporter.get_finished_spans()
            if span.name.startswith("POST ")
        ]
        assert len(spans) == 1
        attributes = spans[0].attributes
        assert attributes is not None
        assert attributes["http.route"] == "/v1/analyze"
        assert spans[0].name == "POST /v1/analyze"
    finally:
        provider.shutdown()


def test_middleware_marks_5xx_as_error() -> None:
    provider, exporter = _provider()
    app = Starlette(routes=[Route("/v1/analyze", _boom, methods=["POST"])])
    try:
        with TestClient(
            TraceMiddleware(app, tracer=provider.get_tracer("test")),
            raise_server_exceptions=False,
        ) as client:
            response = client.post("/v1/analyze")
            assert response.status_code == 500
        attributes = _span_attributes(exporter)
        assert attributes["http.route"] == "/v1/analyze"
        # Если ServerErrorMiddleware успел отправить ответ, статус есть;
        # при пробросе исключения наружу (TestClient) спан всё равно ERROR.
        if "http.response.status_code" in attributes:
            assert attributes["http.response.status_code"] == 500
        spans = exporter.get_finished_spans()
        assert spans[0].status.status_code.name == "ERROR"
    finally:
        provider.shutdown()
