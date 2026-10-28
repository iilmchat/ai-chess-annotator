"""Переоценка сохранённых комментариев с полным пакетом фактов (T-023).

Первый прогон `evaluate_llm.py` передавал судье компактный факт-лист без
PV и рекомендаций Policy, поэтому легальные цитаты вариантов могли
считаться «выдуманными». Этот скрипт берёт уже сохранённые `commentaries`
и тот же `build_fact_package`, который видела генерация, и повторяет
оценку без повторной генерации.

Запуск (после `evaluate_llm.py`):

    python scripts/rejudge_llm.py --min-commentary-id 18
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluate_llm import (  # noqa: E402
    DEFAULT_JSON,
    DEFAULT_OUTPUT,
    CommentaryRow,
    GameRow,
    _averages,
    _dump_json,
    _judge,
    _report,
)
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.commentary.service import load_analyses  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Commentary, Game  # noqa: E402
from app.ingestion import parse_pgn  # noqa: E402
from app.llm.prompts import build_fact_package  # noqa: E402

__all__ = ["main"]


async def _load_rows(
    min_commentary_id: int,
) -> tuple[list[CommentaryRow], list[GameRow]]:
    """Загрузить комментарии и восстановить факт-пакет для судьи."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rows: list[CommentaryRow] = []
    games: dict[int, GameRow] = {}
    seen: set[tuple[int, str]] = set()
    try:
        async with factory() as session:
            commentaries = (
                await session.scalars(
                    select(Commentary)
                    .where(Commentary.id >= min_commentary_id)
                    .order_by(Commentary.id)
                )
            ).all()
            for commentary in commentaries:
                game = await session.get(Game, commentary.game_id)
                if game is None:
                    continue
                parsed = parse_pgn(game.pgn)
                game_row = GameRow(
                    game_id=game.id,
                    pgn=game.pgn,
                    white=parsed.metadata.white or "неизвестно",
                    black=parsed.metadata.black or "неизвестно",
                    event=parsed.metadata.event or "без события",
                )
                games[game_row.game_id] = game_row
                level = str(commentary.level)
                seen.add((game.id, level))
                analyses = await load_analyses(session, game, parsed)
                row = CommentaryRow(game=game_row, level=level)
                row.commentary_id = commentary.id
                row.text = commentary.text
                row.model = commentary.model
                row.tokens_used = (commentary.tokens_in or 0) + (
                    commentary.tokens_out or 0
                )
                row.validated = commentary.validated
                row.attempts = 0  # в БД число попыток не хранится
                row.fact_sheet = build_fact_package(parsed, analyses, level)
                rows.append(row)
    finally:
        await engine.dispose()

    # Одна club-генерация упала с 503 (LM Studio не загрузила модель) —
    # оставляем её в матрице как явную ошибку.
    for game_id, game_row in games.items():
        if (game_id, "club") not in seen:
            rows.append(
                CommentaryRow(
                    game=game_row,
                    level="club",
                    error="commentary 503: LM Studio failed to load model",
                )
            )
    return rows, list(games.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rejudge сохранённых комментариев.")
    parser.add_argument("--min-commentary-id", type=int, default=18)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args(argv)

    rows, games = asyncio.run(_load_rows(args.min_commentary_id))
    if not rows:
        print("Не найдено комментариев для переоценки", file=sys.stderr)
        return 1
    settings = get_settings()
    asyncio.run(_judge(rows, settings))
    overall = _averages(rows)
    average = sum(overall.values()) / len(overall) if overall else 0.0

    report = _report(rows, games, ["club", "gm"], 300.0)
    report = report.replace(
        "- Судья получает метаданные, ходы, ключевые моменты и сам комментарий;",
        "- Судья получает **полный пакет фактов** (ходы, ключевые моменты, PV, "
        "рекомендации Policy) и сам комментарий;",
    )
    report = report.replace(
        "## Итоговые оценки",
        "> Повторная оценка сохранённых комментариев с полным пакетом фактов; "
        "генерация не повторялась.\n\n## Итоговые оценки",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    _dump_json(rows, args.json_output)

    print("--- REJUDGE ---")
    for scale, value in overall.items():
        print(f"{scale}: {value:.2f}/10")
    print(f"Средняя: {average:.2f}/10")
    print(f"Отчёт: {args.output}")
    print(f"JSON: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
