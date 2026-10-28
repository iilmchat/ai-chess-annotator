"""Append-only аудит и hash-chain (T-035, итерация 18).

Каждая запись ссылается на `current_hash` предыдущей:
``sha256(prev_hash + timestamp + actor + action + target_id + details)``.
Запись идёт под `pg_advisory_xact_lock`, иначе два параллельных писателя
прочитали бы один `prev_hash` и создали развилку.

Сырые тексты в `details` не пишутся: только id, длины и sha256, чтобы
аудит не превращался в хранилище промптов, сообщений и PII.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.db.models import AuditLog
from app.db.session import SessionLocal

__all__ = [
    "GENESIS_HASH",
    "AuditVerification",
    "actor_from_request",
    "append_audit",
    "canonical_details",
    "compute_audit_hash",
    "hash_text",
    "record_injection_attempt",
    "verify_audit_chain",
]

logger = structlog.get_logger(__name__)

#: `prev_hash` первой записи в цепочке.
GENESIS_HASH = "0" * 64


def canonical_details(details: Mapping[str, Any] | None) -> str:
    """Канонический JSON для хеша (без пробелов, ключи отсортированы)."""
    return json.dumps(
        dict(details or {}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _timestamp_text(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.isoformat()


def compute_audit_hash(
    prev_hash: str,
    timestamp: datetime,
    actor: str,
    action: str,
    target_id: str | None,
    details_json: str,
) -> str:
    """Вычислить `current_hash` записи аудита."""
    payload = (
        f"{prev_hash}{_timestamp_text(timestamp)}"
        f"{actor}{action}{target_id or ''}{details_json}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    """sha256 текста — для `details` без хранения самого текста."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def append_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    target_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Добавить запись в аудит в текущей транзакции.

    Вызывающая сторона commit'ит транзакцию (или откатывает вместе с
    бизнес-операцией). Для аудита после уже совершённого commit (правка
    промпта) используйте отдельную сессию.

    Raises:
        sqlalchemy.exc.DBAPIError: цепочка/триггер/БД недоступны.
    """
    # Advisory-lock на транзакцию: сериализует чтение prev_hash и вставку.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('audit_logs'))")
    )
    previous = await session.scalar(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(1)
    )
    prev_hash = previous.current_hash if previous is not None else GENESIS_HASH
    timestamp = datetime.now(UTC)
    details_json = canonical_details(details)
    row = AuditLog(
        timestamp=timestamp,
        actor=actor,
        action=action,
        target_id=target_id,
        details=dict(details or {}),
        prev_hash=prev_hash,
        current_hash=compute_audit_hash(
            prev_hash,
            timestamp,
            actor,
            action,
            target_id,
            details_json,
        ),
    )
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True, slots=True)
class AuditVerification:
    """Результат проверки целостности цепочки."""

    ok: bool
    checked: int
    errors: tuple[str, ...]


async def record_injection_attempt(
    request: Request,
    *,
    rule_ids: tuple[str, ...],
    message_hash: str,
    message_len: int,
    surface: str = "chat",
) -> None:
    """Записать факт блокировки injection в отдельной транзакции.

    Запрос завершится 422, поэтому его сессия будет откатана; аудит
    пишется отдельно. Сбой аудита не превращает 422 в 500.
    """
    try:
        async with SessionLocal() as session:
            await append_audit(
                session,
                actor=actor_from_request(request),
                action="prompt_injection.blocked",
                details={
                    "surface": surface,
                    "rule_ids": list(rule_ids),
                    "message_sha256": message_hash,
                    "message_len": message_len,
                },
            )
            await session.commit()
    except Exception as exc:
        logger.warning("audit_injection_write_failed", error=str(exc))


async def verify_audit_chain(session: AsyncSession) -> AuditVerification:
    """Проверить `prev_hash`/`current_hash` всех записей по порядку id."""
    rows = (
        await session.scalars(select(AuditLog).order_by(AuditLog.id))
    ).all()
    errors: list[str] = []
    previous_hash = GENESIS_HASH
    for row in rows:
        if row.prev_hash != previous_hash:
            errors.append(
                f"id={row.id}: prev_hash не совпадает с предыдущим "
                "current_hash"
            )
        expected = compute_audit_hash(
            row.prev_hash,
            row.timestamp,
            row.actor,
            row.action,
            row.target_id,
            canonical_details(row.details),
        )
        if row.current_hash != expected:
            errors.append(f"id={row.id}: current_hash не совпадает")
        previous_hash = row.current_hash
    return AuditVerification(
        ok=not errors, checked=len(rows), errors=tuple(errors)
    )


def client_ip(request: Request) -> str:
    """IP клиента: первый `X-Forwarded-For` либо адрес сокета."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client:
        return request.client.host
    return "unknown"


def actor_from_request(request: Request) -> str:
    """Актор для аудита: admin-сессия, X-User-Id или anonymous+IP."""
    user = getattr(request.state, "admin_user", None)
    username = getattr(user, "username", None)
    if username:
        return f"admin:{username}"
    user_id = request.headers.get("x-user-id")
    if user_id:
        return f"user:{user_id.strip()[:128]}"
    return f"anonymous:{client_ip(request)}"
