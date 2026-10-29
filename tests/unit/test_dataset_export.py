"""Формат JSONL для LoRA (T-104): system→user→assistant."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from scripts.export_dataset import (
    SCHEMA_VERSION,
    load_system_prompt,
    record_from_edit,
)


def _edit() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        commentary_id=3,
        editor_id=1,
        original_text="было",
        edited_text="стало",
        created_at=datetime(2026, 10, 29, 1, 2, 3, tzinfo=UTC),
    )


def _commentary(level: str = "club") -> SimpleNamespace:
    return SimpleNamespace(level=SimpleNamespace(value=level), model="qwen")


def test_system_prompt_is_loaded_from_prompt_file() -> None:
    text = load_system_prompt("club")

    assert "шахматный комментатор" in text.lower()
    assert "ЗАПРЕЩЕНО" in text


def test_unknown_level_falls_back_to_club_prompt() -> None:
    assert load_system_prompt("master") == load_system_prompt("club")


def test_record_from_edit_has_system_user_assistant() -> None:
    record = record_from_edit(_edit(), _commentary())

    messages = record["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
    ]
    assert messages[0]["content"] == load_system_prompt("club")
    assert messages[1] == {"role": "user", "content": "было"}
    assert messages[2] == {"role": "assistant", "content": "стало"}


def test_record_from_edit_keeps_metadata() -> None:
    record = record_from_edit(_edit(), _commentary())

    assert record["id"] == 7
    assert record["commentary_id"] == 3
    assert record["editor_id"] == 1
    assert record["level"] == "club"
    assert record["model"] == "qwen"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["created_at"].startswith("2026-10-29T01:02:03")


def test_record_from_edit_uses_level_prompt() -> None:
    club = record_from_edit(_edit(), _commentary("club"))
    gm = record_from_edit(_edit(), _commentary("gm"))

    assert gm["messages"][0]["content"] == load_system_prompt("gm")
    assert gm["messages"][0]["content"] != club["messages"][0]["content"]


def test_record_from_edit_accepts_system_prompt_override() -> None:
    record = record_from_edit(
        _edit(), _commentary(), system_prompt="кастомный system"
    )

    assert record["messages"][0] == {
        "role": "system",
        "content": "кастомный system",
    }
