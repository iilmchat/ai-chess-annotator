"""Хуки редактируемого PromptAdmin (T-029, итерация 17)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette_admin.exceptions import FormValidationError

from app.admin import PromptAdmin
from app.db.models import Prompt


class _PromptObj:
    """Минимальный объект строки `prompts` для проверки хуков."""

    id = 1
    level = "club"
    version = 2
    content = "prompt text"


class _FakeRequest:
    """Минимальный Request для хуков без HTTP/Redis."""

    def __init__(self) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(redis=None)
        )
        self.state = SimpleNamespace(
            admin_user=None,
            prompt_old_hash=None,
            prompt_old_version=None,
        )
        self.headers: dict[str, str] = {}
        self.client = None


async def test_validate_rejects_blank_content() -> None:
    view = PromptAdmin(Prompt)

    with pytest.raises(FormValidationError):
        await view.validate(None, {"content": "   "})  # type: ignore[arg-type]


async def test_before_edit_bumps_version() -> None:
    view = PromptAdmin(Prompt)
    obj = _PromptObj()

    await view.before_edit(None, {}, obj)  # type: ignore[arg-type]

    assert obj.version == 3


async def test_after_edit_committed_resets_prompt_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = PromptAdmin(Prompt)
    obj = _PromptObj()
    calls: list[str | None] = []

    async def _noop_audit(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "app.admin.reset_prompt_cache", lambda level=None: calls.append(level)
    )
    monkeypatch.setattr("app.admin._write_prompt_audit", _noop_audit)

    await view.after_edit_committed(_FakeRequest(), obj)  # type: ignore[arg-type]

    assert calls == ["club"]
