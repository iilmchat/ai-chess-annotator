"""Профиль пользователя: дневной расход токенов (T-037, T-028, KI-038, T-031b).

`subject`/`tier` берутся из общего резолвера `app.finops`: при валидном
access-токене это `user:{id}`, а тир читается из `users.tier` (T-083);
без токена — `ip:{ip}` и `free` (pro-белый список IP отменён). Поэтому
middleware и этот эндпоинт всегда считают один и тот же счётчик
`usage:{day}:{subject}` и одинаково понимают тариф.

Источники расхода (T-031b):

* быстрый путь — Redis-счётчик `TokenUsageTracker` (тот же, по которому
  middleware возвращает 429);
* fallback — durable-агрегат `usage_daily` за сегодня: используется, если
  Redis недоступен, счётчика нет (бюджет выключен) или ключ пуст;
* если доступны оба — `used = max(redis, db)`: durable-запись может
  отставать на время фонового upsert-а, максимум защищает от «отката»
  показателя вниз.

Схема ответа не менялась (frontend ждёт те же поля); ошибки БД и Redis
не ломают endpoint — в худшем случае отдаётся `_fallback` (как раньше).
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.finops import (
    TierName,
    TokenBudgetStatus,
    next_utc_midnight,
    request_identity,
    usage_daily_tokens,
)

router = APIRouter(tags=["users"])


class UsageResponse(BaseModel):
    """Текущий дневной бюджет токенов."""

    tier: TierName
    used_tokens: int = Field(ge=0)
    limit_tokens: int = Field(ge=0)
    remaining_tokens: int = Field(ge=0)
    reset_at: int = Field(description="Epoch-секунды до сброса (UTC)")
    enabled: bool


def _limit_for_tier(cfg: Settings, tier: TierName) -> int:
    """Дневной лимит токенов для тира (из настроек)."""
    return (
        cfg.daily_token_limit_pro
        if tier == "pro"
        else cfg.daily_token_limit_free
    )


def _fallback(cfg: Settings, tier: TierName) -> UsageResponse:
    limit = _limit_for_tier(cfg, tier)
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
    # T-083: тот же резолвер, что у TokenBudgetMiddleware — subject из JWT,
    # tier из `users.tier` в БД.
    subject, resolved_tier = await request_identity(request, cfg)
    tier: TierName = cast("TierName", resolved_tier)

    # Быстрый путь: Redis (источник 429). Ошибка/отсутствие трекера — не
    # повод падать: ниже подстрахует durable-агрегат.
    status: TokenBudgetStatus | None = None
    tracker = getattr(request.app.state, "token_usage_tracker", None)
    if tracker is not None:
        try:
            status = await tracker.check(subject, tier)
        except Exception:
            status = None

    # Durable-fallback (T-031b): None — строки нет или БД недоступна.
    db_used = await usage_daily_tokens(subject)
    redis_used = status.used if status is not None else None
    used_values = [used for used in (redis_used, db_used) if used is not None]
    if not used_values:
        return _fallback(cfg, tier)

    used = max(used_values)
    limit = status.limit if status is not None else _limit_for_tier(cfg, tier)
    reset_at = (
        status.reset_at
        if status is not None
        else int(next_utc_midnight().timestamp())
    )
    return UsageResponse(
        tier=tier,
        used_tokens=used,
        limit_tokens=limit,
        remaining_tokens=max(0, limit - used),
        reset_at=reset_at,
        enabled=True,
    )
