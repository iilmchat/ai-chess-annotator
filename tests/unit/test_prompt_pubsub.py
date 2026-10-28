"""Redis Pub/Sub-инвалидация кэша промптов (KI-032)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import app.prompts as prompts
from app.prompts.pubsub import (
    PROMPT_UPDATES_CHANNEL,
    handle_prompt_update,
    publish_prompt_update,
    watch_prompt_updates,
)


class FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = list(messages)
        self.subscribed: str | None = None
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed = channel

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool,
        timeout: float,  # noqa: ASYNC109 - сигнатура redis PubSub
    ) -> dict[str, Any] | None:
        return self._messages.pop(0) if self._messages else None

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.published: list[tuple[str, str]] = []
        self.pubsub_obj = FakePubSub(
            [{"type": "message", "data": b"club"}]
        )

    async def publish(self, channel: str, message: str) -> int:
        if self.fail:
            raise ConnectionError("redis недоступен")
        self.published.append((channel, message))
        return 1

    def pubsub(self) -> FakePubSub:
        return self.pubsub_obj


async def test_publish_sends_level() -> None:
    redis = FakeRedis()

    assert await publish_prompt_update(redis, "club") is True  # type: ignore[arg-type]
    assert redis.published == [(PROMPT_UPDATES_CHANNEL, "club")]


async def test_publish_failure_is_logged_and_returns_false() -> None:
    redis = FakeRedis(fail=True)

    assert await publish_prompt_update(redis, "club") is False  # type: ignore[arg-type]


async def test_handle_update_clears_one_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    monkeypatch.setattr(
        "app.prompts.pubsub.reset_prompt_cache",
        lambda level=None: calls.append(level),
    )

    await handle_prompt_update({"data": b"club"})

    assert calls == ["club"]


async def test_handle_star_clears_all(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []
    monkeypatch.setattr(
        "app.prompts.pubsub.reset_prompt_cache",
        lambda level=None: calls.append(level),
    )

    await handle_prompt_update({"data": "*"})

    assert calls == [None]


async def test_watcher_applies_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    monkeypatch.setattr(
        "app.prompts.pubsub.reset_prompt_cache",
        lambda level=None: calls.append(level),
    )
    redis = FakeRedis()

    task = asyncio.create_task(
        watch_prompt_updates(redis)  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == ["club"]
    assert redis.pubsub_obj.subscribed == PROMPT_UPDATES_CHANNEL
    assert redis.pubsub_obj.closed is True


def test_cache_object_is_shared() -> None:
    # Страховка от случайного пересоздания кэша в другом модуле.
    prompts._cache["club"] = "x"
    assert "club" in prompts._cache
    prompts.reset_prompt_cache("club")
