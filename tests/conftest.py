"""Общие настройки тестов.

CI не имеет LM Studio, поэтому тесты используют детерминированный
768-мерный `HashEmbedder`; production-дефолт остаётся `lmstudio`.
Админ-токен фиксирован, чтобы тесты `/v1/ingest` не зависели от `.env`.
"""

from __future__ import annotations

import os

os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-token")
# Интеграционные тесты не должны упираться в общий дневной счётчик Redis:
# бюджет проверяется отдельно в tests/unit/test_token_budget.py.
os.environ.setdefault("TOKEN_BUDGET_ENABLED", "false")
# Фоновый subscriber не нужен в каждом TestClient; проверяется отдельно.
os.environ.setdefault("PROMPT_PUBSUB_ENABLED", "false")
os.environ.setdefault("SEMANTIC_CACHE_ENABLED", "false")
