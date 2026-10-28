"""Валидация JSONL для `train_lora.py --dry-run` (T-104)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.train_lora import validate_dataset


def _write(path: Path, lines: list[object]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for line in lines:
            stream.write(json.dumps(line, ensure_ascii=False) + "\n")


def test_valid_dataset(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(
        path,
        [
            {
                "messages": [
                    {"role": "user", "content": "было"},
                    {"role": "assistant", "content": "стало"},
                ]
            }
        ],
    )

    count, errors = validate_dataset(path)

    assert count == 1
    assert errors == []


def test_invalid_rows_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(
        path,
        [
            {"messages": []},
            {
                "messages": [
                    {"role": "user", "content": "x"},
                    {"role": "assistant", "content": " "},
                ]
            },
        ],
    )

    count, errors = validate_dataset(path)

    assert count == 0
    assert len(errors) == 2


def test_missing_file_is_error(tmp_path: Path) -> None:
    count, errors = validate_dataset(tmp_path / "nope.jsonl")

    assert count == 0
    assert errors
