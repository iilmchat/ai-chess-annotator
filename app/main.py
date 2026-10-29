"""FastAPI-приложение ChessAnnotator.

Собирает роутеры, обработчик доменных ошибок, rate limiting и lifespan
(пул движков + Redis). `create_app()` принимает настройки, чтобы тесты
могли собрать приложение с другим лимитом/размером пула.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.admin import AdminPathNormalizer, create_admin
from app.api.ratelimit import RateLimitMiddleware, RedisRateLimiter
from app.api.v1 import (
    admin_exports,
    analyze,
    auth,
    chat,
    commentary,
    games,
    health,
    ingest,
    users,
)
from app.cache import cleanup_expired_cache
from app.config import Settings, get_settings
from app.db.session import SessionLocal
from app.db.session import engine as db_engine
from app.engine.pool import EnginePool
from app.errors import AppError, EngineError
from app.finops import TokenBudgetMiddleware, TokenUsageTracker
from app.llm.warmup import warmup_local_llm
from app.observability import configure_telemetry, shutdown_telemetry
from app.prompts import seed_prompts, warm_prompt_cache
from app.prompts.pubsub import watch_prompt_updates
from app.rag.context import make_rag_context_builder
from app.tracing import TraceMiddleware, current_trace_id
from app.web import SPAStaticFiles

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создать приложение (по умолчанию — с настройками из окружения)."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # T-030: трейсинг включается только при OTEL_ENABLED=true;
        # без провайдера спаны в клиенте — no-op.
        app.state.telemetry_provider = configure_telemetry(cfg)
        app.state.engine_pool = EnginePool(cfg)
        redis_client = aioredis.from_url(  # type: ignore[no-untyped-call]
            cfg.redis_url, decode_responses=True
        )
        app.state.redis = redis_client
        # T-029: сид/прогрев промптов из БД. Сбой БД не блокирует старт —
        # load_system_prompt в этом случае падает обратно на .txt-файлы.
        async with SessionLocal() as prompt_session:
            try:
                await seed_prompts(prompt_session)
                await warm_prompt_cache(prompt_session)
            except Exception as exc:
                logger.warning("prompts_init_failed", error=str(exc))
        # T-032: физическая чистка записей старше TTL при старте.
        async with SessionLocal() as cache_session:
            try:
                deleted = await cleanup_expired_cache(
                    cache_session, settings=cfg
                )
                if deleted:
                    await cache_session.commit()
                logger.info("semantic_cache_cleanup", deleted=deleted)
            except Exception as exc:
                logger.warning("semantic_cache_cleanup_failed", error=str(exc))
        app.state.rag_context_builder = make_rag_context_builder(cfg)
        app.state.rate_limiter = (
            RedisRateLimiter(
                redis_client,
                limit=cfg.rate_limit_requests,
                window_s=cfg.rate_limit_window_s,
            )
            if cfg.rate_limit_enabled
            else None
        )
        app.state.token_usage_tracker = (
            TokenUsageTracker(
                redis_client,
                free_limit=cfg.daily_token_limit_free,
                pro_limit=cfg.daily_token_limit_pro,
            )
            if cfg.token_budget_enabled
            else None
        )
        # KI-032: каждый воркер слушает канал и сбрасывает локальный кэш
        # промптов. Недоступный Redis просто логируется.
        prompt_updates_task: asyncio.Task[None] | None = None
        if cfg.prompt_pubsub_enabled:
            prompt_updates_task = asyncio.create_task(
                watch_prompt_updates(redis_client), name="prompt-updates"
            )
            app.state.prompt_updates_task = prompt_updates_task
        # KI-022: грузим локальную модель в фоне, чтобы первый реальный
        # запрос не ждал ~12 с. Отсутствие LM Studio не ломает старт.
        warmup_task: asyncio.Task[bool] | None = None
        if cfg.llm_warmup_enabled:
            warmup_task = asyncio.create_task(
                warmup_local_llm(cfg), name="llm-warmup"
            )
            app.state.llm_warmup = warmup_task
        logger.info(
            "app_started",
            env=cfg.app_env,
            engine_pool_size=cfg.engine_pool_size,
            rate_limit=cfg.rate_limit_enabled,
            llm_warmup=cfg.llm_warmup_enabled,
        )
        try:
            yield
        finally:
            if warmup_task is not None and not warmup_task.done():
                warmup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await warmup_task
            if prompt_updates_task is not None and not prompt_updates_task.done():
                prompt_updates_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_updates_task
            await app.state.engine_pool.aclose()
            await redis_client.aclose()
            # Закрываем пул соединений БД внутри текущего event loop: иначе
            # соединения asyncpg остаются привязанными к закрытому циклу
            # (актуально для тестов с несколькими TestClient).
            await db_engine.dispose()
            shutdown_telemetry(app.state.telemetry_provider)
            logger.info("app_stopped")

    app = FastAPI(
        title="ChessAnnotator API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = cfg
    # Один объект Settings — единственный источник истины для приложения:
    # зависимости Depends(get_settings) должны видеть те же настройки, что
    # роутеры и middleware (см. итерацию 10).
    app.dependency_overrides[get_settings] = lambda: cfg

    # T-028: тестовый seam. Только APP_ENV=test + TEST_AUTH_BYPASS=true;
    # в dev/prod эта ветка недостижима (реальная авторизация обязательна).
    if cfg.app_env == "test" and cfg.test_auth_bypass:
        from app.auth.deps import get_current_user, get_current_user_optional
        from app.auth.testing import get_anonymous_user, get_test_user

        app.dependency_overrides[get_current_user] = get_test_user
        app.dependency_overrides[get_current_user_optional] = get_anonymous_user

    # Порядок важен: последним добавленный middleware — самый внешний.
    # CORS должен оборачивать rate limiting, чтобы браузер видел 429.
    app.add_middleware(
        RateLimitMiddleware,
        protected=[
            # T-028: защита от перебора паролей на /auth/*.
            ("POST", "/auth/login"),
            ("POST", "/auth/register"),
            ("POST", f"{cfg.api_prefix}/analyze"),
            ("POST", f"{cfg.api_prefix}/commentary"),
            ("POST", f"{cfg.api_prefix}/commentary/stream"),
            ("POST", f"{cfg.api_prefix}/chat"),
            ("POST", f"{cfg.api_prefix}/chat/stream"),
            ("POST", f"{cfg.api_prefix}/ingest"),
        ],
    )
    app.add_middleware(
        TokenBudgetMiddleware,
        protected=[
            ("POST", f"{cfg.api_prefix}/analyze"),
            ("POST", f"{cfg.api_prefix}/commentary"),
            ("POST", f"{cfg.api_prefix}/commentary/stream"),
            ("POST", f"{cfg.api_prefix}/chat"),
            ("POST", f"{cfg.api_prefix}/chat/stream"),
        ],
        # KI-038: субъект/tier — из JWT (иначе IP + PRO_IPS); настройки
        # передаются целиком, чтобы middleware и request_subject/
        # request_tier считали одинаково.
        settings=cfg,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # `/admin` → `/admin/` до маршрутизации: Mount не матчит точный префикс.
    app.add_middleware(AdminPathNormalizer)
    # T-030: trace-контекст должен быть самым внешним, чтобы X-Trace-Id
    # получали и ответы остальных middleware (429/404), и SSE.
    app.add_middleware(TraceMiddleware)

    app.add_exception_handler(AppError, _app_error_handler)
    # KI-047: unhandled-исключения обрабатываются внутри ServerErrorMiddleware,
    # поэтому TraceMiddleware не успевает добавить заголовок в ответ. Здесь
    # сами достаём trace_id из request.state (его уже выставил TraceMiddleware
    # до входа в приложение) и добавляем X-Trace-Id к 500.
    app.add_exception_handler(Exception, _unhandled_error_handler)

    # T-028: JWT-эндпоинты живут под `/auth` (вне /v1, как в постановке).
    app.include_router(auth.router, prefix="/auth")
    app.include_router(health.router, prefix=cfg.api_prefix)
    app.include_router(analyze.router, prefix=cfg.api_prefix)
    app.include_router(games.router, prefix=cfg.api_prefix)
    app.include_router(commentary.router, prefix=cfg.api_prefix)
    app.include_router(chat.router, prefix=cfg.api_prefix)
    app.include_router(ingest.router, prefix=cfg.api_prefix)
    app.include_router(users.router, prefix=cfg.api_prefix)
    # T-034b: выгрузка датасета edits.jsonl (JWT или cookie админки).
    app.include_router(admin_exports.router, prefix=cfg.api_prefix)

    # Admin control plane (T-028): монтируется до SPA, чтобы `/admin`
    # не перехватывался статикой.
    create_admin(cfg).mount_to(app)

    # SPA (T-025): production-сборка Vite лежит в web/dist и раздаётся тем же
    # процессом. Монтируется последним, чтобы не перехватывать /v1/* и /docs.
    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", SPAStaticFiles(directory=web_dist, html=True), name="web")

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


def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """500 для необработанных исключений + `X-Trace-Id` (KI-047).

    Клиент не должен видеть текст внутренней ошибки; для поддержки нужен
    только trace_id (он же в structlog-логе). Регистрируется на `Exception`,
    поэтому доменные `AppError` по-прежнему идут через
    :func:`_app_error_handler`.
    """
    trace_id = getattr(request.state, "trace_id", None) or current_trace_id()
    logger.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        trace_id=trace_id,
        error_type=type(exc).__name__,
    )
    headers = {"X-Trace-Id": trace_id} if trace_id else None
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "code": "internal_error"},
        headers=headers,
    )


app = create_app()
