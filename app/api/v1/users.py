"""Профиль пользователя: дневной расход токенов (T-037, T-028, KI-038).

`subject`/`tier` берутся из общего резолвера `app.finops`: при валидном
access-токене это `user:{id}` и роль (`admin`/`auditor` → `pro`, `user` →
`free`), без токена — прежнее поведение по IP + `PRO_IPS` (KI-031).
Поэтому middleware и этот эндпоинт всегда считают один и тот же счётчик
`usage:{day}:{subject}`, а БД для ответа не нужна.
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.finops import request_subject, request_tier

router = APIRouter(tags=["users"])

TierName = Literal["free", "pro"]


class UsageResponse(BaseModel):
    """Текущий дневной бюджет токенов."""

    tier: TierName
    used_tokens: int = Field(ge=0)
    limit_tokens: int = Field(ge=0)
    remaining_tokens: int = Field(ge=0)
    reset_at: int = Field(description="Epoch-секунды до сброса (UTC)")
    enabled: bool


def _fallback(cfg: Settings, tier: TierName) -> UsageResponse:
    limit = (
        cfg.daily_token_limit_pro
        if tier == "pro"
        else cfg.daily_token_limit_free
    )
    return UsageResponse(
        tier=tier,
        used_tokens=0,
        limit_tokens=limit,
        remaining_tokens=limit,
        reset_at=0,
        enabled=False,
    )


@router.get("/users/me/usage", response_model=UsageResponse)
async def my_usage(request: Request) -> UsageResponse:
    """Вернуть тир, расход и лимит токенов за текущий день (UTC)."""
    cfg = getattr(request.app.state, "settings", None) or get_settings()
    # KI-038: тот же JWT-aware резолвер, что у TokenBudgetMiddleware.
    tier = cast(TierName, request_tier(request, cfg))
    subject = request_subject(request, cfg)
    tracker = getattr(request.app.state, "token_usage_tracker", None)
    if tracker is None:
        return _fallback(cfg, tier)
    try:
        status = await tracker.check(subject, tier)
    except Exception:
        return _fallback(cfg, tier)
    return UsageResponse(
        tier=tier,
        used_tokens=status.used,
        limit_tokens=status.limit,
        remaining_tokens=status.remaining,
        reset_at=status.reset_at,
        enabled=True,
    )
