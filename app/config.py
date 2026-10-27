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

    # LLM
    llm_provider_local: str = "vllm"
    vllm_base_url: str = "http://localhost:8001/v1"
    vllm_model: str = "Qwen/Qwen3-8B"
    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

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