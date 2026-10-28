from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_env: str = "dev"
    log_level: str = "INFO"
    api_prefix: str = "/v1"
    cors_origins: str = "http://localhost:5173"

    # DB
    # 127.0.0.1, а не localhost: на Windows localhost резолвится сначала в ::1,
    # а docker-compose публикует порты только на IPv4-loopback.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5434
    postgres_db: str = "chessannotator"
    postgres_user: str = "chess"
    postgres_password: str = "chess_dev_password"

    # Redis
    redis_url: str = "redis://127.0.0.1:6380/0"

    # Auth
    jwt_secret: str = "dev_secret_change_me"
    jwt_alg: str = "HS256"
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 7

    # Engine
    stockfish_path: str = "/usr/bin/stockfish"
    stockfish_host: str | None = None
    stockfish_port: int | None = None
    #: Число параллельных процессов Stockfish (KI-014 / SPEC §9).
    engine_pool_size: int = 4

    # Rate limiting (T-009)
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 10
    rate_limit_window_s: int = 60

    # LLM (T-013): локально — LM Studio (OpenAI-совместимый API), облако — DeepSeek.
    #: Историческое имя: локальный провайдер (compose/healthcheck).
    llm_provider_local: str = "lmstudio"
    #: Локальная модель (LM Studio). Имена vllm_* сохранены для совместимости.
    vllm_base_url: str = "http://127.0.0.1:8034/v1"
    #: Быстрая не-thinking модель: qwen3-8b «умнее», но генерирует reasoning
    #: и на этом хосте отвечает в разы дольше (см. отчёт итерации 6).
    vllm_model: str = "qwen/qwen3-4b-2507"
    #: LM Studio не проверяет ключ, но OpenAI-совместимые клиенты требуют
    #: непустой Authorization — отправляем заглушку.
    vllm_api_key: str = "lm-studio"
    #: Облачный провайдер для уровня «гроссмейстер».
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_api_key: str | None = None
    #: Задел на альтернативные облака (сейчас не маршрутизируются).
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    #: Таймауты и ретраи LLM-запросов.
    #: Верхняя граница ответа: без неё LM Studio может генерировать «до конца».
    llm_max_tokens: int = 1024
    #: Прогрев локальной модели при старте приложения (KI-022).
    llm_warmup_enabled: bool = True
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    llm_retry_backoff_s: float = 0.5

    # Embeddings
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024

    # Observability
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()