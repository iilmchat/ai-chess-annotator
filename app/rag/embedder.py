"""Эмбеддинги для RAG: LM Studio (768) и детерминированный fallback.

Production использует OpenAI-совместимый endpoint LM Studio
`POST /v1/embeddings` с моделью `text-embedding-nomic-embed-text-v1.5`
(768 измерений, L2-нормализация). `HashEmbedder` оставлен как
детерминированный fallback для тестов/CI и аварийного режима.

Любая сетевая/HTTP/JSON-ошибка превращается в `EmbeddingError` (503):
`/v1/ingest` и RAG-контекст не должны падать трейсбеком. Размерность
ответа модели проверяется **до** записи в БД: иначе PostgreSQL ответит
невнятной ошибкой приведения `vector`, а не подсказкой «модель 1024,
а колонка 768».
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

import httpx
import structlog

from app.config import Settings, get_settings
from app.errors import EmbeddingError

__all__ = [
    "BATCH_SIZE",
    "DIMENSION",
    "Embedder",
    "HashEmbedder",
    "LMStudioEmbedder",
    "get_embedder",
    "position_document",
]

logger = structlog.get_logger(__name__)

#: Размерность совпадает с `annotated_games.embedding` (после миграции на 768).
DIMENSION = 768

#: Сколько текстов отправлять в одном HTTP-запросе `embed_many`.
BATCH_SIZE = 32

_TOKEN_RE = re.compile(r"[0-9a-zа-яё+#=]+")


class Embedder(Protocol):
    """Интерфейс эмбеддера: текст → вектор фиксированной размерности."""

    @property
    def dimension(self) -> int: ...

    async def embed(self, text: str) -> list[float]:
        """Вернуть L2-нормализованный вектор."""
        ...


class HashEmbedder:
    """Детерминированный хеш-эмбеддер (fallback для тестов/CI)."""

    def __init__(self, dimension: int = DIMENSION) -> None:
        if dimension < 1:
            raise ValueError("dimension должен быть ≥ 1")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        return _normalize(vector)


class LMStudioEmbedder:
    """Эмбеддер через OpenAI-совместимый `/embeddings` LM Studio.

    Args:
        base_url: база API (для LM Studio — с `/v1`).
        model: имя embedding-модели.
        api_key: ключ; LM Studio его игнорирует, но OpenAI-совместимым
            клиентам нужен непустой Bearer.
        dimension: ожидаемая размерность вектора; обязана совпадать с
            `annotated_games.embedding` (`vector(N)`).
        timeout_s: таймаут одного HTTP-запроса.
        batch_size: сколько текстов отправлять в одном запросе `embed_many`.
        client: готовый `httpx.AsyncClient` (в тестах — с `MockTransport`).
            Владелец клиента — вызывающий код; без него на каждый запрос
            создаётся короткоживущий клиент.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        dimension: int = DIMENSION,
        timeout_s: float = 60.0,
        batch_size: int = BATCH_SIZE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url не может быть пустым")
        if not model:
            raise ValueError("model не может быть пустым")
        if dimension < 1:
            raise ValueError("dimension должен быть ≥ 1")
        if batch_size < 1:
            raise ValueError("batch_size должен быть ≥ 1")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._dimension = dimension
        self._timeout_s = timeout_s
        self._batch_size = batch_size
        self._client = client

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model(self) -> str:
        return self._model

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/embeddings"

    async def embed(self, text: str) -> list[float]:
        """Эмбеддинг одного текста одним HTTP-запросом.

        Raises:
            EmbeddingError: сеть/HTTP/таймаут/кривой ответ или размерность
                вектора не совпала с ожидаемой.
        """
        vectors = await self._request(text)
        return vectors[0]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """Эмбеддинги списка текстов, по `batch_size` на HTTP-запрос.

        Порядок результатов совпадает с порядком `texts`. Пустой список
        не делает ни одного HTTP-запроса.
        """
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            chunk = list(texts[start : start + self._batch_size])
            vectors.extend(await self._request(chunk))
        return vectors

    # --- внутреннее -------------------------------------------------------

    async def _request(self, text_input: str | list[str]) -> list[list[float]]:
        expected = 1 if isinstance(text_input, str) else len(text_input)
        payload: dict[str, Any] = {"model": self._model, "input": text_input}
        response = await self._post(payload)
        return self._parse_vectors(response, expected)

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            if self._client is not None:
                response = await self._client.post(
                    self.endpoint, json=payload, headers=headers
                )
            else:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self._timeout_s)
                ) as client:
                    response = await client.post(
                        self.endpoint, json=payload, headers=headers
                    )
        except httpx.TimeoutException as exc:
            raise EmbeddingError(
                f"LM Studio embeddings: таймаут {self._timeout_s:g} с "
                f"(модель {self._model}). Проверьте LLM_TIMEOUT_S и загрузку "
                "сервера."
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"LM Studio embeddings: сеть недоступна: {exc}. Проверьте, что "
                f"LM Studio запущен на {self._base_url}."
            ) from exc

        if response.status_code != httpx.codes.OK:
            raise EmbeddingError(
                f"LM Studio embeddings: HTTP {response.status_code} для модели "
                f"{self._model}: {response.text[:300]}. Проверьте, что модель "
                "загружена в LM Studio."
            )
        return response

    def _parse_vectors(
        self, response: httpx.Response, expected: int
    ) -> list[list[float]]:
        try:
            payload: Any = response.json()
            items: Any = payload["data"]
            if not isinstance(items, list):
                raise TypeError("data не список")
            vectors = [
                [float(value) for value in item["embedding"]]
                for item in _order_by_index(items)
            ]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise EmbeddingError(
                f"LM Studio embeddings: неожиданный ответ: {response.text[:300]}"
            ) from exc

        if len(vectors) != expected:
            raise EmbeddingError(
                f"LM Studio embeddings: ожидался {expected} вектор(а), а модель "
                f"{self._model} вернула {len(vectors)}. Проверьте входной batch."
            )
        return [self._normalize(vector) for vector in vectors]

    def _normalize(self, vector: list[float]) -> list[float]:
        model_dim = len(vector)
        if model_dim != self._dimension:
            raise EmbeddingError(
                f"Модель {self._model} вернула вектор размерности {model_dim}, "
                f"а БД ждёт {self._dimension} "
                f"(annotated_games.embedding vector({self._dimension})). "
                "Проверьте EMBEDDING_MODEL/EMBEDDING_DIM."
            )
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm == 0.0:
            raise EmbeddingError(
                f"Модель {self._model} вернула вырожденный вектор "
                f"(норма {norm!r}); эмбеддинг непригоден"
            )
        return [value / norm for value in vector]


def _order_by_index(items: list[Any]) -> list[Any]:
    """Упорядочить `data[]` по `index`, если он есть у всех элементов.

    OpenAI-совместимые API обычно отдают элементы в порядке запроса, но
    спецификация оставляет поле `index`; сортировка по нему делает
    `embed_many` устойчивым к перестановке.
    """
    indexed: list[tuple[int, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int):
            return items
        indexed.append((item["index"], item))
    indexed.sort(key=lambda pair: pair[0])
    return [item for _, item in indexed]


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def position_document(fen: str) -> str:
    """Текст, который эмбеддится для позиции: FEN дважды (вес позиции выше)."""
    return f"fen {fen}\n{fen}"


def get_embedder(settings: Settings | None = None) -> Embedder:
    """Выбрать эмбеддер: `lmstudio` (prod) или `hash` (тесты/CI)."""
    cfg = settings or get_settings()
    provider = cfg.embedding_provider.strip().lower()
    if provider == "hash":
        return HashEmbedder(cfg.embedding_dim)
    if provider == "lmstudio":
        return LMStudioEmbedder(
            base_url=cfg.vllm_base_url,
            model=cfg.embedding_model,
            api_key=cfg.vllm_api_key,
            dimension=cfg.embedding_dim,
            timeout_s=cfg.llm_timeout_s,
        )
    raise ValueError(
        f"Неизвестный EMBEDDING_PROVIDER={provider!r}; ожидается lmstudio или hash"
    )
