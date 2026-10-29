"""Тестовый seam авторизации (ТОЛЬКО для APP_ENV=test).

Существующая сюита (286 тестов) написана до JWT и ходит в API анонимно.
Чтобы не переписывать сотни вызовов, `create_app()` при
`APP_ENV=test` И `TEST_AUTH_BYPASS=true` подменяет зависимости
`get_current_user`/`get_current_user_optional`:

* `get_current_user` → общий тестовый пользователь-администратор
  (владелец создаваемых партий и «всё видит», как админ);
* `get_current_user_optional` → `None` (аноним), чтобы `/v1/users/me/usage`
  сохранил прежнее поведение по IP.

В любом другом окружении (`dev`/`prod`/`stage`) этот модуль не
подключается; реальная авторизация проверяется отдельными тестами,
которые создают приложение с `test_auth_bypass=False`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.db.models import AdminRole, Level, User
from app.db.session import get_session

__all__ = ["TEST_USER_EMAIL", "get_anonymous_user", "get_test_user"]

#: Email общего тестового пользователя (создаётся лениво).
TEST_USER_EMAIL = "test-suite@example.com"
_TEST_PASSWORD = "test-suite-password"


async def get_test_user(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Вернуть (при необходимости создать) тестового пользователя-админа."""
    user = await session.scalar(select(User).where(User.email == TEST_USER_EMAIL))
    if user is None:
        user = User(
            email=TEST_USER_EMAIL,
            level=Level.club,
            role=AdminRole.admin,
            is_active=True,
            password_hash=hash_password(_TEST_PASSWORD),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif user.role != AdminRole.admin:
        user.role = AdminRole.admin
        await session.commit()
    return user


async def get_anonymous_user() -> None:
    """Зависимость-заглушка для `get_current_user_optional` (аноним)."""
    return None
