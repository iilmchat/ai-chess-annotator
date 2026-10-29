"""Профиль пользователя: дневной расход токенов (T-037, T-028).

Для аутентифицированного пользователя `subject`/`tier` берутся из его
аккаунта: администратор и аудитор получают `pro`, обычный пользователь —
`free`, ключ счётчика — `user:{id}`. Без токена сохраняется прежнее
поведение по IP + `PRO_IPS` (KI-031): эндпоинт доступен и анонимно, БД ему
не нужна.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_optional
from app.config import Settings, get_settings
from app.db.models import AdminRole, User
from app.finops import request_subject, request_tier

router = APIRouter(tags=["users"])

TierName = Literal["free", "pro"]

#: Роли с «профессиональным» дневным лимитом.
PRO_ROLES = frozenset({AdminRole.admin, AdminRole.auditor})


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
async def my_usage(
    request: Request,
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> UsageResponse:
    """Вернуть тир, расход и лимит токенов за текущий день (UTC)."""
    cfg = getattr(request.app.state, "settings", None) or get_settings()
    if user is None:
        # Аноним: прежнее поведение по IP (KI-031).
        tier = cast(TierName, request_tier(request, cfg))
        subject = request_subject(request, cfg)
    else:
        tier = "pro" if user.role in PRO_ROLES else "free"
        subject = f"user:{user.id}"
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
