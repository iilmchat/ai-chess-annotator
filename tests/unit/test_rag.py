"""Тесты хеш-эмбеддера RAG (T-017)."""

from __future__ import annotations

import math

import pytest

from app.rag.embedder import DIMENSION, HashEmbedder, position_document

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
OTHER = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


async def test_embedding_dimension_and_norm() -> None:
    vector = await HashEmbedder().embed(position_document(START))

    assert len(vector) == DIMENSION == 1024
    norm = math.sqrt(sum(value * value for value in vector))
    assert norm == pytest.approx(1.0, abs=1e-9)


async def test_embedding_is_deterministic() -> None:
    embedder = HashEmbedder()
    text = position_document(START)

    first = await embedder.embed(text)
    second = await embedder.embed(text)

    assert first == second


async def test_different_positions_have_different_vectors() -> None:
    embedder = HashEmbedder()

    start = await embedder.embed(position_document(START))
    other = await embedder.embed(position_document(OTHER))

    assert start != other


async def test_empty_text_gives_zero_vector() -> None:
    vector = await HashEmbedder().embed("")

    assert len(vector) == DIMENSION
    assert all(value == 0.0 for value in vector)


def test_position_document_contains_fen_twice() -> None:
    document = position_document(START)

    assert document.count(START) == 2
    assert document.startswith("fen ")


def test_embedder_validates_dimension() -> None:
    with pytest.raises(ValueError):
        HashEmbedder(dimension=0)
