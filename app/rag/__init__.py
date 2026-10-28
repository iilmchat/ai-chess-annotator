"""RAG: ingest аннотированных партий и поиск похожих позиций (T-017)."""

from __future__ import annotations

from app.rag.context import make_rag_context_builder
from app.rag.embedder import (
    DIMENSION,
    Embedder,
    HashEmbedder,
    get_embedder,
    position_document,
)
from app.rag.service import find_similar_positions, ingest_pgn_text, parse_pgn_games

__all__ = [
    "DIMENSION",
    "Embedder",
    "HashEmbedder",
    "find_similar_positions",
    "get_embedder",
    "ingest_pgn_text",
    "make_rag_context_builder",
    "parse_pgn_games",
    "position_document",
]
