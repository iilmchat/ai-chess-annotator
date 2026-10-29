"""Redis-denylist отозванных refresh-токенов (KI-037).

Refresh JWT — stateless: подпись и срок проверяются без БД, поэтому «просто
удалить cookie» недостаточно — украденный/перехваченный токен продолжал бы
работать до истечения срока. Чтобы отзыв был серверным, `/auth/logout`
кладёт `jti` токена в Redis:

```
SETEX refresh_denylist:<jti> <секунд до exp> 1
```

TTL равен остатку жизни токена: после `exp` запись не нужна (JWT и так
недействителен), Redis не растёт бесконечно. `/auth/refresh` проверяет
наличие ключа и отвечает 401 для отозванного токена.

**Global revocation (KI-041).** `/auth/logout-all` и успешный сброс пароля
пишут ``revoked_before:<user_id>`` = Unix-ms; `/auth/refresh` сравнивает с
ним `issued_at` токена (claim `iat_ms`), поэтому отзываются все refresh,
выпущенные до момента, включая украденные. Точность в миллисекундах важна:
сравнение секунд `iat` давало бы гонку с только что выданным токеном.

**Fail-open.** Redis — не источник истины для аутентификации: если он
недоступен, logout всё равно завершается успешно (204) и чистит cookie, а
проверка denylist пропускается. Обратная сторона: при аварии Redis
отозванный refresh можно использовать до истечения срока. Это осознанный
компромисс в пользу доступности (как у rate limiting, см. KI-015); факт
деградации виден в логах `refresh_denylist_unavailable`. Если политика
безопасности потребует fail-closed — достаточно поменять возврат
:func:`is_refresh_token_revoked` на `True` при ошибке и обновить тест.
"""

from __future__ import annotations

from datetime import UTC, datetime

import redis.asyncio as aioredis
import structlog

from app.auth.tokens import TokenPayload

__all__ = [
    "DENYLIST_KEY_PREFIX",
    "GLOBAL_REVOCATION_KEY_PREFIX",
    "denylist_key",
    "global_revocation_key",
    "is_refresh_token_revoked",
    "is_token_globally_revoked",
    "revoke_all_refresh_tokens",
    "revoke_refresh_token",
]

logger = structlog.get_logger(__name__)

#: Префикс ключей denylist в Redis — отделяет их от прочих ключей приложения.
DENYLIST_KEY_PREFIX = "refresh_denylist:"

#: Префикс ключа «все refresh-токены пользователя до момента X недействительны»
#: (KI-041, logout-all / сброс пароля). Значение — Unix-время в миллисекундах.
GLOBAL_REVOCATION_KEY_PREFIX = "revoked_before:"


def denylist_key(jti: str) -> str:
    """Ключ Redis для конкретного `jti`."""
    return f"{DENYLIST_KEY_PREFIX}{jti}"


def global_revocation_key(user_id: int) -> str:
    """Ключ Redis с временем глобального отзыва refresh-токенов."""
    return f"{GLOBAL_REVOCATION_KEY_PREFIX}{int(user_id)}"


def _ttl_to_expiry_s(
    expires_at: datetime, *, now: datetime | None = None
) -> int:
    """Сколько секунд осталось жить токену (0 — если уже истёк)."""
    remaining = expires_at - (now or datetime.now(UTC))
    return max(0, int(remaining.total_seconds()))


async def revoke_refresh_token(
    client: aioredis.Redis, payload: TokenPayload
) -> bool:
    """Занести refresh-токен в denylist; `False` — отозвать не удалось.

    `False` возвращается, если токен уже истёк (TTL 0 — хранить нечего) или
    Redis недоступен. Исключения наружу не пробрасываются: logout не должен
    падать из-за кэша.
    """
    ttl_s = _ttl_to_expiry_s(payload.expires_at)
    if ttl_s <= 0:
        return False
    try:
        await client.setex(denylist_key(payload.jti), ttl_s, "1")
    except Exception as exc:
        logger.warning(
            "refresh_denylist_unavailable",
            action="revoke",
            error=str(exc),
        )
        return False
    logger.info("refresh_token_revoked", jti=payload.jti, ttl_s=ttl_s)
    return True


async def revoke_all_refresh_tokens(
    client: aioredis.Redis,
    user_id: int,
    *,
    ttl_s: int,
    now: datetime | None = None,
) -> bool:
    """Глобально отозвать все refresh-токены пользователя (KI-041).

    В Redis пишется время в миллисекундах: `/auth/refresh` и
    `/auth/reset-password` сравнивают с ним `issued_at` токена. TTL —
    не меньше максимального срока жизни refresh, иначе маркер исчезнет
    раньше, чем истекут отозванные токены.

    Fail-open: при недоступном Redis логируется warning и возвращается
    `False` (logout-all не должен падать из-за кэша; ограничение
    зафиксировано в отчёте).
    """
    marker_ms = int((now or datetime.now(UTC)).timestamp() * 1000)
    try:
        await client.setex(
            global_revocation_key(user_id), max(1, ttl_s), str(marker_ms)
        )
    except Exception as exc:
        logger.warning(
            "refresh_denylist_unavailable",
            action="revoke_all",
            error=str(exc),
        )
        return False
    logger.info(
        "refresh_tokens_revoked_all",
        user_id=user_id,
        revoked_before_ms=marker_ms,
        ttl_s=ttl_s,
    )
    return True


async def is_token_globally_revoked(
    client: aioredis.Redis,
    user_id: int,
    issued_at: datetime | None,
) -> bool:
    """Токен выпущен до глобального отзыва пользователя?

    `issued_at is None` (legacy-токен без `iat`/`iat_ms`) не блокируем:
    иначе токены, выпущенные до внедрения KI-041, сломались бы без явной
    причины. Fail-open при недоступном Redis — как у denylist.
    """
    if issued_at is None:
        return False
    try:
        raw = await client.get(global_revocation_key(user_id))
    except Exception as exc:
        logger.warning(
            "refresh_denylist_unavailable",
            action="check_all",
            error=str(exc),
        )
        return False
    if raw is None:
        return False
    try:
        revoked_before_ms = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "refresh_denylist_bad_marker", user_id=user_id, value=str(raw)
        )
        return False
    issued_ms = int(issued_at.timestamp() * 1000)
    return issued_ms <= revoked_before_ms


async def is_refresh_token_revoked(
    client: aioredis.Redis, jti: str
) -> bool:
    """Отозван ли refresh-токен с данным `jti`.

    Fail-open: при недоступном Redis возвращается `False` (см. docstring
    модуля) и пишется `structlog.warning`.
    """
    try:
        return bool(await client.exists(denylist_key(jti)))
    except Exception as exc:
        logger.warning(
            "refresh_denylist_unavailable",
            action="check",
            error=str(exc),
        )
        return False
