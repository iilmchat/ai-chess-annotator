"""Общие настройки тестов.

CI не имеет LM Studio, поэтому тесты используют детерминированный
768-мерный `HashEmbedder`; production-дефолт остаётся `lmstudio`.
Админ-токен фиксирован, чтобы тесты `/v1/ingest` не зависели от `.env`.
"""

from __future__ import annotations

import os

os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-token")
