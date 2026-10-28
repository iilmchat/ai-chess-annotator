"""LoRA-адаптер в конфиге и фабрике клиента (T-104)."""

from __future__ import annotations

from app.config import Settings
from app.llm import get_llm_client


def test_lora_model_uses_adapter_basename() -> None:
    cfg = Settings(lora_adapter_path="/models/qwen-chess-lora")

    assert cfg.lora_model == "qwen-chess-lora"


def test_without_adapter_uses_base_model() -> None:
    cfg = Settings()

    assert cfg.lora_model == cfg.vllm_model


async def test_local_client_uses_lora_model() -> None:
    cfg = Settings(lora_adapter_path="/models/qwen-chess-lora")
    client = get_llm_client("club", cfg)
    try:
        assert client.model == "qwen-chess-lora"
    finally:
        await client.aclose()
