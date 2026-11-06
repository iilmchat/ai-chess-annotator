from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator, model_validator
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
    jwt_secret: str = "dev_secret_change_me_32_chars_min_000000"
    jwt_alg: str = "HS256"
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 7
    # KI-037: refresh-токен — в HttpOnly-cookie, а не в теле ответа.
    #: Имя cookie с refresh JWT.
    auth_cookie_name: str = "refresh_token"
    #: Флаг Secure (в проде за HTTPS — true; в dev по http — false).
    auth_cookie_secure: bool = False
    #: SameSite для refresh-cookie; lax — разумный дефолт для SPA.
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    #: Path cookie: refresh уходит только на /auth/*, не на весь API.
    auth_cookie_path: str = "/auth"
    #: Домен cookie; None — host-only (текущий хост).
    auth_cookie_domain: str | None = None
    #: Redis-denylist отозванных refresh-токенов (`/auth/logout`).
    refresh_denylist_enabled: bool = True

    @model_validator(mode="after")
    def _validate_auth_cookie_flags(self) -> "Settings":
        """SameSite=None без Secure браузеры отвергают — не допускаем конфиг.

        Проверка ловит мисконфиг на старте, а не «тихо потерянную» cookie в
        проде: `AUTH_COOKIE_SAMESITE=none` имеет смысл только под HTTPS.
        """
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError(
                "AUTH_COOKIE_SAMESITE=none требует AUTH_COOKIE_SECURE=true "
                "(браузеры не принимают SameSite=None без Secure)"
            )
        return self

    @model_validator(mode="after")
    def _validate_security_defaults(self) -> "Settings":
        """T-110: не пускать dev-секреты и `*`-CORS в production.

        Приложение всегда добавляет CORSMiddleware с
        ``allow_credentials=True``; браузерный стандарт запрещает сочетать
        это с ``Access-Control-Allow-Origin: *``, а реальная конфигурация
        с wildcard означала бы «любой сайт с cookie пользователя». Поэтому
        wildcard запрещён в любом окружении, а дефолтные/пустые секреты —
        только в prod (dev/test должны запускаться без настройки).
        """
        origins = self.cors_origin_list
        if "*" in origins:
            raise ValueError(
                "CORS_ORIGINS не может содержать '*': приложение отправляет "
                "allow_credentials=True (нужен явный список origin-ов)"
            )

        if self.app_env.strip().lower() not in {"prod", "production"}:
            return self

        secret = self.jwt_secret.strip()
        if len(secret) < 32 or self._looks_like_placeholder(secret):
            raise ValueError(
                "JWT_SECRET в production должен быть случайной строкой "
                "длиной ≥ 32 символов (например: openssl rand -hex 32); "
                "dev-плейсхолдеры запрещены"
            )

        admin_key = (self.admin_api_key or "").strip()
        if admin_key and (
            admin_key.lower()
            in {"dev-admin-token", "dev-admin-key", "admin", "changeme"}
            or "change_me" in admin_key.lower()
        ):
            raise ValueError(
                "ADMIN_API_KEY в production не должен быть dev-плейсхолдером; "
                "задайте случайный токен или оставьте пустым"
            )
        return self

    @staticmethod
    def _looks_like_placeholder(value: str) -> bool:
        """Похож ли секрет на дефолтный/документационный плейсхолдер."""
        normalized = value.strip().lower()
        return (
            normalized.startswith("dev_secret")
            or "change_me" in normalized
            or normalized in {"secret", "changeme", "change-me", "dev-secret"}
        )

    #: Тестовый seam (T-028): подмена get_current_user для старой сюиты.
    #: Работает ТОЛЬКО при APP_ENV=test; в dev/prod значение игнорируется.
    test_auth_bypass: bool = False

    # PDF-экспорт комментария (T-107)
    #: Путь к TrueType-шрифту с кириллицей; по умолчанию ищется Arial/
    #: DejaVuSans в системных каталогах Windows/Linux/macOS.
    pdf_font_path: str | None = None

    # Фоновая выгрузка датасета (KI-050, KI-053)
    #: Каталог готовых JSONL-файлов; вне web-root (`web/dist`), поэтому
    #: файлы не раздаются статикой — только через download-эндпоинт.
    export_dir: str = "data/exports"
    #: Сколько последних джоб показывать в админке.
    export_jobs_page_size: int = 20
    #: Имя очереди arq (KI-053); тесты используют уникальное имя.
    arq_queue_name: str = "arq:queue"
    #: `running`-джобы старше N минут при старте API/воркера → `failed`.
    export_job_stale_minutes: int = 15
    #: KI-054: сколько дней хранить `export_jobs` и готовые JSONL в
    #: `EXPORT_DIR`. Используется `scripts/prune_export_jobs.py` (cron).
    export_retention_days: int = 7

    # Email / верификация (KI-041)
    #: Публичный URL приложения (SPA) для ссылок в письмах: в dev — Vite
    #: (5173), в prod — тот же origin, что и API (SPA раздаётся `app.web`).
    #: Без завершающего `/`.
    app_base_url: str = "http://localhost:5173"
    #: `mock` — письмо в structlog; `smtp` — реальная отправка (T-082).
    email_sender: Literal["mock", "smtp"] = "mock"
    email_from: str = "noreply@chessannotator.local"
    #: Время жизни ссылки подтверждения email.
    email_verification_ttl_hours: int = 24
    #: Время жизни ссылки сброса пароля.
    password_reset_ttl_min: int = 30

    #: SMTP (T-082): используется только при ``EMAIL_SENDER=smtp``.
    #: Пустые `smtp_host`/`smtp_from` — ошибка конфигурации на старте
    #: (`create_email_sender` → `EmailDeliveryError`), а не «тихий» отказ
    #: при регистрации.
    smtp_host: str = ""
    smtp_port: int = 587
    #: Логин/пароль SMTP; пустой `smtp_user` — анонимная отправка (без auth).
    smtp_user: str = ""
    smtp_password: str = ""
    #: Адрес отправителя в заголовке `From` (обязателен для smtp).
    smtp_from: str = ""
    #: STARTTLS для порта 587 (по умолчанию) и неявный TLS для 465.
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    #: Таймаут SMTP-операции в секундах (aiosmtplib).
    smtp_timeout: float = 30.0

    @field_validator("email_sender", mode="before")
    @classmethod
    def _normalize_email_sender(cls, value: Any) -> Any:
        """`EMAIL_SENDER` не зависит от регистра и пробелов (``MOCK`` → ``mock``)."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    # Engine
    stockfish_path: str = "/usr/bin/stockfish"
    stockfish_host: str | None = None
    stockfish_port: int | None = None
    #: Число параллельных процессов Stockfish (KI-014 / SPEC §9).
    engine_pool_size: int = 4
    #: Кэш результатов Stockfish по позиции (T-108): ключ — позиция +
    #: версия движка + depth/multipv, TTL — `ENGINE_CACHE_TTL_DAYS`.
    engine_cache_enabled: bool = True
    engine_cache_ttl_days: int = 30

    # Rate limiting (T-009)
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 10
    rate_limit_window_s: int = 60

    # Admin API (T-031): защита /v1/ingest общим токеном до появления JWT.
    admin_api_key: str | None = None
    # Admin Panel (T-028): тот же токен принимается заголовком X-Admin-Token,
    # а форма входа /admin/login — паролем (или тем же токеном).
    admin_username: str = "admin"
    admin_password: str | None = None
    #: Секрет подписи cookie админ-сессии; по умолчанию — JWT_SECRET.
    admin_session_secret: str | None = None
    admin_session_ttl_min: int = 30
    #: Redis Pub/Sub-инвалидация кэша промптов между воркерами (KI-032).
    prompt_pubsub_enabled: bool = True

    # LLM (T-013): локально — LM Studio (OpenAI-совместимый API), облако — DeepSeek.
    #: Историческое имя: локальный провайдер (compose/healthcheck).
    llm_provider_local: str = "lmstudio"
    #: Путь к LoRA-адаптеру (T-104). Если задан, локальный клиент просит
    #: у vLLM adapter по имени (basename); LM Studio динамические адаптеры
    #: через OpenAI API не поддерживает — нужен vLLM с `--lora-modules`.
    lora_adapter_path: str | None = None
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
    #: Лимит ответа судьи LLM-as-judge (итерация 61). Судья возвращает JSON
    #: с тремя оценками и коротким комментарием; при общем лимите 1024 токена
    #: ответ обрезался и `_extract_json` падал с «в ответе судьи нет
    #: JSON-объекта» (2 из 5 партий baseline v3). Судья получает отдельный,
    #: более щедрый лимит, чтобы не менять длину обычных комментариев.
    llm_judge_max_tokens: int = 2048
    #: Температура генерации: ниже — меньше «фантазий», стабильнее ответы.
    llm_temperature: float = 0.2
    #: Прогрев локальной модели при старте приложения (KI-022).
    llm_warmup_enabled: bool = True

    # Commentary (T-016)
    #: Язык комментариев по умолчанию (T-106): `ru` или `en`. Запрос может
    #: переопределить его полем `language`; иначе учитывается
    #: `Accept-Language`, затем это значение.
    default_language: Literal["ru", "en"] = "ru"
    #: Сколько раз переспрашивать LLM, если валидатор нашёл нелегальные ходы.
    commentary_max_retries: int = 2
    #: KI-023: семантическая проверка утверждений о позиции («слон на e6»,
    #: «связка пешки f7»), а не только легальности SAN-ходов. На последней
    #: попытке невалидные предложения вырезаются вместо 502; флаг позволяет
    #: сравнивать качество с проверкой и без неё.
    semantic_validation_enabled: bool = True

    # Chat (T-022)
    #: Сколько раз переспрашивать LLM в чате при нелегальных ходах.
    chat_max_retries: int = 2

    # RAG (T-017)
    #: Сколько похожих аннотированных партий подмешивать в пакет фактов.
    rag_top_k: int = 3
    #: Включать RAG-контекст для уровня gm.
    rag_enabled: bool = True
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2
    llm_retry_backoff_s: float = 0.5
    #: T-123 / итерация 59: адрес локального LLM-бэкенда (LM Studio) глазами
    #: приложения. На dev-машине — прямой адрес; на облачном VPS — адрес
    #: обратного SSH-туннеля (например, ``http://172.17.0.1:8034/v1``).
    #: Если пусто — используется исторический `VLLM_BASE_URL` (совместимость
    #: с dev-окружениями); см. `local_llm_base_url`.
    llm_local_backend_url: str | None = None
    #: Таймаут HTTP-запроса локального бэкенда (LM Studio), секунды.
    #: Итерация 60: дефолт поднят с 2 до 120 с — 2 с короче реальной
    #: генерации 7B (10–15 с), из-за чего живой GPU считался недоступным и
    #: каждый `club`-запрос уходил в DeepSeek. Недоступный хост, который
    #: отвергает соединение, по-прежнему даёт мгновенный `ConnectError`
    #: (fallback без ожидания); таймаут ждётся только при «чёрной дыре» или
    #: зависшем туннеле, после чего circuit-breaker пропускает local на
    #: `LLM_ROUTER_COOLDOWN_S`. Опускать ниже p95 генерации нельзя.
    llm_local_timeout_s: float = 120.0
    #: Circuit-breaker роутера: сколько секунд не пробовать локальный GPU
    #: после ошибки соединения/таймаута (0 — пробовать каждый запрос).
    llm_router_cooldown_s: float = 60.0

    # Embeddings
    #: `lmstudio` — реальные эмбеддинги 768; `hash` — тесты/CI.
    embedding_provider: str = "lmstudio"
    embedding_model: str = "text-embedding-nomic-embed-text-v1.5"
    embedding_dim: int = 768

    # Semantic cache (T-032)
    semantic_cache_enabled: bool = True
    semantic_cache_ttl_days: int = 30
    #: Максимальное косинусное расстояние для semantic-hit.
    semantic_cache_similarity_threshold: float = 0.05

    # LLM pricing (KI-021 / T-030/T-031): доллары за 1M токенов.
    # Облачные цены — иллюстративные (DeepSeek), локальные — 0.
    llm_price_in_usd_per_1m: float = 0.27
    llm_price_out_usd_per_1m: float = 1.10
    llm_local_price_in_usd_per_1m: float = 0.0
    llm_local_price_out_usd_per_1m: float = 0.0

    # FinOps / token budget (T-031): дневные лимиты по тарифу из БД.
    token_budget_enabled: bool = True
    daily_token_limit_free: int = 50_000
    daily_token_limit_pro: int = 500_000
    #: Устарело (KI-038 → T-083): тир больше не выдаётся по IP, источник
    #: истины — `users.tier`. Поле оставлено, чтобы старые `.env` не падали
    #: на неизвестной настройке; значение нигде не используется.
    pro_ips: str = ""
    #: Доверять `X-Forwarded-For` (только за известным reverse-proxy).
    trust_forwarded_for: bool = False

    # Billing / Stripe (T-083)
    #: Секретный ключ Stripe (`sk_test_…`/`sk_live_…`). Пусто — billing
    #: выключен: checkout/portal отвечают 503, webhook — 503.
    stripe_secret_key: str = ""
    #: Секрет endpoint-а webhook (`whsec_…`) для проверки подписи
    #: `Stripe-Signature`. Без него webhook не принимается.
    stripe_webhook_secret: str = ""
    #: Price ID подписки Pro (`price_…`); сумма/период задаются в Stripe.
    stripe_price_id: str = ""

    @property
    def billing_configured(self) -> bool:
        """Можно ли создавать Checkout/Portal-сессии (ключ + цена)."""
        return bool(
            self.stripe_secret_key.strip() and self.stripe_price_id.strip()
        )

    @property
    def billing_webhook_configured(self) -> bool:
        """Можно ли проверять подпись Stripe webhook."""
        return bool(
            self.stripe_secret_key.strip()
            and self.stripe_webhook_secret.strip()
        )

    @property
    def lora_adapter_dir(self) -> Path | None:
        """Нормализованный путь к адаптеру или None, если путь не задан."""
        raw = (self.lora_adapter_path or "").strip()
        if not raw:
            return None
        normalized = raw.rstrip("/\\")
        return Path(normalized) if normalized else None

    @property
    def lora_adapter_available(self) -> bool:
        """Существует ли адаптер: файл или каталог с `adapter_config.json`.

        Проверка нарочно консервативная: каталог без `adapter_config.json` —
        это не готовый PEFT-адаптер (например, недокачанный или созданный до
        `save_pretrained`), и запрашивать его у vLLM нельзя.
        """
        adapter = self.lora_adapter_dir
        if adapter is None:
            return False
        if adapter.is_file():
            return True
        return adapter.is_dir() and (adapter / "adapter_config.json").is_file()

    @property
    def lora_model(self) -> str:
        """Имя модели для запроса: LoRA-adapter basename или базовая модель.

        Basename возвращается только если адаптер реально существует (файл
        или каталог с `adapter_config.json`): иначе vLLM ответил бы «model
        not found», а базовую модель по-прежнему можно использовать.
        """
        adapter = self.lora_adapter_dir
        if adapter is not None and self.lora_adapter_available:
            return adapter.name or self.vllm_model
        return self.vllm_model

    @property
    def local_llm_base_url(self) -> str:
        """Адрес локального LLM-бэкенда с учётом туннеля (T-123).

        Приоритет: ``LLM_LOCAL_BACKEND_URL`` → исторический ``VLLM_BASE_URL``.
        На облачном VPS первая переменная указывает на вход обратного
        SSH-туннеля (например, ``http://172.17.0.1:8034/v1``), через который
        backend видит LM Studio на домашней Windows-машине. На dev-машине
        обе настройки совпадают — ``http://127.0.0.1:8034/v1``.

        Хвостовой ``/`` убирается, чтобы `endpoint` не получал двойной слэш.
        """
        configured = (self.llm_local_backend_url or "").strip().rstrip("/")
        if configured:
            return configured
        return self.vllm_base_url.strip().rstrip("/")

    # Observability
    #: Retention `llm_calls` (KI-048): сколько последних дней хранить.
    #: Используется `scripts/prune_llm_calls.py` (по умолчанию 90).
    llm_calls_retention_days: int = 90
    otel_enabled: bool = False
    #: `console` — печать спанов в stdout; `otlp` — OTLP/HTTP экспортёр.
    otel_exporter: str = "console"
    otel_service_name: str = "chessannotator"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    #: OTLP-метрики (`/v1/metrics`): счётчики LLM для Grafana (T-109b).
    otel_metrics_enabled: bool = True
    #: Период экспорта метрик; короче — живее дашборд, больше сетевой шум.
    otel_metrics_interval_ms: int = 15_000

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