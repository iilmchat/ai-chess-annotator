"""Формат JSONL для LoRA (T-104)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from scripts.export_dataset import SCHEMA_VERSION, record_from_edit


def test_record_from_edit_has_chat_messages_and_metadata() -> None:
    edit = SimpleNamespace(
        id=7,
        commentary_id=3,
        editor_id=1,
        original_text="было",
        edited_text="стало",
        created_at=datetime(2026, 10, 29, 1, 2, 3, tzinfo=UTC),
    )
    commentary = SimpleNamespace(
        level=SimpleNamespace(value="club"), model="qwen"
    )

    record = record_from_edit(edit, commentary)

    assert record["messages"] == [
        {"role": "user", "content": "было"},
        {"role": "assistant", "content": "стало"},
    ]
    assert record["level"] == "club"
    assert record["model"] == "qwen"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["created_at"].startswith("2026-10-29T01:02:03")
