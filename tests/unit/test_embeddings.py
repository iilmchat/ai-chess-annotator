"""Тесты эмбеддингов LM Studio без сети (KI-025).

`httpx.MockTransport` эмулирует `/v1/embeddings` LM Studio, поэтому сюита
детерминирована и не требует запущенного сервера. Проверяется: URL, тело
запроса (model/input), Bearer-авторизация, единичный и батчевый режимы,
сохранение порядка, L2-нормализация, несовпадение размерности, HTTP 500,
таймаут, сетевая ошибка, битый JSON, вырожденный вектор и фабрика.

Живой провайдер проверяет отдельный файл
`tests/integration/test_embeddings_live.py`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.errors import EmbeddingError
from app.rag.embedder import (
    BATCH_SIZE,
    HashEmbedder,
    LMStudioEmbedder,
    get_embedder,
    position_document,
)

Handler = Callable[[httpx.Request], httpx.Response]

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

_BASE_URL = "http://lmstudio.test/v1"
_MODEL = "text-embedding-nomic-embed-text-v1.5"


def _embedding_response(
    vectors: list[list[float]], *, model: str = _MODEL
) -> dict[str, Any]:
    return {
        "object": "list",
        "model": model,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "embedding_provider": "lmstudio",
        "vllm_base_url": _BASE_URL,
        "embedding_model": _MODEL,
        "embedding_dim": 4,
        "vllm_api_key": "lm-studio",
        "llm_timeout_s": 1.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _embedder(
    client: httpx.AsyncClient, *, dimension: int = 4, batch_size: int = 2
) -> LMStudioEmbedder:
    return LMStudioEmbedder(
        base_url=_BASE_URL,
        model=_MODEL,
        dimension=dimension,
        api_key="lm-studio",
        timeout_s=1.0,
        batch_size=batch_size,
        client=client,
    )


# --- один текст -----------------------------------------------------------


async def test_embed_posts_openai_payload_with_bearer_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_embedding_response([[3.0, 4.0]]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = LMStudioEmbedder(
            base_url=_BASE_URL,
            model=_MODEL,
            dimension=2,
            api_key="secret-key",
            timeout_s=1.0,
            client=client,
        )
        vector = await embedder.embed("fen test")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{_BASE_URL}/embeddings"
    assert request.headers["authorization"] == "Bearer secret-key"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"model": _MODEL, "input": "fen test"}
    assert embedder.endpoint == f"{_BASE_URL}/embeddings"
    assert embedder.dimension == 2
    assert embedder.model == _MODEL
    assert vector == pytest.approx([0.6, 0.8])


async def test_embed_l2_normalizes_vector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embedding_response([[10.0, 0.0, 0.0]]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vector = await _embedder(client, dimension=3).embed("fen")

    assert vector == pytest.approx([1.0, 0.0, 0.0])
    assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0)


async def test_empty_api_key_omits_authorization_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_embedding_response([[1.0, 1.0]]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = LMStudioEmbedder(
            base_url=_BASE_URL,
            model=_MODEL,
            dimension=2,
            api_key="",
            client=client,
        )
        await embedder.embed("fen")

    assert "authorization" not in requests[0].headers


# --- батчи ----------------------------------------------------------------


async def test_embed_many_batches_by_batch_size_and_preserves_order() -> None:
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        texts = payload["input"]
        assert isinstance(texts, list)
        vectors = [[1.0, float(text.removeprefix("t"))] for text in texts]
        return httpx.Response(200, json=_embedding_response(vectors))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = _embedder(client, batch_size=2, dimension=2)
        vectors = await embedder.embed_many(["t1", "t2", "t3", "t4", "t5"])

    assert [payload["input"] for payload in payloads] == [
        ["t1", "t2"],
        ["t3", "t4"],
        ["t5"],
    ]
    assert all(payload["model"] == _MODEL for payload in payloads)
    assert len(vectors) == 5
    for expected_index, vector in enumerate(vectors, start=1):
        assert vector[0] == pytest.approx(1.0 / math.sqrt(1 + expected_index**2))
        assert vector[1] / vector[0] == pytest.approx(float(expected_index))


async def test_embed_many_honours_response_index_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # LM Studio/совместимые API отдают data[] с полем index; порядок
        # элементов в JSON может не совпадать с порядком запроса.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 2.0]},
                    {"index": 0, "embedding": [2.0, 0.0]},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vectors = await _embedder(client, dimension=2, batch_size=2).embed_many(
            ["a", "b"]
        )

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]


async def test_embed_many_empty_makes_no_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_embedding_response([]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await _embedder(client).embed_many([]) == []

    assert requests == []


# --- ошибки ---------------------------------------------------------------


async def test_dimension_mismatch_mentions_model_and_db_dimensions() -> None:
    model_vector = [0.0] * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embedding_response([model_vector]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = _embedder(client, dimension=768)
        with pytest.raises(EmbeddingError, match=r"1024.*768") as exc_info:
            await embedder.embed(position_document(START))

    assert exc_info.value.code == "embedding_unavailable"
    assert exc_info.value.http_status == 503


async def test_http_error_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="HTTP 500") as exc_info:
            await _embedder(client).embed("fen")

    assert exc_info.value.code == "embedding_unavailable"
    assert exc_info.value.http_status == 503


async def test_timeout_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="таймаут"):
            await _embedder(client).embed("fen")


async def test_connection_error_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="сеть недоступна"):
            await _embedder(client).embed("fen")


async def test_malformed_response_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"foo": "bar"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="неожиданный ответ"):
            await _embedder(client).embed("fen")


async def test_invalid_json_body_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{not valid json",
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="неожиданный ответ"):
            await _embedder(client).embed("fen")


async def test_non_list_data_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "not-a-list"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="неожиданный ответ"):
            await _embedder(client).embed("fen")


async def test_wrong_vector_count_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_embedding_response([[1.0, 0.0], [0.0, 1.0]])
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="1 вектор"):
            await _embedder(client, dimension=2).embed("fen")


async def test_zero_vector_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embedding_response([[0.0, 0.0]]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="вырожденный"):
            await _embedder(client, dimension=2).embed("fen")


async def test_non_finite_vector_raises_embedding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"data": [{"embedding": [NaN, 1.0]}]}',
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingError, match="вырожденный"):
            await _embedder(client, dimension=2).embed("fen")


# --- конструктор и фабрика ------------------------------------------------


def test_default_batch_size_is_32() -> None:
    embedder = LMStudioEmbedder(base_url=_BASE_URL, model=_MODEL, dimension=768)

    assert embedder.batch_size == BATCH_SIZE == 32


def test_constructor_validates_arguments() -> None:
    with pytest.raises(ValueError, match="base_url"):
        LMStudioEmbedder(base_url="", model=_MODEL, dimension=768)
    with pytest.raises(ValueError, match="model"):
        LMStudioEmbedder(base_url=_BASE_URL, model="", dimension=768)
    with pytest.raises(ValueError, match="dimension"):
        LMStudioEmbedder(base_url=_BASE_URL, model=_MODEL, dimension=0)
    with pytest.raises(ValueError, match="batch_size"):
        LMStudioEmbedder(
            base_url=_BASE_URL, model=_MODEL, dimension=768, batch_size=0
        )


def test_get_embedder_lmstudio_uses_current_config_keys() -> None:
    embedder = get_embedder(_settings(embedding_dim=768))

    assert isinstance(embedder, LMStudioEmbedder)
    assert embedder.dimension == 768
    assert embedder.model == _MODEL
    assert embedder.endpoint == f"{_BASE_URL}/embeddings"
    assert embedder.batch_size == BATCH_SIZE


async def test_get_embedder_hash_provider_works_offline() -> None:
    embedder = get_embedder(
        _settings(embedding_provider="hash", embedding_dim=768)
    )

    assert isinstance(embedder, HashEmbedder)
    vector = await embedder.embed(position_document(START))

    assert len(vector) == 768
    assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0)


def test_get_embedder_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        get_embedder(_settings(embedding_provider="bogus"))
