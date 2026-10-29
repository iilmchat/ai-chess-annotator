"""Тесты CLI массовой загрузки PGN (`scripts/ingest_pgn.py`, T-017)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import AnnotatedGame
from app.rag.embedder import HashEmbedder
from app.rag.service import ingest_pgn_text, parse_pgn_games
from scripts.ingest_pgn import build_parser, main

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PGN = REPO_ROOT / "tests" / "fixtures" / "rag_sample.pgn"

#: Метка source для строк, создаваемых интеграционным тестом (чистим по id).
DB_SOURCE = "cli-pytest"


# --- argparse -------------------------------------------------------------


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--file", "games.pgn"])

    assert args.file == Path("games.pgn")
    assert args.limit == 10
    assert args.source == "upload"


def test_parser_custom_values() -> None:
    args = build_parser().parse_args(
        ["--file", "bulk.pgn", "--limit", "3", "--source", "cli-demo"]
    )

    assert args.file == Path("bulk.pgn")
    assert args.limit == 3
    assert args.source == "cli-demo"


def test_parser_requires_file() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--limit", "2"])

    assert exc_info.value.code == 2


@pytest.mark.parametrize("limit", ["0", "-1", "-42"])
def test_main_rejects_non_positive_limit(
    limit: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--file", "games.pgn", "--limit", limit]) == 2

    captured = capsys.readouterr()
    assert "limit" in captured.err
    assert "≥ 1" in captured.err


def test_main_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0

    captured = capsys.readouterr()
    assert "--file" in captured.out
    assert "--limit" in captured.out
    assert "--source" in captured.out


def test_main_missing_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "absent.pgn"

    assert main(["--file", str(missing), "--limit", "2"]) == 2

    captured = capsys.readouterr()
    assert "Файл не найден" in captured.err
    assert str(missing) in captured.err


def test_main_unreadable_file_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _deny_read(self: Path) -> bytes:
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_bytes", _deny_read)

    assert main(["--file", str(SAMPLE_PGN), "--limit", "1"]) == 2

    captured = capsys.readouterr()
    assert "Не удалось прочитать файл" in captured.err
    assert "access denied" in captured.err
    assert "Traceback" not in captured.err


def test_main_returns_2_for_unknown_embedding_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bogus")
    get_settings.cache_clear()
    try:
        assert main(["--file", str(SAMPLE_PGN), "--limit", "1"]) == 2
    finally:
        get_settings.cache_clear()

    captured = capsys.readouterr()
    assert "EMBEDDING_PROVIDER" in captured.err
    assert "Traceback" not in captured.err


# --- фикстура -------------------------------------------------------------


def test_sample_fixture_has_three_annotated_games() -> None:
    games = parse_pgn_games(SAMPLE_PGN.read_text(encoding="utf-8"))

    assert len(games) == 3
    assert [game.headers["White"] for game in games] == [
        "White A",
        "White B",
        "White C",
    ]
    for game in games:
        assert list(game.mainline_moves())
        comments = [
            node.comment for node in game.mainline() if node.comment.strip()
        ]
        assert comments, f"в фикстуре нет комментариев: {game.headers}"


def test_limit_selects_first_two_games() -> None:
    games = parse_pgn_games(SAMPLE_PGN.read_text(encoding="utf-8"))

    assert [game.headers["White"] for game in games[:2]] == ["White A", "White B"]


# --- интеграция с PostgreSQL ----------------------------------------------


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
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


requires_db = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


@requires_db
async def test_ingest_first_two_games_persists_768_dim_embeddings() -> None:
    pgn_text = SAMPLE_PGN.read_text(encoding="utf-8")
    db_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ids: list[int] = []
    try:
        async with factory() as session:
            ids = await ingest_pgn_text(
                session,
                pgn_text,
                source=DB_SOURCE,
                limit=2,
                embedder=HashEmbedder(dimension=768),
            )
            await session.commit()

        assert len(ids) == 2

        async with factory() as session:
            rows = (
                await session.scalars(
                    select(AnnotatedGame).where(AnnotatedGame.id.in_(ids))
                )
            ).all()

        assert len(rows) == 2
        assert {row.white for row in rows} == {"White A", "White B"}
        assert all(row.source == DB_SOURCE for row in rows)
        assert all(row.embedding is not None for row in rows)
        assert all(len(row.embedding or []) == 768 for row in rows)
        assert all(row.annotation_text for row in rows)
    finally:
        if ids:
            async with factory() as session:
                await session.execute(
                    AnnotatedGame.__table__.delete().where(
                        AnnotatedGame.id.in_(ids)
                    )
                )
                await session.commit()
        await db_engine.dispose()
