"""RAG-сервис: ingest аннотированных партий и поиск похожих позиций (T-017).

Модель данных — таблица `annotated_games` из первой миграции:
`source`, `pgn`, `white`, `black`, `event`, `year`, `annotation_text`,
`embedding vector(1024)`.

Эмбеддинг строится по **позиции** (FEN после последнего хода партии) —
именно позицию ищет `find_similar_positions`; текст аннотации хранится
отдельно и возвращается как контекст для LLM.
"""

from __future__ import annotations

import io
import re

import chess
import chess.pgn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnnotatedGame
from app.db.session import SessionLocal
from app.ingestion.parser import parse_fen
from app.rag.embedder import Embedder, get_embedder, position_document

__all__ = [
    "find_similar_positions",
    "ingest_pgn_text",
    "parse_pgn_games",
]

_YEAR_RE = re.compile(r"^(\d{4})")


def parse_pgn_games(pgn_text: str) -> list[chess.pgn.Game]:
    """Разобрать все партии из PGN-потока (пустые пропускаются)."""
    games: list[chess.pgn.Game] = []
    stream = io.StringIO(pgn_text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        if not list(game.mainline_moves()):
            continue
        games.append(game)
    return games


def _final_fen(game: chess.pgn.Game) -> str:
    board = game.board()
    for move in game.mainline_moves():
        board.push(move)
    return board.fen()


def _collect_annotations(game: chess.pgn.Game) -> str:
    """Комментарии из PGN (узлы главной линии), по строке на комментарий."""
    comments = [
        node.comment.strip() for node in game.mainline() if node.comment.strip()
    ]
    return "\n".join(comments)


def _year(headers: chess.pgn.Headers) -> int | None:
    match = _YEAR_RE.match(headers.get("Date", ""))
    return int(match.group(1)) if match else None


async def ingest_pgn_text(
    session: AsyncSession,
    pgn_text: str,
    *,
    source: str = "upload",
    embedder: Embedder | None = None,
    limit: int | None = None,
) -> list[int]:
    """Загрузить все партии из PGN в `annotated_games`.

    Args:
        limit: сколько первых партий загрузить (``None`` — все). Нужен
            CLI-скрипту для выборочного ingest-а больших файлов.

    Returns:
        Список `id` созданных записей (в порядке партий).
    """
    if limit is not None and limit < 1:
        raise ValueError("limit должен быть ≥ 1")
    games = parse_pgn_games(pgn_text)
    if limit is not None:
        games = games[:limit]
    if not games:
        return []

    emb = embedder or get_embedder()
    # Все позиции эмбеддятся одним батчем (LMStudioEmbedder сам режет на
    # чанки по 32): для больших PGN это один round-trip вместо N.
    documents = [position_document(_final_fen(game)) for game in games]
    embeddings = await _embed_documents(emb, documents)

    ids: list[int] = []
    for game, embedding in zip(games, embeddings, strict=True):
        headers = game.headers
        row = AnnotatedGame(
            source=source[:64],
            pgn=str(game),
            white=headers.get("White") or None,
            black=headers.get("Black") or None,
            event=headers.get("Event") or None,
            year=_year(headers),
            annotation_text=_collect_annotations(game) or None,
            embedding=embedding,
        )
        session.add(row)
        await session.flush()
        ids.append(row.id)
    return ids


async def _embed_documents(
    embedder: Embedder, documents: list[str]
) -> list[list[float]]:
    """Эмбеддинги документов: батч, если эмбеддер умеет `embed_many`.

    `HashEmbedder` (fallback для тестов/CI) реализует только `embed`,
    поэтому для него используется последовательный путь.
    """
    embed_many = getattr(embedder, "embed_many", None)
    if callable(embed_many):
        vectors: list[list[float]] = await embed_many(documents)
        return vectors
    return [await embedder.embed(document) for document in documents]


async def find_similar_positions(
    fen: str,
    top_k: int = 3,
    *,
    session: AsyncSession | None = None,
    embedder: Embedder | None = None,
) -> list[str]:
    """Найти аннотации партий с ближайшей позицией (косинусное расстояние).

    Args:
        fen: позиция запроса.
        top_k: сколько результатов вернуть.
        session: сессия БД; если не передана — создаётся своя.
        embedder: эмбеддер; по умолчанию `get_embedder()`.

    Returns:
        Тексты аннотаций (или описание партии, если аннотации нет).

    Raises:
        InvalidFenError: FEN некорректен.
    """
    if top_k < 1:
        raise ValueError("top_k должен быть ≥ 1")
    board = parse_fen(fen)
    emb = embedder or get_embedder()
    vector = await emb.embed(position_document(board.fen()))

    owns_session = session is None
    db_session = session if session is not None else SessionLocal()
    try:
        distance = AnnotatedGame.embedding.cosine_distance(vector)
        statement = (
            select(AnnotatedGame)
            .where(AnnotatedGame.embedding.is_not(None))
            .order_by(distance)
            .limit(top_k)
        )
        rows = (await db_session.scalars(statement)).all()
    finally:
        if owns_session:
            await db_session.close()

    return [_describe(row) for row in rows]


def _describe(row: AnnotatedGame) -> str:
    if row.annotation_text:
        return row.annotation_text
    title = f"{row.white or '?'} — {row.black or '?'}"
    if row.event:
        title = f"{title} ({row.event})"
    return title
