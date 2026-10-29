"""Фабрика LLM-клиентов по уровню ассистента (T-013).

Маршрутизация (SPEC §3, решение итерации 6):

| Уровень | Провайдер | Настройки |
| :--- | :--- | :--- |
| `beginner` | LM Studio (локально) | `vllm_base_url`, `vllm_model`, `vllm_api_key` |
| `club` | LM Studio (локально) | те же |
| `gm` | DeepSeek (облако) | `deepseek_base_url`, `deepseek_model`, `deepseek_api_key` |

Почему так: новичку и клубному игроку нужна дешёвая локальная модель (и
приватность партии — она не уходит в облако), гроссмейстеру — сильная
облачная. Ключ DeepSeek обязателен: без него `gm` падает
`LLMNotConfiguredError`, а не «молча» переключается на локальную модель.
"""

from __future__ import annotations

import httpx
import structlog

from app.config import Settings, get_settings
from app.errors import InvalidLevelError, LLMNotConfiguredError
from app.llm.base import BaseLLMClient
from app.llm.openai_client import OpenAICompatibleClient

__all__ = ["LOCAL_LEVELS", "get_llm_client", "resolve_llm_provider"]

logger = structlog.get_logger(__name__)

#: Уровни, которые обслуживает локальная LM Studio.
LOCAL_LEVELS = frozenset({"beginner", "club"})


def resolve_llm_provider(level: str) -> str:
    """Имя провайдера для уровня: ``lmstudio`` или ``deepseek``.

    Raises:
        InvalidLevelError: уровень не из ``beginner`` / ``club`` / ``gm``.
    """
    if level in LOCAL_LEVELS:
        return "lmstudio"
    if level == "gm":
        return "deepseek"
    raise InvalidLevelError(
        f"Неизвестный уровень {level!r}; ожидается beginner, club или gm"
    )


def get_llm_client(
    level: str,
    settings: Settings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> BaseLLMClient:
    """Собрать LLM-клиент под уровень.

    Args:
        level: ``beginner`` / ``club`` (локально) или ``gm`` (DeepSeek).
        settings: настройки (по умолчанию — из окружения).
        client: готовый `httpx.AsyncClient` (тесты с `MockTransport`).

    Returns:
        Готовый клиент `BaseLLMClient`.

    Raises:
        InvalidLevelError: неизвестный уровень.
        LLMNotConfiguredError: для DeepSeek не задан API-ключ.
    """
    cfg = settings or get_settings()
    provider = resolve_llm_provider(level)

    if provider == "lmstudio":
        # T-104: если LoRA-адаптер существует, vLLM принимает его basename
        # как имя модели (нужен запуск с --lora-modules). Если LORA_ADAPTER_PATH
        # задан, но пути нет — работаем базовой моделью и явно предупреждаем.
        if cfg.lora_adapter_available:
            logger.info(
                "llm_lora_adapter_enabled",
                level=level,
                adapter_path=cfg.lora_adapter_path,
                model=cfg.lora_model,
            )
        elif cfg.lora_adapter_dir is not None:
            logger.warning(
                "llm_lora_adapter_unavailable",
                level=level,
                adapter_path=cfg.lora_adapter_path,
                model=cfg.vllm_model,
                reason="path missing or adapter_config.json not found",
            )
        return OpenAICompatibleClient(
            base_url=cfg.vllm_base_url,
            model=cfg.lora_model,
            api_key=cfg.vllm_api_key,
            provider_name="lmstudio",
            timeout_s=cfg.llm_timeout_s,
            max_retries=cfg.llm_max_retries,
            retry_backoff_s=cfg.llm_retry_backoff_s,
            client=client,
            max_tokens=cfg.llm_max_tokens,
            temperature=cfg.llm_temperature,
            price_in_usd_per_1m=cfg.llm_local_price_in_usd_per_1m,
            price_out_usd_per_1m=cfg.llm_local_price_out_usd_per_1m,
        )

    api_key = (cfg.deepseek_api_key or "").strip()
    if not api_key:
        raise LLMNotConfiguredError(
            "DEEPSEEK_API_KEY не задан: уровень gm требует облачную модель (T-013)"
        )
    return OpenAICompatibleClient(
        base_url=cfg.deepseek_base_url,
        model=cfg.deepseek_model,
        api_key=api_key,
        provider_name="deepseek",
        timeout_s=cfg.llm_timeout_s,
        max_retries=cfg.llm_max_retries,
        retry_backoff_s=cfg.llm_retry_backoff_s,
        client=client,
        max_tokens=cfg.llm_max_tokens,
        temperature=cfg.llm_temperature,
        price_in_usd_per_1m=cfg.llm_price_in_usd_per_1m,
        price_out_usd_per_1m=cfg.llm_price_out_usd_per_1m,
    )
