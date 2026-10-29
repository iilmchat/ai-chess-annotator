"""Хеширование паролей (passlib/bcrypt).

bcrypt ограничен 72 байтами входа: более длинный пароль молча обрезался бы,
поэтому длину проверяем явно (API-схема тоже ограничивает длину, но
утилита не должна зависеть от неё).
"""

from __future__ import annotations

from passlib.context import CryptContext

__all__ = ["MAX_PASSWORD_BYTES", "hash_password", "verify_password"]

#: Максимум байт, который принимает bcrypt.
MAX_PASSWORD_BYTES = 72

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Захешировать пароль bcrypt-ом.

    Raises:
        ValueError: пароль длиннее 72 байт UTF-8 или пустой.
    """
    if not password:
        raise ValueError("Пароль не может быть пустым")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Пароль длиннее {MAX_PASSWORD_BYTES} байт — bcrypt его обрежет"
        )
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Проверить пароль против хеша; битый/пустой хеш — `False`, не исключение."""
    if not password or not password_hash:
        return False
    try:
        return _pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False
