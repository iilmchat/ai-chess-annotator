"""Фабрика LLM-клиентов по уровню ассистента (T-013, T-123).

Маршрутизация (SPEC §3, §10; решение итерации 6, роутер — итерации 58):

| Уровень | Провайдер | Настройки |
| :--- | :--- | :--- |
| `beginner` | LM Studio (локально) | `vllm_base_url`, `vllm_model`, `vllm_api_key` |
| `club` | LM Studio (локально) → DeepSeek (fallback) | локальные настройки + `BackendRouter` |
| `gm` | DeepSeek (облако) | `deepseek_base_url`, `deepseek_model`, `deepseek_api_key` |

Почему так: новичку нужна дешёвая локальная модель и приватность партии
(она не уходит в облако) — уровень `beginner` остаётся строго локальным
(RULES §7.3). Клубному игроку — локальная модель с автоматическим fallback
на DeepSeek, если GPU/туннель недоступен (SPEC §10, T-123): `BackendRouter`
логирует `local_gpu_unavailable` и переключается на облако. Гроссмейстеру —
сильная облачная модель напрямую. Ключ DeepSeek обязателен для `gm`: без
него `LLMNotConfiguredError`, а не «молчаливое» переключение на локальную
модель. Для `club` без ключа роутер остаётся локальным (fallback выключен).
"""

from __future__ import annotations

import httpx
import structlog

from app.config import Settings, get_settings
from app.errors import InvalidLevelError, LLMNotConfiguredError
from app.llm.base import BaseLLMClient
from app.llm.openai_client import OpenAICompatibleClient
from app.llm.router import BackendRouter, log_fallback_disabled

__all__ = ["LOCAL_LEVELS", "get_llm_client", "resolve_llm_provider"]

logger = structlog.get_logger(__name__)

#: Уровни, которые обслуживает локальная LM Studio.
LOCAL_LEVELS = frozenset({"beginner", "club"})

_DEEPSEEK_KEY_ERROR = "DEEPSEEK_API_KEY не задан: уровень gm требует облачную модель (T-013)"


def resolve_llm_provider(level: str) -> str:
    """Имя провайдера для уровня: ``lmstudio`` или ``deepseek``.

    Для `club` роутер локальный-first, поэтому провайдер по-прежнему
    ``lmstudio`` (облако — резерв).

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


def _local_client(
    cfg: Settings,
    level: str,
    *,
    timeout_s: float | None = None,
    max_retries: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> OpenAICompatibleClient:
    """Собрать клиент локальной LM Studio (с учётом LoRA-адаптера)."""
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
        timeout_s=timeout_s if timeout_s is not None else cfg.llm_timeout_s,
        max_retries=(
            max_retries if max_retries is not None else cfg.llm_max_retries
        ),
        retry_backoff_s=cfg.llm_retry_backoff_s,
        client=client,
        max_tokens=cfg.llm_max_tokens,
        temperature=cfg.llm_temperature,
        price_in_usd_per_1m=cfg.llm_local_price_in_usd_per_1m,
        price_out_usd_per_1m=cfg.llm_local_price_out_usd_per_1m,
    )


def _deepseek_client(
    cfg: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> OpenAICompatibleClient:
    """Собрать клиент облачного DeepSeek (ключ обязателен)."""
    api_key = (cfg.deepseek_api_key or "").strip()
    if not api_key:
        raise LLMNotConfiguredError(_DEEPSEEK_KEY_ERROR)
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


def get_llm_client(
    level: str,
    settings: Settings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> BaseLLMClient:
    """Собрать LLM-клиент под уровень.

    Args:
        level: ``beginner`` (локально), ``club`` (local-first роутер) или
            ``gm`` (DeepSeek).
        settings: настройки (по умолчанию — из окружения).
        client: готовый `httpx.AsyncClient` (тесты с `MockTransport`).

    Returns:
        Готовый клиент `BaseLLMClient`: для `club` — `BackendRouter`,
        иначе — `OpenAICompatibleClient`.

    Raises:
        InvalidLevelError: неизвестный уровень.
        LLMNotConfiguredError: для `gm` не задан API-ключ DeepSeek.
    """
    cfg = settings or get_settings()
    provider = resolve_llm_provider(level)

    if provider == "lmstudio":
        if level == "club":
            # T-123: local-first с fallback в облако. Локальный бэкенд
            # fail-fast (короткий таймаут, без ретраев): ретрай — это и есть
            # переход на резервный DeepSeek.
            local = _local_client(
                cfg,
                level,
                timeout_s=cfg.llm_router_local_timeout_s,
                max_retries=0,
                client=client,
            )
            backends: list[BaseLLMClient] = [local]
            if (cfg.deepseek_api_key or "").strip():
                backends.append(_deepseek_client(cfg, client=client))
            else:
                log_fallback_disabled(
                    level=level, reason="deepseek_api_key_missing"
                )
            return BackendRouter(
                backends, cooldown_s=cfg.llm_router_cooldown_s
            )
        return _local_client(cfg, level, client=client)

    return _deepseek_client(cfg, client=client)
