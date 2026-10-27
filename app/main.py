"""FastAPI-приложение ChessAnnotator.

Собирает роутеры, обработчик доменных ошибок, rate limiting и lifespan
(пул движков + Redis). `create_app()` принимает настройки, чтобы тесты
могли собрать приложение с другим лимитом/размером пула.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.ratelimit import RateLimitMiddleware, RedisRateLimiter
from app.api.v1 import analyze, health
from app.config import Settings, get_settings
from app.engine.pool import EnginePool
from app.errors import AppError, EngineError

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создать приложение (по умолчанию — с настройками из окружения)."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine_pool = EnginePool(cfg)
        redis_client = aioredis.from_url(  # type: ignore[no-untyped-call]
            cfg.redis_url, decode_responses=True
        )
        app.state.redis = redis_client
        app.state.rate_limiter = (
            RedisRateLimiter(
                redis_client,
                limit=cfg.rate_limit_requests,
                window_s=cfg.rate_limit_window_s,
            )
            if cfg.rate_limit_enabled
            else None
        )
        logger.info(
            "app_started",
            env=cfg.app_env,
            engine_pool_size=cfg.engine_pool_size,
            rate_limit=cfg.rate_limit_enabled,
        )
        try:
            yield
        finally:
            await app.state.engine_pool.aclose()
            await redis_client.aclose()
            logger.info("app_stopped")

    app = FastAPI(
        title="ChessAnnotator API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Порядок важен: последним добавленный middleware — самый внешний.
    # CORS должен оборачивать rate limiting, чтобы браузер видел 429.
    app.add_middleware(
        RateLimitMiddleware,
        protected=[("POST", f"{cfg.api_prefix}/analyze")],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(AppError, _app_error_handler)

    app.include_router(health.router, prefix=cfg.api_prefix)
    app.include_router(analyze.router, prefix=cfg.api_prefix)
    return app


def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Единый ответ на доменные ошибки: `{"detail", "code"}` + HTTP-статус."""
    app_error = exc if isinstance(exc, AppError) else AppError(str(exc))
    status = app_error.http_status
    detail = (
        "Engine unavailable"
        if isinstance(app_error, EngineError) and status >= 500
        else app_error.message
    )
    logger.warning(
        "app_error",
        code=app_error.code,
        status=status,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "code": app_error.code},
    )


app = create_app()
