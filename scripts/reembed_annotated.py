"""Переиндексация `annotated_games` после миграции 1024 → 768.

Миграция `d2e3f4a5b6c7` пересоздаёт колонку `embedding` как `vector(768)`
и теряет старые 1024-мерные векторы. Скрипт считает эмбеддинг финальной
позиции каждой партии через текущий `get_embedder()` (LM Studio) и
обновляет строки.

Запуск:

    python scripts/reembed_annotated.py
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import chess.pgn  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import AnnotatedGame  # noqa: E402
from app.rag.embedder import get_embedder, position_document  # noqa: E402

__all__ = ["main"]


def _final_fen(pgn: str) -> str | None:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return None
    board = game.board()
    for move in game.mainline_moves():
        board.push(move)
    return board.fen()


async def _run(batch_size: int) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    embedder = get_embedder(settings)
    updated = 0
    skipped = 0
    try:
        async with factory() as session:
            rows = (
                await session.scalars(
                    select(AnnotatedGame).order_by(AnnotatedGame.id)
                )
            ).all()
            print(f"Партий: {len(rows)}; эмбеддер: {type(embedder).__name__}")
            for index, row in enumerate(rows, start=1):
                fen = _final_fen(row.pgn)
                if fen is None:
                    skipped += 1
                    continue
                try:
                    row.embedding = await embedder.embed(position_document(fen))
                except Exception as exc:
                    print(f"[{index}] ошибка: {exc}", file=sys.stderr)
                    skipped += 1
                    continue
                updated += 1
                if index % batch_size == 0:
                    await session.commit()
                    print(f"[{index}/{len(rows)}] обновлено {updated}")
            await session.commit()
    finally:
        await engine.dispose()
    print(f"Готово: обновлено {updated}, пропущено {skipped}")
    return 0 if updated else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Переиндексация RAG 768.")
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args(argv)
    return asyncio.run(_run(max(1, args.batch_size)))


if __name__ == "__main__":
    raise SystemExit(main())
