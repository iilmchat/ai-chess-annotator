"""Экспорт `edits` в JSONL для LoRA-дообучения (T-104/T-034).

Формат строки — chat-сообщения плюс метаданные. Схема строгая:
`system → user → assistant`; system берётся из `app/prompts/<level>.txt`,
поэтому датасет совместим с маскированием лосса в `scripts/train_lora.py`.

```json
{"messages": [{"role": "system", "content": "Ты — шахматный комментатор..."},
              {"role": "user", "content": "было"},
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
from functools import cache
from importlib import resources
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

__all__ = [
    "DEFAULT_OUTPUT",
    "SCHEMA_VERSION",
    "load_system_prompt",
    "main",
    "record_from_edit",
]

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "lora_dataset.jsonl"
SCHEMA_VERSION = 1
#: Уровни, для которых есть файл system-промпта.
PROMPT_LEVELS = ("beginner", "club", "gm")
#: Fallback, если в БД встретился уровень без файла промпта.
DEFAULT_PROMPT_LEVEL = "club"


@cache
def _read_prompt_file(level: str) -> str:
    """Прочитать `app/prompts/<level>.txt` через importlib.resources."""
    resource = resources.files("app.prompts").joinpath(f"{level}.txt")
    return resource.read_text(encoding="utf-8").strip()


def load_system_prompt(level: str) -> str:
    """System-промпт для уровня (`beginner`/`club`/`gm`).

    Читается через `importlib.resources`, чтобы работало и из wheel-пакета.
    Неизвестный уровень откатывается на `club`.
    """
    resolved = level if level in PROMPT_LEVELS else DEFAULT_PROMPT_LEVEL
    text = _read_prompt_file(resolved)
    if not text:
        raise RuntimeError(
            f"Пустой системный промпт: app/prompts/{resolved}.txt"
        )
    return text


def record_from_edit(
    edit: Any,
    commentary: Any,
    *,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Собрать одну JSONL-строку из `Edit` и `Commentary`.

    Первым сообщением всегда идёт `system` с промптом уровня комментария
    (или явно переданным `system_prompt`).
    """
    created_at: datetime = edit.created_at
    level = (
        commentary.level.value
        if hasattr(commentary.level, "value")
        else str(commentary.level)
    )
    prompt = system_prompt if system_prompt is not None else load_system_prompt(level)
    return {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": edit.original_text},
            {"role": "assistant", "content": edit.edited_text},
        ],
        "id": edit.id,
        "commentary_id": edit.commentary_id,
        "editor_id": edit.editor_id,
        "level": level,
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
        description=(
            "Экспорт edits в JSONL для LoRA (T-104): "
            "system→user→assistant."
        )
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
