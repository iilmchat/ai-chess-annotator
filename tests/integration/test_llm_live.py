"""Живые тесты LLM-адаптера (T-013).

Идут в реальные API:

* уровень `club` → LM Studio (`VLLM_BASE_URL`, модель `VLLM_MODEL`);
* уровень `gm` → DeepSeek (`DEEPSEEK_BASE_URL`, `DEEPSEEK_API_KEY`).

Если провайдер недоступен (CI, другая машина) — тест **скипается**, поэтому
`pytest -q` остаётся зелёным без сети. Ключи в логи не пишутся.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import get_settings
from app.llm import get_llm_client

PROMPT = "Скажи 'Привет, шахматы!'"


async def _lmstudio_responds() -> bool:
    settings = get_settings()
    timeout = httpx.Timeout(3.0)
    headers = {"Authorization": f"Bearer {settings.vllm_api_key}"}
    url = f"{settings.vllm_base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _lmstudio_available() -> bool:
    try:
        return asyncio.run(_lmstudio_responds())
    except Exception:
        return False


def _deepseek_configured() -> bool:
    return bool((get_settings().deepseek_api_key or "").strip())


@pytest.mark.skipif(not _lmstudio_available(), reason="LM Studio недоступен")
async def test_live_club_uses_lmstudio() -> None:
    client = get_llm_client("club")

    try:
        result = await client.complete(PROMPT)
    finally:
        await client.aclose()

    assert client.provider == "lmstudio"
    assert result.text
    assert "привет" in result.text.lower()
    assert result.usage.tokens_in > 0
    assert result.usage.tokens_out > 0


@pytest.mark.skipif(not _deepseek_configured(), reason="DEEPSEEK_API_KEY не задан")
async def test_live_gm_uses_deepseek() -> None:
    client = get_llm_client("gm")

    try:
        result = await client.complete(PROMPT)
    finally:
        await client.aclose()

    assert client.provider == "deepseek"
    assert result.text
    assert "привет" in result.text.lower()
    assert result.usage.tokens_in > 0
    assert result.usage.tokens_out > 0
