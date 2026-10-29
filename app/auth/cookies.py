"""HttpOnly-cookie с refresh-токеном (KI-037).

Refresh JWT больше не возвращается в теле ответа `/auth/login` и
`/auth/refresh`: браузер получает его в `Set-Cookie` с флагами
`HttpOnly; SameSite=<auth_cookie_samesite>; Path=/auth` (+`Secure` при
`AUTH_COOKIE_SECURE=true`). Access-токен по-прежнему отдаётся в JSON и
хранится клиентом в памяти.

`HttpOnly` прячет refresh от JS (XSS не украдёт долгоживущий токен),
`Path=/auth` не отправляет cookie на остальные эндпоинты, `SameSite=lax`
по умолчанию смягчает CSRF для кросс-сайтовых POST. Домен/имя/путь/флаги —
из :class:`app.config.Settings`, чтобы тесты и разные окружения могли их
переопределить.

Cookie — транспорт для **браузеров**; не-браузерные клиенты (curl, мобильные
приложения, тесты) могут передать refresh-токен в теле JSON — fallback
реализован в `app/api/v1/auth.py`.
"""

from __future__ import annotations

from starlette.responses import Response

from app.config import Settings

__all__ = [
    "clear_refresh_cookie",
    "refresh_cookie_max_age_s",
    "set_refresh_cookie",
]


def refresh_cookie_max_age_s(settings: Settings) -> int:
    """`Max-Age` cookie в секундах — ровно время жизни refresh-токена."""
    return settings.refresh_token_ttl_days * 24 * 60 * 60


def set_refresh_cookie(
    response: Response, refresh_token: str, settings: Settings
) -> None:
    """Положить refresh JWT в HttpOnly-cookie ответа."""
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=refresh_token,
        max_age=refresh_cookie_max_age_s(settings),
        path=settings.auth_cookie_path,
        domain=settings.auth_cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Удалить refresh-cookie (logout): `Max-Age=0` и пустое значение.

    Атрибуты (path/domain/secure/samesite) повторяют установку: браузер
    сопоставляет cookie по имени/домену/пути, но консистентные флаги делают
    удаление предсказуемым и не оставляют «мёртвую» cookie с другим путём.
    """
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path=settings.auth_cookie_path,
        domain=settings.auth_cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
