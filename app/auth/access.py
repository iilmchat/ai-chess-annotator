"""Проверки доступа к партиям и комментариям (изоляция данных, KI-035).

Правило: владелец видит свои записи, администратор — все. Для чужих
записей отвечаем `404`, а не `403`, чтобы не раскрывать существование
чужих `games.id`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminRole, Commentary, Game, User
from app.errors import NotFoundError

__all__ = [
    "ensure_commentary_access",
    "ensure_game_access",
    "get_accessible_commentary",
    "get_accessible_game",
]


def _is_admin(user: User) -> bool:
    return user.role == AdminRole.admin


def ensure_game_access(game: Game, user: User) -> None:
    """Проверить, что партия принадлежит пользователю или он администратор.

    Legacy-партии с `user_id IS NULL` доступны только администратору.

    Raises:
        NotFoundError: чужая/бесхозная партия (→ HTTP 404).
    """
    if _is_admin(user):
        return
    if game.user_id is None or game.user_id != user.id:
        raise NotFoundError(f"Партия {game.id} не найдена")


async def get_accessible_game(
    session: AsyncSession, game_id: int, user: User
) -> Game:
    """Загрузить партию и проверить доступ.

    Raises:
        NotFoundError: партии нет или она чужая (→ HTTP 404).
    """
    game = await session.get(Game, game_id)
    if game is None:
        raise NotFoundError(f"Партия {game_id} не найдена")
    ensure_game_access(game, user)
    return game


def ensure_commentary_access(
    commentary: Commentary, game: Game | None, user: User
) -> None:
    """Проверить доступ к комментарию через его партию.

    Raises:
        NotFoundError: партия недоступна (→ HTTP 404).
    """
    if game is None:
        raise NotFoundError(f"Комментарий {commentary.id} не найден")
    ensure_game_access(game, user)


async def get_accessible_commentary(
    session: AsyncSession, commentary_id: int, user: User
) -> Commentary:
    """Загрузить комментарий и проверить доступ к его партии."""
    commentary = await session.get(Commentary, commentary_id)
    if commentary is None:
        raise NotFoundError(f"Комментарий {commentary_id} не найден")
    game = await session.get(Game, commentary.game_id)
    ensure_commentary_access(commentary, game, user)
    return commentary
