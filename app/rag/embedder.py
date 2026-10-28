"""Эмбеддинги для RAG: LM Studio (768) и детерминированный fallback.

Production использует OpenAI-совместимый endpoint LM Studio
`POST /v1/embeddings` с моделью `text-embedding-nomic-embed-text-v1.5`
(768 измерений, L2-нормализация). `HashEmbedder` оставлен как
детерминированный fallback для тестов/CI и аварийного режима.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

import httpx
import structlog

from app.config import Settings, get_settings
from app.errors import LLMError

__all__ = [
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
    """Эмбеддер через OpenAI-совместимый `/embeddings` LM Studio."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        dimension: int = DIMENSION,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url не может быть пустым")
        if not model:
            raise ValueError("model не может быть пустым")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._dimension = dimension
        self._timeout_s = timeout_s
        self._client = client

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/embeddings"

    async def embed(self, text: str) -> list[float]:
        payload: dict[str, Any] = {"model": self._model, "input": text}
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
        except httpx.HTTPError as exc:
            raise LLMError(f"lmstudio embeddings: сеть недоступна: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise LLMError(
                f"lmstudio embeddings: HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            data = response.json()
            raw = data["data"][0]["embedding"]
            vector = [float(value) for value in raw]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(
                "lmstudio embeddings: неожиданный ответ "
                f"{response.text[:300]}"
            ) from exc
        if len(vector) != self._dimension:
            raise LLMError(
                f"lmstudio embeddings: ожидалось {self._dimension} измерений, "
                f"получено {len(vector)}"
            )
        return _normalize(vector)


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
