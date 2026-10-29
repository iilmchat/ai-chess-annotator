"""Хеширование и проверка паролей: Argon2id (KI-036).

Новые пароли хешируются **Argon2id** через `argon2-cffi`
(`argon2.PasswordHasher`, параметры библиотеки по умолчанию, префикс
`$argon2id$`). Argon2id — рекомендация OWASP Password Storage Cheat Sheet и
совместим с DESIGN_ADMIN_AND_OPS §4.1; в отличие от bcrypt он не ограничен
72 байтами входа, поэтому лимит длины пароля теперь чисто прикладной
(8..256 символов, см. Pydantic-схему API).

**Почему остаётся bcrypt.** В БД со времён T-028 лежат bcrypt-хеши
(`passlib[bcrypt]`). Чтобы не сломать вход существующим пользователям,
bcrypt установлен как зависимость «только для legacy-verify»: при успешном
входе `verify_and_update` возвращает новый Argon2id-хеш, и API-слой
перезаписывает `users.password_hash` в той же транзакции (миграция «на лету»).
passlib больше не используется. Когда все bcrypt-хеши конвертированы
(например, по метрике `password_rehashed` или после разовой миграции), эту
ветку и зависимость `bcrypt` можно удалить.

Длину пароля проверяем явно: утилита не должна зависеть от Pydantic-схемы
(её могут вызвать скрипты/админка), а слишком длинный вход — лишняя работа
для KDF.

KI-043: :data:`DUMMY_PASSWORD_HASH` — заранее посчитанный Argon2id-хеш тех
же параметров. `/auth/login` при неизвестном email выполняет verify по нему,
чтобы время ответа не выдавало, зарегистрирован ли email (timing attack).
"""

from __future__ import annotations

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

__all__ = [
    "DUMMY_PASSWORD_HASH",
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "hash_password",
    "verify_and_update",
    "verify_password",
]

#: Минимальная длина пароля (символов) — дублирует Pydantic-схему API.
MIN_PASSWORD_LENGTH = 8
#: Максимальная длина пароля (символов). bcrypt-лимита 72 байта больше нет.
MAX_PASSWORD_LENGTH = 256

#: Префиксы bcrypt-хешей, которые ещё могут встретиться в БД (legacy).
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")

#: Один общий hasher: параметры Argon2id по умолчанию (argon2-cffi).
_hasher = PasswordHasher()

#: Dummy Argon2id-хеш для выравнивания времени входа при неизвестном email
#: (KI-043). Сгенерирован локально ``PasswordHasher().hash(...)`` теми же
#: параметрами, что и :data:`_hasher` (argon2id, m=65536, t=3, p=4), поэтому
#: verify по нему стоит столько же, сколько проверка реального пароля.
#: Секрет в хеше фиктивный — пароль к нему не подходит ни у одного клиента.
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$DJEAz++cOiJahpTs6r5lbA$"
    "QTDcy4RqBQzdfrWXgEfA0+QG+F87I21IlYD81PMW3Wo"
)


def hash_password(password: str) -> str:
    """Захешировать пароль Argon2id-ом.

    Raises:
        ValueError: пароль пустой или длиннее
            :data:`MAX_PASSWORD_LENGTH` символов.
    """
    if not password:
        raise ValueError("Пароль не может быть пустым")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Пароль длиннее {MAX_PASSWORD_LENGTH} символов"
        )
    return _hasher.hash(password)


def _is_legacy_bcrypt(password_hash: str) -> bool:
    """Похож ли хеш на bcrypt-хеш старого формата (`$2a$`/`$2b$`/`$2y$`)."""
    return password_hash.startswith(_BCRYPT_PREFIXES)


def _verify_bcrypt(password: str, password_hash: str) -> bool:
    """Проверить пароль против legacy bcrypt-хеша.

    bcrypt 5.x падает `ValueError`, если вход длиннее 72 байт; это не должно
    превращаться в 500 — считаем такой пароль неверным.
    """
    try:
        return bool(
            bcrypt.checkpw(
                password.encode("utf-8"), password_hash.encode("utf-8")
            )
        )
    except (ValueError, TypeError):
        return False


def _verify_argon2(password: str, password_hash: str) -> bool:
    """Проверить пароль против Argon2-хеша; битый хеш — `False`."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except (ValueError, TypeError):
        # Мусор вроде None уже отфильтрован вызывающим, но защищаемся и от
        # экзотических значений (не-str) из БД.
        return False


def verify_password(password: str, password_hash: str | None) -> bool:
    """Проверить пароль против хеша (Argon2id или legacy bcrypt).

    Битый/пустой хеш и пустой пароль — `False`, не исключение.
    """
    if not password or not password_hash:
        return False
    if _is_legacy_bcrypt(password_hash):
        return _verify_bcrypt(password, password_hash)
    return _verify_argon2(password, password_hash)


def verify_and_update(
    password: str, password_hash: str | None
) -> tuple[bool, str | None]:
    """Проверить пароль и, если нужно, вернуть новый Argon2id-хеш.

    Возвращает `(пароль_верен, новый_хеш_или_None)`:

    * Argon2id-хеш с актуальными параметрами — `(True, None)`, хеш не меняем;
    * Argon2id-хеш с устаревшими параметрами — `(True, новый_хеш)`,
      нативный `check_needs_rehash` (rehash при смене параметров hasher-а);
    * legacy bcrypt-хеш (`$2b$`/`$2a$`) и верный пароль — `(True,
      новый_argon2_хеш)` — миграция при первом успешном входе;
    * неверный пароль или битый/пустой хеш — `(False, None)`.

    У `argon2.PasswordHasher` нет единого метода `verify_and_update`
    (он есть в pwdlib/passlib-обёртках), поэтому используем нативную пару
    ``verify`` + ``check_needs_rehash`` — семантика та же: перехеширование
    только при реальной смене параметров.
    """
    if not password or not password_hash:
        return False, None
    if _is_legacy_bcrypt(password_hash):
        if not _verify_bcrypt(password, password_hash):
            return False, None
        # Миграция: заменяем bcrypt-хеш на актуальный Argon2id.
        return True, hash_password(password)
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    except (ValueError, TypeError):
        return False, None
    if _hasher.check_needs_rehash(password_hash):
        return True, hash_password(password)
    return True, None
