"""Тесты хеш-эмбеддера RAG (T-017)."""

from __future__ import annotations

import math

import httpx
import pytest

from app.errors import EmbeddingError
from app.rag.embedder import (
    DIMENSION,
    HashEmbedder,
    LMStudioEmbedder,
    position_document,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
OTHER = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


async def test_embedding_dimension_and_norm() -> None:
    vector = await HashEmbedder().embed(position_document(START))

    assert len(vector) == DIMENSION == 768
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


async def test_lmstudio_embedder_normalizes_response() -> None:
    raw = [3.0, 4.0, *([0.0] * (DIMENSION - 2))]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"embedding": raw}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = LMStudioEmbedder(
            base_url="http://127.0.0.1:8034/v1",
            model="text-embedding-nomic-embed-text-v1.5",
            api_key="lm-studio",
            dimension=DIMENSION,
            client=client,
        )
        vector = await embedder.embed("fen test")

    assert len(vector) == DIMENSION
    assert vector[0] == pytest.approx(0.6)
    assert vector[1] == pytest.approx(0.8)
    assert requests[0].url.path.endswith("/v1/embeddings")
    assert requests[0].headers["authorization"] == "Bearer lm-studio"
    assert requests[0].content.decode().find("nomic") >= 0


async def test_lmstudio_embedder_rejects_wrong_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [1.0, 2.0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = LMStudioEmbedder(
            base_url="http://127.0.0.1:8034/v1",
            model="nomic",
            client=client,
        )
        with pytest.raises(EmbeddingError, match="768") as exc_info:
            await embedder.embed("fen test")

    assert exc_info.value.code == "embedding_unavailable"
    assert exc_info.value.http_status == 503
