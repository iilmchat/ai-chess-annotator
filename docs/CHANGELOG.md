# Changelog

Все значимые изменения проекта ChessAnnotator.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **LoRA fine-tuning (T-104, итерация 25):** `scripts/train_lora.py` —
  реальный SFT на `peft`/`transformers` (QLoRA 4-bit, r=16,
  `lora_alpha=32`, `target_modules=["q_proj","v_proj"]`, 3 эпохи,
  lr=2e-4), маскирование лосса только на assistant-токенах,
  right-padding-коллатор, gradient checkpointing; `--dry-run`/`--check-env`
  работают без `torch`, реальный запуск без GPU/зависимостей падает с
  понятной инструкцией; `--system-prompt`, `--limit`, `--check-env`.
  Экспорт `scripts/export_dataset.py` — схема `system → user → assistant`
  (`system` из `app/prompts/<level>.txt`). `LORA_ADAPTER_PATH`
  учитывается в `Settings.lora_model`/`app/llm/factory.py` только для
  существующего адаптера (`adapter_config.json`), с логами
  `llm_lora_adapter_enabled` / `llm_lora_adapter_unavailable`.
  Инструкция по GPU/облаку, стоимости и vLLM —
  [LORA_TRAINING.md](development/reports/LORA_TRAINING.md). Реальное
  обучение не выполнялось (нет CUDA) — KI-030 Open, KI-039.
- **Playwright E2E (T-036b, итерация 25):** `@playwright/test` 1.64.1,
  `web/playwright.config.ts` (projects `chromium` + `mobile`, baseURL
  `http://127.0.0.1:5173`, webServer Vite, trace/screenshot/video при
  падении), 5 сценариев в `web/e2e/` (`auth`, `analyze`, `commentary`,
  `history`, `limits`) с page.route-моками API; скрипты
  `test:e2e`/`test:e2e:ui`; CI-job `e2e` с `upload-artifact` при
  падении; real-backend-режим `E2E_REAL_BACKEND=1` (локально 5 passed).
  Ограничение CI — KI-040.
- **Тесты (итерация 25):** `tests/unit/test_train_lora.py` (41),
  `test_dataset_export.py` (6), `test_lora_config.py` (13), фикстура
  `tests/fixtures/lora_sample.jsonl`; E2E 10 mocked-прогонов (5×2).
- **JWT-аутентификация и изоляция данных (T-041, KI-035 закрыт, итерация 24):**
  миграция `c7d8e9f0a1b2` — `users.role` (enum `admin_role`: user/admin/
  auditor), `is_active`, `password_hash`, `last_login_at`;
  `commentaries.user_id`, `edits.user_id`; индексы `ix_games_user_id_created`,
  `ix_users_role`, `ix_commentaries_user_id`, `ix_edits_user_id`.
  Пакет `app/auth/`: bcrypt-хеширование (passlib), access+refresh JWT
  (python-jose), зависимости `get_current_user` / `get_current_user_optional`
  / `require_admin`, проверки владельца партии/комментария.
- **`/auth` API:** `POST /auth/register` (201 профиль, 409 `email_taken`,
  422 на слабый пароль), `POST /auth/login` (200 access+refresh+профиль,
  401/403), `POST /auth/refresh` (ротация пары), `GET /auth/me`.
- **Изоляция данных:** `POST /v1/analyze` пишет `games.user_id`; `GET
  /v1/games` — только свои партии (admin — все, legacy NULL-owner — только
  admin); `GET /v1/games/{id}` — чужой id → 404; `POST /v1/commentary(/stream)`
  и `POST /v1/chat(/stream)` проверяют доступ к партии до вызова LLM и
  пишут `commentaries.user_id` (включая SSE); `PATCH /v1/commentaries/{id}`
  проверяет владельца и пишет `edits.user_id`.
- **Usage по ролям:** `GET /v1/users/me/usage` для JWT-пользователя —
  admin/auditor → `pro`, user → `free`, subject `user:{id}`; анонимно —
  прежний IP-путь (KI-031).
- **Фронтенд auth-flow:** страница `/login` (вход/регистрация, RU/EN),
  Zustand-стор `chess.auth` в `localStorage`, Bearer-заголовок и
  single-flight refresh на 401, `AuthGate` с редиректом на `/login`,
  профиль email + «Выйти» в шапке; SSE-запросы тоже авторизуются.
- **Feature-rate-limit на `/auth/login|register`** (10/мин на IP) —
  базовая защита от перебора паролей.
- **Тесты:** `tests/unit/test_auth.py` (24), `tests/integration/test_auth_api.py`
  (15, real auth), `tests/integration/test_data_isolation.py` (11),
  `tests/integration/test_usage_roles.py` (4); фронтенд — `authStore.test.ts`
  (7), `api.auth.test.ts` (9).

### Changed

- **LoRA-экспорт (итерация 25):** JSONL из `edits` теперь строго
  `system → user → assistant` (`system` — промпт уровня из
  `app/prompts/*.txt`); старые выгрузки без `system` требуют
  переэкспорта. `Settings.lora_model` возвращает basename адаптера только
  если путь существует (файл или каталог с `adapter_config.json`) —
  иначе базовая `VLLM_MODEL`, без «model not found» у vLLM.
- **CI (итерация 25):** новый job `e2e` (Playwright mocked, артефакты при
  падении); `vitest` исключает `web/e2e/**`, в `.gitignore` добавлены
  `web/test-results/`, `web/playwright-report/`, `web/blob-report/`,
  `models/`.
- **Защищённые эндпоинты:** `/v1/analyze`, `/v1/games*`, `/v1/commentary*`,
  `/v1/chat*` требуют `Authorization: Bearer <access>` (401 `unauthorized`
  без токена); `/v1/ingest` по-прежнему защищён `X-Admin-Token`.
- **Salvage из локальной ветки итераций 10–11:** `LMStudioEmbedder` —
  `EmbeddingError` (503 `embedding_unavailable`) для HTTP/таймаутов/сети/
  битого JSON/неверной размерности/вырожденных векторов, батчевый
  `embed_many` (32, порядок по `index`) и батчевый ingest;
  `scripts/ingest_pgn.py` — `--limit` по умолчанию **10** (защита от
  случайной массовой загрузки) и чистые exit-коды; дополнительные тесты
  `GET /v1/games/{id}` (глубокий parity POST↔GET, FEN-старт, 404,
  порядок комментариев) и детерминированный порядок мотивов.
- **Тестовая сюита:** тестовый seam `APP_ENV=test` + `TEST_AUTH_BYPASS=true`
  (dependency override `get_current_user` на тестового admin-пользователя;
  в dev/prod недостижим); `tests/conftest.py` включает его, реальная
  авторизация проверяется отдельными тестами.
- **Конфиг:** `passlib[bcrypt]` + `bcrypt<4.1` (KI-036), `python-jose[cryptography]`,
  `email-validator`, стабы `types-passlib`/`types-python-jose`; в
  `.env.example` — комментарии про JWT и test-seam.

- **История партий и лимиты (T-036/T-037, итерация 20):**
  `GET /v1/games`, `GET /v1/users/me/usage`, страницы `/history` и
  `/analysis/:id`, usage-badge с модалкой Pro, SPA-fallback.
- **История чата (T-037):** Zustand + `sessionStorage`, передача
  `history` в `/v1/chat` и `/v1/chat/stream`; user-реплики повторно
  проверяются guardrails.
- **Mobile, skeleton, Error Boundary (T-038, итерация 21):** адаптивность
  до 480 px, shimmer-лоадеры, fallback с кнопкой reload.
- **LLM Evaluations (T-039, итерация 22):** `llm_evaluations`, read-only
  view в админке, запись из `evaluate_llm.py`.
- **CI caching (T-040, итерация 23):** pip-кэш, pnpm store cache,
  `workflow_dispatch`, полный прогон.
- **Semantic Cache (T-032, итерация 19):** `semantic_cache` (vector
  768, HNSW), exact `fen_hash+level+prompt_hash` и semantic
  cosine < 0.05; `/v1/commentary`, `/chat` и SSE отдают кэш; TTL 30
  дней + чистка; `prompt_hash` включает sysprompt/модель/style/question.
- **LoRA-пайплайн (T-104/T-034, итерация 19):**
  `scripts/export_dataset.py` (JSONL из `edits`),
  `scripts/train_lora.py` (QLoRA-каркас, `--dry-run`),
  `LORA_ADAPTER_PATH` → basename adapter-модели для vLLM.
- **Prompt Injection Guardrails (T-033, итерация 18):**
  `app/security/guardrails.py` — NFKC-нормализация, удаление
  control/zero-width/bidi, лимит 2000, 7 правил (override/leak/role/
  jailbreak/role-tags, RU+EN); `inspect_user_input` → 422
  `prompt_injection` в `/v1/chat` и `/chat/stream`; запрет раскрывать
  системные инструкции в `CHAT_SYSTEM_PROMPT`.
- **Security Audit (T-035, итерация 18):** append-only `audit_logs`
  (`timestamp`, `actor`, `action`, `target_id`, `details`,
  `prev_hash`, `current_hash`), миграция `f4a5b6c7d8e9`, hash-chain,
  advisory-lock и триггер `UPDATE/DELETE/TRUNCATE`; аудит правки промпта,
  `PATCH /v1/commentaries/{id}` и заблокированных инъекций; read-only
  view и страница «Проверить целостность».
- **Redis Pub/Sub кэша промптов (KI-032):** канал `prompts_updates`,
  watcher в lifespan, публикация после commit в админке.
- **Prompt Management (T-029, итерация 17):** таблица `prompts`
  (`level`, `content`, `version`, `updated_at`), миграция
  `e3f4a5b6c7d8`, seed из `app/prompts/*.txt` при старте, async
  `load_system_prompt` с in-memory кэшем, `reset_prompt_cache`, горячая
  инвалидация после правки и редактируемый view `PromptAdmin` (версия
  инкрементируется, `content` меняется без рестарта).
- **FinOps / token budget (T-031, итерация 17):** дневные Redis-счётчики
  `usage:{date}:{ip|user}`, `TokenBudgetMiddleware` для
  analyze/commentary/chat (+stream), `429 token_budget_exceeded` с
  `X-RateLimit-Reset`/`Retry-After`, лимиты
  `DAILY_TOKEN_LIMIT_FREE/PRO`, `cost_usd` по ценам из конфига в
  `commentaries` и OTel-спанах.
- **Admin Panel (T-028, частично):** `app/admin.py` — `/admin` на
  `starlette-admin` 1.0 (тот же `AsyncEngine`), защита `X-Admin-Token`
  (тот же `ADMIN_API_KEY`) или cookie-сессия формы входа
  (`ADMIN_USERNAME`/`ADMIN_PASSWORD`), 401 без учётных данных и 403 на
  неверный токен. Read-only views `annotated_games`, `commentaries`,
  `edits`; `tzdata` для конвертации времени на Windows.
- **OTel-спаны LLM (T-030, частично):** `llm_generate` и `llm_stream` в
  `OpenAICompatibleClient` с атрибутами `llm.model`, `llm.level`,
  `llm.tokens_in`, `llm.tokens_out`, `llm.cost_usd`, `llm.status`;
  экспорт `console`/`otlp` при `OTEL_ENABLED=true`.
- **Стандарт отчётов (RULES §2.18):** отчёт о фазе — строго 7 разделов;
  файл `docs/development/reports/v0.1.0/ITERATION-16.md`.
- **Синхронизация UI (итерация 15):** `react-chessboard` 5.12.1
  (`<Chessboard options={{position}} />`), `chess.js`
  `history({verbose:true})` для FEN из PGN, `react-i18next` (RU/EN),
  Zustand-стор `analysisStore.ts` с persist в `sessionStorage`.
- **SSE-стриминг (T-032):** `OpenAICompatibleClient.stream_generate`,
  `POST /v1/commentary/stream` и `POST /v1/chat/stream`
  (`sse-starlette`), события `meta`/`delta`/`done`/`error`, финальная
  SAN-валидация; фронтенд читает `ReadableStream` и печатает текст
  постепенно.
- **RAG 768:** `LMStudioEmbedder` (`/v1/embeddings`,
  `text-embedding-nomic-embed-text-v1.5`, L2), миграция `vector(768)`,
  `scripts/reembed_annotated.py`; hash-эмбеддер — fallback тестов.
- **`GET /v1/games/{id}`:** сохранённый анализ (`moves`,
  `analysis_level`, `commentaries`) без повторного Stockfish.
- **`X-Admin-Token`:** constant-time защита `POST /v1/ingest`
  (`ADMIN_API_KEY`).

### Changed

- **KI-033 закрыт:** аудит правки промпта пишется в `PromptAdmin.after_edit`
  в той же транзакции, что и `prompts`; сбой аудита откатывает правку.
- **Semantic cache выключен в eval/тестах по умолчанию:** иначе повторные
  прогоны не проверяют генерацию; включается `SEMANTIC_CACHE_ENABLED=true`.
- **KI-031 закрыт:** тир `free/pro` больше не берётся из
  `X-User-Tier`/`X-User-Id`; `pro` — только IP из `PRO_IPS`,
  `X-Forwarded-For` — только при `TRUST_FORWARDED_FOR=true`.
- **KI-032 закрыт:** кэш промптов инвалидируется между воркерами через
  Redis Pub/Sub; локальный reset остаётся fallback'ом.
- **Промпты из БД:** `load_system_prompt` стал async (читает `prompts`
  при промахе кэша и падает обратно на `.txt` при недоступной БД);
  вызовы в commentary service/streaming обновлены.
- **KI-021 закрыт:** `LLMUsage.cost_usd`, `commentaries.cost_usd` и
  OTel-атрибут `llm.cost_usd` считаются по `LLM_PRICE_*`/
  `LLM_LOCAL_PRICE_*`.
- **KI-030 / `club`:** промпт `club.txt` сокращён и получил few-shot
  примеры **без SAN**; пакет фактов для `club` — только топ-3 момента без
  PV (было до 10 с PV); заголовки моментов однозначно называют сторону и
  номер. LLM-as-judge (полный пакет): **4.40/10** против 3.83, 5/5 без
  `502`, prompt-токены ≈ 3.4k → 1.9k. Конкретные few-shot (V1) оказались
  вредны — 4B-модель копировала ходы из примеров (3.00/10). Валидатор
  больше не считает одиночную клетку (`e1`) ходом.
- `games` получила `analysis_level` и `analysis_time_ms`; `AnalyzeResponse`
  отдаёт `analysis_level`.

## [0.1.0] — 2026-10-28

### Added

- **Web UI (T-019…T-022):** React 19 + Vite SPA: вставка PGN, выбор уровня,
  доска из FEN, список ходов с оценками и классами, карточка хода с
  мотивами/рекомендацией, панель комментария с inline-редактором и PATCH,
  чат по партии. 14 Vitest-тестов на FEN и форматирование.
- **Production-раздача SPA (T-025):** Vite-сборка `web/dist` отдаётся
  FastAPI `StaticFiles`; multi-stage Dockerfile (Node → Python), non-root
  пользователь, Stockfish в образе; `docker-compose.prod.yml` с loopback-
  портами БД/Redis и healthcheck.
- **LLM-as-judge (T-023):** `scripts/evaluate_llm.py` — 5 партий ×
  `club`/`gm`, оценки DeepSeek по трём шкалам, отчёт
  `docs/development/reports/LLM_EVAL.md`. Средняя **5.19/10** (9 из 10),
  ниже порога 7/10 — заведён KI-030 (план улучшения промптов); часть
  `club`-генераций не укладывается в production-таймаут (KI-024).
- **Документация MVP (T-026):** `USER_GUIDE.md`, `ASSISTANT_LEVELS.md`,
  `ARCHITECTURE.md`, `MANUAL_TESTING_GUIDE.md`, README и RELEASES под
  v0.1.0.
- **API для доски:** `AnalyzeResponse.initial_fen` и `fen` у каждого хода —
  чтобы клиент рисовал позицию без собственного шахматного движка.
- **Production hardening plan (T-028…T-035, документация):** в `TODO.md`
  добавлена Фаза 7 — admin control plane, prompt management, LLMOps,
  FinOps, semantic cache, prompt-injection guardrails, dataset export и
  security audit; расширен Backlog (T-111…T-119). Создан
  `docs/development/DESIGN_ADMIN_AND_OPS.md` (схема БД, админка,
  трейсинг, безопасность), `SPEC.md` §10 дополнен Admin Control Plane /
  Observability / FinOps. Код и миграции не менялись.
- **`PATCH /v1/commentaries/{id}` (T-021b):** ручная правка текста
  комментария — `commentaries.text` обновляется, `human_edited = true`,
  пара «оригинал → правка» пишется в `edits` (датасет для T-104);
  ответ содержит `edit_id`; 404 на неизвестный id, 422 на пустой текст.
- **`POST /v1/chat` (T-022):** диалог по `analysis_id` — метаданные,
  ключевые ходы, мотивы и оценки из БД → LLM по уровню → валидатор SAN с
  retry → `{"reply", "tokens_used"}`; rate limiting, 404/422/502/503/504.
- **`scripts/ingest_pgn.py`:** CLI для загрузки PGN в `annotated_games`
  (`--file`, `--limit`, `--source`), UTF-8/cp1251, общий с
  `POST /v1/ingest` пайплайн; `ingest_pgn_text(..., limit=...)`.
- **Тесты:** `tests/integration/test_api_commentaries_patch.py` (4),
  `tests/integration/test_api_chat.py` (9, включая живой диалог с LM Studio).
- **RAG-коллекция (итерация 12):** в `annotated_games` загружено 102
  партии из `data/` (`my-collection`: 10 + 42 + 50).
- **RAG (T-017):** `app/rag/` — хеш-эмбеддер (1024, по FEN), ingest
  аннотированных партий (`parse_pgn_games`, `ingest_pgn_text`), поиск
  похожих позиций (`find_similar_positions`, косинус через pgvector),
  RAG-контекст для уровня `gm` в `/v1/commentary`.
- **`POST /v1/ingest`:** загрузка PGN (multipart, до 5 МБ, UTF-8/cp1251),
  парсинг нескольких партий, сохранение аннотаций и эмбеддингов.
- **Тесты:** `tests/unit/test_rag.py` (6), `tests/integration/test_rag.py`
  (9: ingest, retrieval, `/v1/ingest`, RAG в промпте gm, не в club).
- **Комментарий (T-016):** `POST /v1/commentary` — по `analysis_id` берёт
  партию и анализ из БД, собирает пакет фактов, генерирует текст, проверяет
  ходы валидатором (до 2 retry), сохраняет в `commentaries` с токенами и
  `validated`; при нелегальных ходах после retry — `502 llm_invalid_output`.
- **Сервис генерации:** `app/commentary/service.py` — восстановление
  `MoveAnalysis` из `moves`/`motifs`/`multipv`, ретраи, стиль,
  реконструкция UCI/SAN.
- **Ошибки:** `NotFoundError` (404), `LLMValidationError` (502).
- **Системные промпты (T-014):** `app/prompts/{beginner,club,gm}.txt` —
  строгие правила (только факты из пакета, SAN без Markdown, фиксированная
  длина), загрузчик `load_system_prompt(level)` с кешем.
- **Пакет фактов:** `build_fact_package(game, analyses, level)` —
  метаданные, ходы партии, до 10 ключевых моментов с оценкой, классом,
  лучшим ходом, рекомендацией Policy, PV в SAN и мотивами.
- **Прогрев локальной LLM (KI-022 закрыт):** фоновая задача в lifespan
  отправляет короткий запрос в LM Studio при старте; ошибки не ломают
  старт, задача отменяется при остановке.
- **Валидатор ответов LLM (T-015):** `app/engine/validator.py` —
  `extract_moves_from_text` (SAN-регэксп), `validate_moves` (проигрывание
  на доске + проверка альтернатив по позициям партии),
  `validate_llm_response`.
- **Тесты:** `tests/unit/test_prompts.py` (11),
  `tests/unit/test_validator.py` (14), `tests/unit/test_warmup.py` (3),
  живой E2E `tests/integration/test_generation.py` (1).
- **``/v1/analyze``: разбор по ходам (T-012).** В ответ добавлен `moves`:
  `ply`, `san`, `uci`, `eval_cp`, `mate`, `best_move_san`, `delta_cp`,
  `classification`, `motifs` и `recommended_move` (san/uci/eval_cp/mate) —
  рекомендация Policy Layer под запрошенный уровень.
- **LLM-адаптер (T-013):** пакет `app/llm/` — `BaseLLMClient`
  (`generate`/`complete`/`aclose`), `OpenAICompatibleClient` для
  OpenAI-совместимых API (LM Studio, DeepSeek), фабрика
  `get_llm_client(level)`: `beginner`/`club` → LM Studio,
  `gm` → DeepSeek; таймауты, ретраи на 429/5xx с `Retry-After`, учёт
  токенов (`LLMUsage`).
- **Ошибки LLM:** `LLMError`, `LLMNotConfiguredError`, `LLMTimeoutError`
  (503/503/504).
- **Тесты:** `tests/unit/test_llm.py` (13, моки `httpx.MockTransport`),
  `tests/integration/test_llm_live.py` (2 живых, со skip), +4 теста policy
  (KI-020 и различие рекомендаций beginner/gm).
- **Motif Detector (T-010):** пакет `app/motifs/` — `detect_motifs(fen, multipv)`
  находит проходные пешки, слабые поля у короля, открытые линии и диагонали,
  проблемы безопасности короля, вилки/связки по доске, мат и выигрыш
  материала по лучшей линии движка (дедупликация, лимит 6 мотивов).
- **Мотивы в БД:** `MoveAnalysis.motifs` → таблица `motifs` (type,
  description, importance, move_id) в транзакции анализа.
- **Policy Layer (T-011):** `app/engine/policy.py` — `choose_recommended_move`
  для трёх уровней: `gm` (топ-1), `club` (альтернатива при разнице < 20 cp),
  `beginner` (отбрасывание раннего ферзя и «зевков», приоритет развитию,
  центру и рокировке).
- **PV-линии в БД (KI-018 закрыт):** `MoveAnalysis.lines` → `moves.multipv`
  (JSONB, до 3 линий на уровне `club`); отдельная миграция не потребовалась —
  колонка была создана в T-004.
- **Тесты:** `tests/unit/test_motifs.py` (13), `tests/unit/test_policy.py` (15).
- **``POST /v1/analyze`` (T-009):** PGN + `level` → структурированный анализ
  (moves, evals, классификация) с сохранением в `games`/`moves` и
  `analysis_id` в ответе; партии с FEN-стартом поддерживаются через
  PGN-заголовки `[SetUp]`/`[FEN]`.
- **`EnginePool` (T-009, KI-014 закрыт):** 4 параллельных процесса
  Stockfish (`ENGINE_POOL_SIZE`, до 8), очередь свободных, порядок
  результатов сохраняется; SLA на 53 полухода — 14.6 с (лимит 30 с).
- **Глобальный обработчик доменных ошибок:** `AppError` →
  `{"detail", "code"}` с кодами 400/422/500/504; ошибки движка отдают
  `Engine unavailable`.
- **Rate limiting:** Redis `INCR`+`EXPIRE`, 10 запросов/мин на IP для
  `/v1/analyze`, `Retry-After`/`X-RateLimit-*`, fail-open при недоступном
  Redis (KI-015).
- **Lifespan приложения:** пул движков и Redis-клиент создаются/закрываются
  с приложением; `create_app(settings)` для тестовой конфигурации.
- **Тесты:** `tests/unit/test_engine_pool.py` (7),
  `tests/unit/test_rate_limit.py` (7),
  `tests/integration/test_api_analyze.py` (4, включая SLA и проверку БД).

- **Engine Service (T-008):** пакет `app/engine/` — async-обёртка над
  Stockfish: `EngineService.analyze()` (MultiPV, параметры по уровню из
  SPEC §2.2), `classify_move()` (best/good/inaccuracy/mistake/blunder),
  `analyze_game_moves()` (оценка «до/после» для каждого полухода,
  `delta_cp`, SAN/UCI, мат как `±(10000 − 100·N)`); собственный
  асинхронный UCI-клиент с таймаутами и перезапуском процесса.
- **Ошибки движка:** `EngineError`, `EngineNotFoundError`,
  `EngineTimeoutError`, `InvalidLevelError` в `app/errors.py`.
- **`ParsedGame.initial_fen`:** стартовая позиция партии (для партий с
  FEN-стартом); используется движком при анализе истории ходов.
- **Тесты движка:** `tests/unit/test_engine.py` — 27 тестов, включая
  интеграцию с реальным Stockfish (MultiPV, бенчмарк «клуба», первые
  3 хода партии, легальность лучшего хода).
- **Ingestion (T-007):** пакет `app/ingestion/` — `parse_pgn()` (PGN →
  `ParsedGame`), `parse_fen()` (валидация FEN), `find_position_repeats()`
  (повторы позиций); ECO-код из заголовка `[ECO]` с fallback на
  курируемую таблицу дебютных префиксов; разметка фаз
  (дебют ≤ 30 полуходов, эндшпиль по материалу `< 2000 cp` или `< 10`
  фигур).
- **Доменные ошибки ingestion:** `AppError`, `InvalidPgnError`,
  `InvalidFenError` (`code`, `http_status` для будущего API-слоя).
- **Модели результата ingestion:** `ParsedMove`, `GameMetadata`,
  `PhaseBreakdown`, `PositionRepeat`, `ParsedGame` (Pydantic v2, `frozen`,
  `extra="forbid"`).
- **Тесты ingestion:** `tests/unit/test_ingestion.py` — 33 теста
  (10 входных PGN, FEN после каждого полухода, флаги взятия/шаха, фазы,
  ошибки, ECO, повторы), `xfail`-маркер KI-012.
- **Фикстура:** `tests/fixtures/test_game.pgn` (Test White / Test Black)
  и таблица инвариантов в `tests/fixtures/README.md`.
- **Bootstrap проекта (T-001…T-006):** FastAPI-скелет (`app/main.py`,
  `app/config.py`), healthcheck `/v1/health` с проверкой БД, Redis,
  Stockfish и LLM, Docker Compose (PostgreSQL 17 + pgvector, Redis,
  опциональные Stockfish и vLLM), CI (ruff, mypy, alembic, pytest,
  typecheck, build), фронтенд-скелет (React 19 + Vite).
- **Схема БД (T-004):** 8 таблиц по SPEC.md §4 — `users`, `games`,
  `moves`, `motifs`, `annotated_games`, `retrieval_cache`,
  `commentaries`, `edits`; нативный enum `level`, `VECTOR(1024)` с
  ivfflat-индексом, индексы `moves(game_id, ply)` и
  `retrieval_cache(fen_hash)`.
- **Первая миграция** `b802598c3778_init_schema` (upgrade / downgrade /
  повторный upgrade проверены), `alembic check` без дрейфа.
- **Тесты схемы (T-004/T-005):** `tests/test_health.py`,
  `tests/test_models.py` — 5 тестов, включая интеграционный тест схемы
  (defaults, каскады).
- **Документация:** `docs/development/RULES.md` (правила кода, тестов,
  коммитов, безопасности, окружения), `PROMPT_CURRENT.md`,
  `RELEASES.md`, `docs/CHANGELOG.md`, `docs/KNOWN_ISSUES.md`,
  `docs/README.md` (навигация), пошаговые отчёты в
  `docs/development/reports/`.
- **CI:** шаги `alembic check` и roundtrip-миграции
  (`downgrade base` → `upgrade head`) — DoD T-004.

### Changed

- `ruff`: `RUF001` добавлено в `ignore` вместе с `RUF002/RUF003` —
  сообщения и докстринги проекта на русском.
- Комментарий к `games.phase_breakdown` в `app/db/models.py` приведён к
  фактическому формату `{"opening_end_ply": …, "endgame_start_ply": …}`.
- `POSTGRES_HOST` / `REDIS_URL` по умолчанию — `127.0.0.1` вместо
  `localhost`: на Windows `localhost` резолвится в `::1`, а compose
  публикует порты только на IPv4-loopback (иначе healthcheck Redis
  падал по таймауту).
- HTTP-порт Redis в compose сделан переопределяемым: `REDIS_PORT`
  (по умолчанию 6380).
- `/v1/health` возвращает `degraded`, если хоть один компонент не `ok`
  (в том числе `skipped` для ненастроенной LLM) — как требует контракт.
- `pyproject.toml`: добавлен `pgvector`; убран `types-redis` (redis 5.x
  поставляет `py.typed`); отключены `RUF002/RUF003` (русские докстринги);
  добавлен `asyncio_default_fixture_loop_scope`.
- `web/pnpm-workspace.yaml`: `allowBuilds: esbuild` — pnpm 12 не выполняет
  postinstall-скрипты зависимостей без явного разрешения.

### Changed

- Локальный LLM-провайдер — **LM Studio** (`http://127.0.0.1:8034/v1`,
  модель `qwen/qwen3-4b-2507`, ключ-заглушка `lm-studio`); облачный —
  **DeepSeek** (`deepseek-chat`). Новые настройки: `vllm_api_key`,
  `deepseek_base_url`, `deepseek_model`, `llm_max_tokens`.
- Policy новичка: ход с `mate > 0` разрешён всегда, даже если это жертва (KI-020 закрыт).
- Ошибки движка в HTTP теперь отдают **500** (`EngineError`,
  `EngineNotFoundError`) и **504** (`EngineTimeoutError`) вместо 503 —
  как требует контракт T-009; текст для пользователя — `Engine unavailable`.
- `create_app()` принимает `Settings`, а создание пула движков и
  Redis-клиента перенесено в lifespan (корректное закрытие ресурсов).

### Fixed

- **KI-029:** `password authentication failed for user "chess"` — старый том
  PostgreSQL сохранял прежний пароль. Полный сброс `docker compose down -v`
  + `up -d postgres redis` + `alembic upgrade head`; после этого ingest
  загрузил 102 партии без ошибок авторизации.
- Валидатор учитывает позиции PV-линий (xtra_fens): цитаты вариантов из пакета фактов больше не считаются нелегальными ходами; /v1/commentary передаёт их автоматически.
- Температура LLM вынесена в настройку llm_temperature (в тестовой генерации — 0.0).
- Пул соединений asyncpg закрывается в lifespan (ngine.dispose()): без этого несколько TestClient в одном процессе переиспользовали соединения из закрытого event loop и получали 500.
- Alembic-миграция доведена руками: `import pgvector`,
  `CREATE EXTENSION vector`, явные `create/drop` нативного enum `level`
  (иначе `downgrade base` + `upgrade` падали на существующем типе).
- `mypy`: конфликт имени `text` (колонка `commentaries.text` против
  `sqlalchemy.text`), устаревшие стабы `types-redis`.
- `ruff`: неиспользуемый импорт `Field`, `AsyncIterator` из `typing`,
  бесполезные `noqa`.
