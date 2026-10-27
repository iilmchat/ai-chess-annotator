import shutil
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session

router = APIRouter(tags=["health"])

ComponentStatus = Literal["ok", "error", "skipped"]


class ComponentHealth(BaseModel):
    status: ComponentStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    components: dict[str, ComponentHealth]


async def _check_db(session: AsyncSession) -> ComponentHealth:
    try:
        await session.execute(text("SELECT 1"))
        return ComponentHealth(status="ok")
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(status="error", detail=str(exc))


async def _check_redis(settings: Settings) -> ComponentHealth:
    client = aioredis.from_url(settings.redis_url, socket_timeout=2)
    try:
        await client.ping()
        return ComponentHealth(status="ok")
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth(status="error", detail=str(exc))
    finally:
        await client.aclose()


def _check_stockfish(settings: Settings) -> ComponentHealth:
    if settings.stockfish_host:
        return ComponentHealth(status="ok", detail="tcp-mode (not probed)")
    if shutil.which(settings.stockfish_path) or shutil.which("stockfish"):
        return ComponentHealth(status="ok")
    return ComponentHealth(status="error", detail="stockfish binary not found")


def _check_llm(settings: Settings) -> ComponentHealth:
    if settings.openai_api_key or settings.anthropic_api_key or settings.deepseek_api_key:
        return ComponentHealth(status="ok", detail="cloud key present")
    if settings.vllm_base_url:
        return ComponentHealth(status="skipped", detail="local vllm configured")
    return ComponentHealth(status="skipped")


@router.get("/health", response_model=HealthResponse)
async def health(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    components = {
        "db": await _check_db(session),
        "redis": await _check_redis(settings),
        "stockfish": _check_stockfish(settings),
        "llm": _check_llm(settings),
    }
    overall = "ok" if all(c.status != "error" for c in components.values()) else "degraded"
    return HealthResponse(status=overall, components=components)  # type: ignore[arg-type]