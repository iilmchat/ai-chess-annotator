"""Эмбеддинги для RAG (T-017).

Пока используется **детерминированная заглушка** `HashEmbedder` (bag-of-words
hashing, 1024 измерения): она не требует torch/sentence-transformers и даёт
стабильные векторы для тестов и для поиска по точной/почти точной позиции.

Почему не `bge-m3` и не LM Studio:

* `bge-m3` тянет `sentence-transformers` + torch (гигабайты зависимостей);
* единственная embedding-модель в LM Studio — `text-embedding-nomic-embed-text-v1.5`
  с размерностью 768, а колонка `annotated_games.embedding` — `vector(1024)`
  из SPEC §4 (менять схему ради заглушки не хочется).

Поэтому хеш-эмбеддер по **FEN позиции** (именно её ищет retrieval), а текст
аннотации хранится отдельно в `annotation_text`. Замена на реальные
эмбеддинги — KI-025.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

__all__ = [
    "DIMENSION",
    "Embedder",
    "HashEmbedder",
    "get_embedder",
    "position_document",
]

#: Размерность совпадает с `annotated_games.embedding` (SPEC §4).
DIMENSION = 1024

_TOKEN_RE = re.compile(r"[0-9a-zа-яё+#=]+")


class Embedder(Protocol):
    """Интерфейс эмбеддера: текст → вектор фиксированной размерности."""

    @property
    def dimension(self) -> int: ...

    async def embed(self, text: str) -> list[float]:
        """Вернуть L2-нормализованный вектор."""
        ...


class HashEmbedder:
    """Детерминированный хеш-эмбеддер (заглушка до реальной модели).

    Каждый токен отображается в измерение по blake2b-хешу со знаком; вектор
    нормализуется. Одинаковый FEN → одинаковый вектор; близкие позиции
    делят токены (фигуры/поля) и потому ближе по косинусу.
    """

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
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


def position_document(fen: str) -> str:
    """Текст, который эмбеддится для позиции: FEN дважды (вес позиции выше)."""
    return f"fen {fen}\n{fen}"


_EMBEDDER: Embedder = HashEmbedder()


def get_embedder() -> Embedder:
    """Текущий эмбеддер (точка расширения для bge-m3)."""
    return _EMBEDDER
