"""Интеграционный тест экспорта датасета v2 (KI-039).

Seed `Game`/`Move`/`Motif`/`Commentary`/`Edit` в тестовую БД →
`scripts/export_dataset.py --schema-version 2` → JSONL, который:
* проходит валидатор `scripts/train_lora.py`;
* содержит в `user` пакет фактов (`build_fact_package`), в `assistant` —
  правку редактора;
* пропускает записи без анализа и без валидного PGN (не падая).

Тест требует PostgreSQL; без него модуль скипается (как остальные
интеграционные тесты).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Commentary, Edit, Game, Level, Move
from app.db.models import Motif as MotifRow
from app.db.session import engine as export_engine
from app.ingestion import parse_pgn
from scripts.export_dataset import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    load_system_prompt,
    main,
)
from scripts.train_lora import validate_dataset

PGN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"
ORIGINAL_TEXT = "Белые разыграли дебют без ошибок."
EDITED_TEXT = (
    "Белые разыграли итальянскую партию, но на 2-м ходу чёрные неточно "
    "ответили Nc6: слон c4 уже давит на f7, и защита требует Nf6 с "
    "последующим Be7. Оценка после хода осталась примерно равной, однако "
    "план белых с d3 и O-O становится проще. Рекомендация для club: "
    "развивать коня на f6, не закрывая диагональ слону."
)


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


@pytest.fixture(autouse=True)
def _reset_export_engine() -> Iterator[None]:
    """`SessionLocal` живёт на уровне модуля и привязан к event loop.

    Каждый тест вызывает `main()` через собственный `asyncio.run`; после
    теста закрываем пул, иначе следующий тест получит соединение из
    закрытого loop (RuntimeError: Event loop is closed на Windows).
    """
    yield
    asyncio.run(export_engine.dispose())


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed() -> dict[str, Any]:
    """Засидить партию с анализом, партию без анализа и битый PGN."""
    parsed = parse_pgn(PGN)
    factory = _factory()
    async with factory() as session:
        # 1) Партия с анализом — должна экспортироваться.
        analyzed_game = Game(pgn=PGN, eco_code=parsed.eco_code)
        session.add(analyzed_game)
        await session.flush()
        move_rows: list[Move] = []
        for move in parsed.moves:
            is_mistake = move.ply == 4
            move_rows.append(
                Move(
                    game_id=analyzed_game.id,
                    ply=move.ply,
                    san=move.san,
                    fen=move.fen,
                    eval_cp=0,
                    best_move_san=move.san,
                    delta_cp=120 if is_mistake else 0,
                    classification="mistake" if is_mistake else "good",
                    engine_depth=10,
                )
            )
        session.add_all(move_rows)
        await session.flush()
        session.add(
            MotifRow(
                move_id=move_rows[3].id,
                type="weak_square",
                description="Конь на c6 не защищает поле f7",
                importance=4,
            )
        )
        commentary = Commentary(
            game_id=analyzed_game.id,
            level=Level.club,
            text=ORIGINAL_TEXT,
            model="test-model",
            validated=True,
        )
        session.add(commentary)
        await session.flush()
        good_edit = Edit(
            commentary_id=commentary.id,
            original_text=ORIGINAL_TEXT,
            edited_text=EDITED_TEXT,
        )
        session.add(good_edit)
        await session.flush()

        # 2) Партия без анализа — hit «no_analysis».
        no_analysis_game = Game(pgn=PGN)
        session.add(no_analysis_game)
        await session.flush()
        no_analysis_commentary = Commentary(
            game_id=no_analysis_game.id,
            level=Level.club,
            text=ORIGINAL_TEXT,
            model="test-model",
        )
        session.add(no_analysis_commentary)
        await session.flush()
        session.add(
            Edit(
                commentary_id=no_analysis_commentary.id,
                original_text=ORIGINAL_TEXT,
                edited_text=EDITED_TEXT,
            )
        )

        # 3) Партия с битым PGN — hit «invalid_pgn».
        broken_game = Game(pgn="совсем не pgn")
        session.add(broken_game)
        await session.flush()
        broken_commentary = Commentary(
            game_id=broken_game.id,
            level=Level.club,
            text=ORIGINAL_TEXT,
            model="test-model",
        )
        session.add(broken_commentary)
        await session.flush()
        session.add(
            Edit(
                commentary_id=broken_commentary.id,
                original_text=ORIGINAL_TEXT,
                edited_text=EDITED_TEXT,
            )
        )
        await session.commit()
        return {
            "game_ids": [
                analyzed_game.id,
                no_analysis_game.id,
                broken_game.id,
            ],
            "analyzed_game_id": analyzed_game.id,
            "commentary_id": commentary.id,
            "edit_id": good_edit.id,
        }


async def _delete_games(game_ids: list[int]) -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                Game.__table__.delete().where(Game.id.in_(game_ids))
            )
    finally:
        await engine.dispose()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line
    ]
    return [json.loads(line) for line in lines]


def test_export_v2_builds_fact_package_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = asyncio.run(_seed())
    output = tmp_path / "dataset_v2.jsonl"
    try:
        code = main(
            [
                "--schema-version",
                "2",
                "--min-id",
                str(seeded["edit_id"]),
                "--output",
                str(output),
            ]
        )

        assert code == 0
        captured = capsys.readouterr()
        # Одна запись выгружена, две пропущены по разным причинам.
        assert "Экспортировано записей: 1" in captured.out
        assert "Пропущено записей: 2" in captured.out
        assert "нет анализа ходов" in captured.out
        assert "PGN не разобран" in captured.out
        assert "schema_version=2" in captured.out
        assert "ПРЕДУПРЕЖДЕНИЕ" in captured.err

        # JSONL валиден с точки зрения обучения.
        count, errors = validate_dataset(output)
        assert errors == []
        assert count == 1

        records = _read_jsonl(output)
        assert len(records) == 1
        record = records[0]
        assert record["schema_version"] == SCHEMA_VERSION == 2
        messages = record["messages"]
        assert [message["role"] for message in messages] == [
            "system",
            "user",
            "assistant",
        ]
        assert messages[0]["content"] == load_system_prompt("club")
        user = messages[1]["content"]
        assert "# Партия" in user
        assert "# Ходы партии (SAN)" in user
        assert "# Ключевые моменты" in user
        assert "1. e4 e5" in user
        assert "Nc6" in user
        assert messages[2]["content"] == EDITED_TEXT

        metadata = record["metadata"]
        assert metadata["id"] == seeded["edit_id"]
        assert metadata["commentary_id"] == seeded["commentary_id"]
        assert metadata["game_id"] == seeded["analyzed_game_id"]
        assert metadata["editor_id"] is None
        assert metadata["level"] == "club"
        assert metadata["model"] == "test-model"
        assert metadata["created_at"]
        assert metadata["fact_sheet_chars"] == len(user)
    finally:
        asyncio.run(_delete_games(seeded["game_ids"]))


def test_export_v1_flag_uses_original_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = asyncio.run(_seed())
    output = tmp_path / "dataset_v1.jsonl"
    try:
        code = main(
            [
                "--schema-version",
                "1",
                "--min-id",
                str(seeded["edit_id"]),
                "--output",
                str(output),
            ]
        )

        assert code == 0
        captured = capsys.readouterr()
        assert "schema_version=1" in captured.out

        records = _read_jsonl(output)
        assert len(records) == 3
        for record in records:
            assert record["schema_version"] == LEGACY_SCHEMA_VERSION
            assert record["messages"][1]["content"] == ORIGINAL_TEXT
            assert record["messages"][2]["content"] == EDITED_TEXT
            assert "metadata" not in record
    finally:
        asyncio.run(_delete_games(seeded["game_ids"]))
