"""Экспорт `edits` в JSONL для LoRA-дообучения (T-104/T-034).

Формат строки — chat-сообщения плюс метаданные:

```json
{"messages": [{"role": "user", "content": "было"},
              {"role": "assistant", "content": "стало"}],
 "id": 1, "commentary_id": 2, "level": "club", "model": "qwen",
 "created_at": "...", "schema_version": 1}
```

Стриминг: строки пишутся по мере чтения курсора, без загрузки всех
`edits` в память.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Commentary, Edit, Level  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

__all__ = ["DEFAULT_OUTPUT", "main", "record_from_edit"]

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "lora_dataset.jsonl"
SCHEMA_VERSION = 1


def record_from_edit(edit: Any, commentary: Any) -> dict[str, Any]:
    """Собрать одну JSONL-строку из `Edit` и `Commentary`."""
    created_at: datetime = edit.created_at
    return {
        "messages": [
            {"role": "user", "content": edit.original_text},
            {"role": "assistant", "content": edit.edited_text},
        ],
        "id": edit.id,
        "commentary_id": edit.commentary_id,
        "editor_id": edit.editor_id,
        "level": commentary.level.value
        if hasattr(commentary.level, "value")
        else str(commentary.level),
        "model": commentary.model,
        "created_at": created_at.isoformat(),
        "schema_version": SCHEMA_VERSION,
    }


async def _export(
    session: AsyncSession,
    output: Path,
    *,
    limit: int | None,
    level: str | None,
    min_id: int,
) -> int:
    statement = (
        select(Edit, Commentary)
        .join(Commentary, Edit.commentary_id == Commentary.id)
        .where(Edit.id >= min_id)
        .order_by(Edit.id)
    )
    if level:
        statement = statement.where(Commentary.level == Level(level))
    if limit is not None:
        statement = statement.limit(limit)

    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    result = await session.stream(statement)
    with output.open("w", encoding="utf-8") as stream:
        async for edit, commentary in result:
            stream.write(
                json.dumps(
                    record_from_edit(edit, commentary),
                    ensure_ascii=False,
                )
                + "\n"
            )
            count += 1
    return count


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Экспорт edits в JSONL для LoRA (T-104)."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--level", choices=["beginner", "club", "gm"], default=None
    )
    parser.add_argument("--min-id", type=int, default=0)
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        count = await _export(
            session,
            args.output,
            limit=args.limit,
            level=args.level,
            min_id=args.min_id,
        )
    print(f"Экспортировано записей: {count}")
    print(f"Файл: {args.output}")
    if count == 0:
        print(
            "В edits нет записей (или фильтр пуст). "
            "Сначала отредактируйте комментарии через PATCH.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.limit is not None and args.limit < 1:
        print("--limit должен быть ≥ 1", file=sys.stderr)
        return 2
    get_settings()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
