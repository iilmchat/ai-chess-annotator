# GETTING STARTED — развернуть ChessAnnotator за ~20 минут

> Цель этого документа — довести вас от `git clone` до работающего
> `curl 127.0.0.1:8000/v1/health` с зелёными `db`, `redis`, `stockfish`.
> LLM на этом этапе **не требуется**: уровень «новичок» заработает после
> T-013, а healthcheck покажет `llm: skipped` — это нормально.

## Оглавление

1. [Требования](#1-требования)
2. [Быстрый старт (5 команд)](#2-быстрый-старт-5-команд)
3. [Пошаговое разворачивание](#3-пошаговое-разворачивание)
4. [Проверка работоспособности](#4-проверка-работоспособности)
5. [Настройка LLM-провайдеров](#5-настройка-llm-провайдеров)
6. [Опциональные сервисы](#6-опциональные-сервисы)
7. [Production-развёртывание (Docker)](#7-production-развёртывание-docker)
8. [Troubleshooting](#8-troubleshooting)
9. [Обслуживание: retention и FinOps](#9-обслуживание-retention-и-finops)
10. [Что дальше](#10-что-дальше)

---

## 1. Требования

| Компонент | Версия | Зачем |
|---|---|---|
| **Python** | 3.12+ | Backend |
| **Node.js** | 22+ | Frontend |
| **pnpm** | 12+ | Frontend-пакетный менеджер |
| **Docker Desktop** | 27+ | PostgreSQL, Redis, Stockfish (опционально), vLLM (опционально) |
| **Stockfish** | 17+ | Движок анализа (локальный бинарник или Docker-сервис) |
| **git** | 2.40+ | Версионирование |

**Опционально:**

- **NVIDIA GPU** + драйверы + `nvidia-container-toolkit` — для локальной
  LLM через vLLM (уровень «новичок»/«клуб» без облака).
- **API-ключи** OpenAI / Anthropic / DeepSeek — для облачных LLM (уровень
  «гроссмейстер» и fallback).

### 1.1. Установка Stockfish

**Ubuntu / Debian:**

```bash
sudo apt-get update
sudo apt-get install -y stockfish
which stockfish   # → /usr/games/stockfish или /usr/bin/stockfish
stockfish --help | head -n 3
```

**macOS:**

```bash
brew install stockfish
which stockfish   # → /opt/homebrew/bin/stockfish
```

**Windows:**

Скачать с [stockfishchess.org](https://stockfishchess.org/download/) и
добавить в `PATH`. Проверить: `stockfish.exe --help`.

Если не хочется ставить локально — используйте Docker-сервис `stockfish`
(см. §6.1). Тогда в `.env` укажите `STOCKFISH_HOST=localhost` и
`STOCKFISH_PORT=5000`.

### 1.2. Установка pnpm

```bash
corepack enable
corepack prepare pnpm@latest --activate
pnpm --version   # → 12.x
```

---

## 2. Быстрый старт (5 команд)

Если всё из §1 уже установлено:

```bash
git clone https://github.com/iilmchat/chess-annotator.git
cd chess-annotator
docker compose up -d postgres redis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && cp .env.example .env && alembic upgrade head
```

Дальше — два терминала:

```bash
# Терминал 1
uvicorn app.main:app --reload

# Терминал 2
curl -s http://127.0.0.1:8000/v1/health | python -m json.tool
```

Ожидаемый ответ — см. §4.

---

## 3. Пошаговое разворачивание

### 3.1. Клонировать репозиторий

```bash
git clone https://github.com/iilmchat/chess-annotator.git
cd chess-annotator
```

Проверить, что вы на ветке `main` и она зелёная:

```bash
git status
git log --oneline -5
```

### 3.2. Поднять PostgreSQL 17 + pgvector и Redis

```bash
docker compose up -d postgres redis
docker compose ps
```

Ожидаемо:

```
NAME                SERVICE    STATUS    PORTS
chess-annotator-postgres-1   postgres   running (healthy)   127.0.0.1:5434->5432/tcp
chess-annotator-redis-1      redis      running (healthy)   127.0.0.1:6380->6379/tcp
```

Если статус `starting` — подождите 5–10 секунд и повторите
`docker compose ps`. Если `unhealthy` — см. §8.

Порты `5434` и `6380` (а не стандартные `5432`/`6379`) выбраны, чтобы не
конфликтовать с локальными Postgres/Redis, если они у вас уже есть.

### 3.3. Создать виртуальное окружение и установить зависимости

**Linux / macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e ".[dev]"
```

Проверка:

```bash
python --version          # → Python 3.12.x
pip list | grep -E "fastapi|chess|stockfish|sqlalchemy"
```

### 3.4. Настроить `.env`

```bash
cp .env.example .env
```

Откройте `.env` и проверьте:

- `POSTGRES_*` — совпадают с `docker-compose.yml` (по умолчанию — да).
- `REDIS_URL` — `redis://localhost:6380/0` (по умолчанию — да).
- `STOCKFISH_PATH` — путь к локальному бинарнику
  (`/usr/games/stockfish` на Ubuntu, `/opt/homebrew/bin/stockfish` на
  macOS, `C:\...\stockfish.exe` на Windows). Если используете
  Docker-сервис — раскомментируйте `STOCKFISH_HOST`/`STOCKFISH_PORT` и
  закомментируйте `STOCKFISH_PATH`.
- `JWT_SECRET` — **обязательно** замените на случайную строку ≥ 32
  символов. Пример: `openssl rand -hex 32`.
- `CORS_ORIGINS` — явный список origin-ов через запятую; `*` запрещён
  (приложение отправляет `allow_credentials=True`, T-110).
- `DEFAULT_LANGUAGE` — язык комментариев по умолчанию: `ru` или `en`
  (T-106). Запрос `/v1/commentary` может переопределить его полем
  `language`, иначе учитывается `Accept-Language`.
- `LLM_*` — пока можно оставить пустыми (LLM не нужна для healthcheck).
- `ENGINE_CACHE_*` — кэш результатов Stockfish (T-108): включён по
  умолчанию, TTL 30 дней, чистка при старте приложения.
- `PDF_FONT_PATH` — нужен только если в системе нет Arial/DejaVuSans
  (PDF-экспорт комментария подбирает их автоматически).
- `APP_BASE_URL` — публичный URL приложения (SPA) для ссылок в письмах:
  `/verify-email` и `/reset-password` (в dev — `http://localhost:5173`,
  в prod — тот же origin, что API). `EMAIL_SENDER=mock` логирует ссылку,
  SMTP — отдельный шаг (KI-056).
- `ARQ_QUEUE_NAME`/`EXPORT_JOB_STALE_MINUTES` — очередь фоновых
  выгрузок и порог recovery (KI-053).
- `EXPORT_RETENTION_DAYS` — сколько дней хранить `export_jobs` и готовые
  JSONL в `EXPORT_DIR` (по умолчанию 7; KI-054, cron — §9.3).

### 3.5. Применить миграции

```bash
alembic upgrade head
```

Проверка:

```bash
alembic current              # → <revision> (head)
docker compose exec postgres psql -U chess -d chessannotator -c "\dt"
```

Должны быть таблицы: `users`, `games`, `moves`, `motifs`,
`annotated_games`, `retrieval_cache`, `commentaries`, `edits`,
`alembic_version`.

### 3.6. Запустить backend

```bash
uvicorn app.main:app --reload
```

Ожидаемо:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [...]
INFO:     Application startup complete.
```

Откройте в браузере:

- **Swagger UI:** http://127.0.0.1:8000/docs
- **OpenAPI JSON:** http://127.0.0.1:8000/openapi.json
- **Healthcheck:** http://127.0.0.1:8000/v1/health

### 3.7. Запустить frontend (опционально)

В отдельном терминале:

```bash
pnpm --dir web install
pnpm --dir web dev
```

Откройте http://127.0.0.1:5173 — увидите заглушку «ChessAnnotator.
Скелет фронтенда» (полный UI — в T-019…T-022).

Vite проксирует `/v1/*`, `/auth/*`, `/chat/*`, `/runs/*` на
`http://127.0.0.1:8000`, поэтому backend должен быть запущен первым.

---

## 4. Проверка работоспособности

### 4.1. Healthcheck

```bash
curl -s http://127.0.0.1:8000/v1/health | python -m json.tool
```

**Ожидаемый результат:**

```json
{
  "status": "degraded",
  "components": {
    "db": {"status": "ok", "detail": null},
    "redis": {"status": "ok", "detail": null},
    "stockfish": {"status": "ok", "detail": null},
    "llm": {"status": "skipped", "detail": "local vllm configured"}
  }
}
```

**Что означает каждое поле:**

| Поле | `ok` | `error` | `skipped` |
|---|---|---|---|
| `db` | Postgres отвечает на `SELECT 1` | Проверьте `docker compose ps postgres`, `POSTGRES_*` в `.env` | — |
| `redis` | Redis отвечает на `PING` | Проверьте `docker compose ps redis`, `REDIS_URL` в `.env` | — |
| `stockfish` | Бинарник найден в `PATH` / по `STOCKFISH_PATH` | Установите Stockfish (§1.1) или настройте `STOCKFISH_HOST` | — |
| `llm` | Есть облачный ключ (`OPENAI_API_KEY` и т.д.) | — | Локальная LLM настроена, но не проверялась |

**`status: "degraded"` — это нормально** на старте: LLM-провайдер ещё не
выбран, но `db`, `redis`, `stockfish` = `ok`. Как только вы настроите
LLM (§5) — `status` станет `ok`.

### 4.2. Тесты

```bash
ruff check .
mypy app
pytest -q
```

Ожидаемо: `ruff` — 0 ошибок, `mypy` — `Success: no issues found`,
`pytest` — `N passed`.

### 4.3. Frontend

```bash
pnpm --dir web typecheck
pnpm --dir web build
```

Ожидаемо: обе команды без ошибок, `web/dist/` создан.

---

## 5. Настройка LLM-провайдеров

LLM нужна для генерации комментария (`/v1/commentary`) и чата
(`/v1/chat`). Для healthcheck и `/v1/analyze` она не требуется.

### 5.1. Вариант A — облачная LLM (быстро, но партии уходят наружу)

Подходит для уровня «гроссмейстер» и fallback.

```dotenv
# .env
OPENAI_API_KEY=sk-...
# или
ANTHROPIC_API_KEY=sk-ant-...
# или
DEEPSEEK_API_KEY=sk-...
```

После изменения `.env` — перезапустить uvicorn. Healthcheck покажет
`llm: ok`.

**Важно:** при уровне «новичок» и `privacy=local` партии **не** уходят в
облако (см. `RULES.md` §7.3). Облачная LLM используется только для
уровня «клуб» (опционально) и «гроссмейстер» (или при явном opt-in).

### 5.2. Вариант B — локальная LLM через vLLM (приватно, нужен GPU)

Подходит для уровня «новичок» и «клуб» без облака.

```bash
docker compose --profile llm-local up -d vllm
```

Первый запуск скачает модель (`Qwen/Qwen3-8B` по умолчанию, ~16 GB).
Дождитесь `vllm` в статусе `healthy` (может занять 5–10 минут на первом
запуске).

В `.env`:

```dotenv
VLLM_BASE_URL=http://localhost:8001/v1
VLLM_MODEL=Qwen/Qwen3-8B
```

Перезапустить uvicorn. Healthcheck покажет `llm: skipped` (локальная LLM
настроена, но не проверялась) — это нормально.

**Требования к GPU:** ≥ 24 GB VRAM для Qwen3-8B (bf16). Для Qwen3-1.7B
(Gemma-3-4B) достаточно 8–12 GB.

### 5.3. Вариант C — гибрид (рекомендуется для prod)

- **Новичок** → локальная LLM (приватно, дёшево).
- **Клуб** → локальная LLM или облачная mini (по настройке).
- **Гроссмейстер** → облачная мощная LLM + RAG.

Настраивается в `app/config.py` (маппинг `level → provider`) — появится в
T-013. Пока — просто заполните те ключи, которые планируете использовать.

---

## 6. Опциональные сервисы

### 6.1. Stockfish как Docker-сервис (UCI-over-TCP)

Если не хотите ставить Stockfish локально:

```bash
docker compose --profile engine up -d stockfish
```

В `.env`:

```dotenv
# STOCKFISH_PATH=/usr/bin/stockfish   # закомментировать
STOCKFISH_HOST=localhost
STOCKFISH_PORT=5000
```

Перезапустить uvicorn. Healthcheck покажет `stockfish: ok (tcp-mode)`.

**Важно:** TCP-режим не проверяется healthcheck'ом (только констатирует,
что `STOCKFISH_HOST` задан). Реальная проверка — при первом запросе
`/v1/analyze`.

### 6.2. Observability-стек: Grafana + Tempo + Prometheus (T-109b)

Полный стек (коллектор, Tempo, Prometheus, Grafana) поднимается отдельным
compose-файлом и не конфликтует с основным:

```bash
docker compose -f docker-compose.observability.yml up -d
docker compose -f docker-compose.observability.yml ps
```

Ожидаемо: `grafana`, `otel-collector`, `prometheus`, `tempo` — `healthy`.
Адреса (только loopback):

| Сервис | URL / порт |
| :--- | :--- |
| Grafana | http://localhost:3000 (`admin` / `admin`, переопределяется `GRAFANA_ADMIN_PASSWORD`) |
| Prometheus | http://localhost:9090 |
| Tempo | http://localhost:3200 (API; трейсы — Grafana → Explore → Tempo) |
| OTel Collector | `localhost:4317` (gRPC), `localhost:4318` (HTTP) |

В `.env` приложения:

```dotenv
OTEL_ENABLED=true
OTEL_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_METRICS_ENABLED=true
OTEL_METRICS_INTERVAL_MS=15000
```

Перезапустите uvicorn. Спаны и метрики уйдут в коллектор: трейсы — в
Tempo, метрики — в Prometheus. Дашборд
**ChessAnnotator — Observability** появляется в Grafana автоматически
(папка `ChessAnnotator`): p95/p99 для `/v1/commentary` и `/v1/analyze`,
error rate 5xx, стоимость и токены LLM, латентность по моделям. Если
приложение запущено в контейнере в сети observability-стека, endpoint —
`http://otel-collector:4318`.

**Алерты (T-113).** `deploy/grafana/provisioning/alerting/rules.yaml`
провижинит три правила в Grafana → Alerting → Alert rules:
`HighErrorRate` (5xx > 5% за 5 мин), `HighLatency` (p95
`/v1/commentary` > 10 с за 5 мин) и `LLMBudgetExceeded` (стоимость LLM
за 24 ч > 5 USD). Отдельный Alertmanager не требуется — правила
оцениваются unified alerting Grafana; уведомления добавляются contact
point'ом. Проверка через API:

```bash
curl -s -u admin:admin http://127.0.0.1:3000/api/v1/provisioning/alert-rules
```

**Диаграммы доски в PDF** требуют нативной `libcairo2` (для `cairosvg`).
В production-образе она установлена (`Dockerfile`); на Windows без Cairo
PDF генерируется без картинки (graceful degradation), см. §8.11.

### 6.3. Фоновый воркер arq (KI-053)

Выгрузка датасета выполняется отдельным процессом `arq`, а не
`BackgroundTasks` — задача переживает рестарт API.

**Docker (dev):**

```bash
docker compose up -d postgres redis worker
docker compose logs -f worker
```

`worker` использует тот же Redis и bind-mount `./data` (`EXPORT_DIR`).
API на хосте (`REDIS_URL=redis://127.0.0.1:6381/0`) и воркер в контейнере
(`redis://redis:6379/0`) видят **один** Redis-инстанс, поэтому очередь
общая. В production — сервис `worker` в `docker-compose.prod.yml` и
volume `prod_export_data`.

**Без Docker:**

```bash
arq worker.WorkerSettings
# или, если пакет установлен как wheel (без корневого worker.py):
arq app.worker.WorkerSettings
```

При старте воркер и API выполняют recovery: `running`-джобы старше
`EXPORT_JOB_STALE_MINUTES` (15) переводятся в `failed`. Если Redis
недоступен, `POST /v1/admin/exports/jobs` вернёт 503
`export_queue_unavailable`, а строка станет `failed` (не «вечный queued»).

### 6.4. MinIO (S3-совместимое хранилище)

Появится в T-019+ (когда понадобится хранилище для аннотированных
партий и экспортов). Пока не требуется.

---

## 7. Production-развёртывание (Docker)

Production-сборка — один контейнер с backend + frontend + миграциями.

### 7.1. Подготовить `.env`

```bash
cp .env.example .env
```

**Обязательно заменить:**

- `JWT_SECRET` — `openssl rand -hex 32`. В `APP_ENV=prod` приложение
  **не стартует** с dev-плейсхолдером или ключом короче 32 символов
  (T-110).
- `POSTGRES_PASSWORD` — сильный пароль.
- `DEEPSEEK_API_KEY` — если нужен уровень `gm`.
- `ADMIN_API_KEY` — токен для `POST /v1/ingest` (`X-Admin-Token`);
  dev-плейсхолдер в prod также отвергается (T-110).
- `PROD_CORS_ORIGINS` — ваш домен (например, `https://chess.example.com`),
  без `*`.

`APP_ENV=prod`, адреса `postgres`/`redis` и путь к Stockfish выставляются
в `docker-compose.prod.yml`; dev-значения `127.0.0.1` и Windows-путь
внутрь контейнера не попадают. При необходимости задайте:

- `API_PORT` — loopback-порт API (по умолчанию `8000`);
- `PROD_POSTGRES_PORT` / `PROD_REDIS_PORT` — loopback-порты БД/Redis
  (по умолчанию `5434`/`6380`);
- `PROD_VLLM_BASE_URL` — LM Studio на хосте;
- `WORKERS` — число uvicorn-воркеров (по умолчанию `1`; каждый воркер
  создаёт свой пул Stockfish).

### 7.2. Собрать и запустить

```bash
docker compose -p chessannotator-prod -f docker-compose.prod.yml build
docker compose -p chessannotator-prod -f docker-compose.prod.yml up -d
```

Проверка:

```bash
docker compose -p chessannotator-prod -f docker-compose.prod.yml ps
curl -s http://127.0.0.1:8000/v1/health | python -m json.tool
curl -s http://127.0.0.1:8000/ | head -n 5   # SPA
```

Если 8000/5434/6380 заняты (KI-001/KI-002), используйте отдельные порты:

```bash
API_PORT=8011 PROD_POSTGRES_PORT=5435 PROD_REDIS_PORT=6382 \
  docker compose -p chessannotator-prod -f docker-compose.prod.yml up -d
```

### 7.3. HTTPS / reverse proxy

Поставьте reverse proxy (nginx, Caddy, Traefik) перед контейнером `app`:

- nginx: `proxy_pass http://127.0.0.1:8000;` + `proxy_set_header Host $host;`
- Caddy: `reverse_proxy 127.0.0.1:8000` (автоматический Let's Encrypt).
- Traefik: labels на контейнере.

Порты `5434`/`6380` опубликованы **только на `127.0.0.1`** (или не
публикуются вовсе) — наружу они не смотрят. БД и Redis доступны
контейнеру `app` по compose-сети.

### 7.4. Обновление

```bash
git pull
docker compose -p chessannotator-prod -f docker-compose.prod.yml up -d --build
```

Миграции применяются автоматически в entrypoint **до** старта
приложения.

### 7.5. Публикация образа в GHCR (T-027)

На обычных push/PR образ только собирается и проверяется (`docker` job).
Публикация включается тегом `v*`:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Job `publish-docker` в `.github/workflows/ci.yml` логинится в `ghcr.io`
через `GITHUB_TOKEN` (`packages: write`), собирает образ и пушит теги
`ghcr.io/iilmchat/ai-chess-annotator:v1.0.0`, `:1.0.0`, `:latest`.
`VITE_API_BASE` можно задать repository variable, если SPA живёт на
отдельном домене; по умолчанию образ same-origin.

```bash
docker pull ghcr.io/iilmchat/ai-chess-annotator:latest
```

---

## 8. Troubleshooting

### 8.1. `docker compose up` → порт занят

**Симптом:** `Bind for 0.0.0.0:5434 failed: port is already allocated`.

**Причина:** локальный Postgres/Redis уже слушает эти порты (или другой
экземпляр chess-annotator).

**Решение:**

```bash
# Найти процесс
lsof -i :5434    # или: sudo ss -tulpn | grep 5434
# Остановить
docker compose down
# Или изменить порт в .env / docker-compose.yml
```

Порты `5434`/`6380` выбраны специально, чтобы не конфликтовать со
стандартными `5432`/`6379`. Если всё равно конфликт — измените в
`docker-compose.yml` и `.env`.

### 8.2. `alembic upgrade head` → `connection refused`

**Симптом:** `sqlalchemy.exc.OperationalError: connection refused`.

**Причина:** Postgres ещё не готов, или неверные `POSTGRES_*` в `.env`.

**Решение:**

```bash
docker compose ps postgres              # должен быть healthy
docker compose logs postgres | tail -20 # ошибки?
# Проверить .env
grep POSTGRES .env
# Проверить доступность
docker compose exec postgres psql -U chess -d chessannotator -c "SELECT 1"
```

### 8.3. Healthcheck → `stockfish: error`

**Симптом:** `{"stockfish": {"status": "error", "detail": "stockfish binary not found"}}`.

**Причина:** бинарник Stockfish не в `PATH` или неверный `STOCKFISH_PATH`.

**Решение:**

```bash
which stockfish              # Linux/macOS
where stockfish              # Windows
# Если пусто — установить (§1.1)
# Если найден, но путь другой — указать в .env:
echo "STOCKFISH_PATH=$(which stockfish)" >> .env
# Перезапустить uvicorn
```

Или использовать Docker-сервис (§6.1).

### 8.4. Healthcheck → `db: error` / `redis: error`

См. §8.2 для `db`. Для `redis`:

```bash
docker compose ps redis
docker compose logs redis | tail -20
docker compose exec redis redis-cli ping    # → PONG
```

Проверить `REDIS_URL` в `.env`: `redis://localhost:6380/0`.

### 8.5. `pnpm --dir web install` → ошибки

**Симптом:** `ERR_PNPM_UNSUPPORTED_ENGINE` или `EACCES`.

**Решение:**

```bash
node --version       # должен быть ≥ 22
corepack enable
corepack prepare pnpm@latest --activate
pnpm --version       # ≥ 12
rm -rf web/node_modules web/pnpm-lock.yaml
pnpm --dir web install
```

### 8.6. `uvicorn` → `ModuleNotFoundError: No module named 'app'`

**Причина:** запущен не из корня репозитория, или `.venv` не активирован.

**Решение:**

```bash
pwd                  # должен быть .../chess-annotator
source .venv/bin/activate
which python         # должен указывать на .venv/bin/python
uvicorn app.main:app --reload
```

### 8.7. `mypy` ругается на `stockfish` / `chess`

**Симптом:** `Cannot find implementation or library stub for module named "stockfish"`.

**Решение:** проверить, что в `pyproject.toml` есть:

```toml
[[tool.mypy.overrides]]
module = ["stockfish.*", "chess.*"]
ignore_missing_imports = true
```

Если есть — перезапустить `mypy app`. Если нет — добавить и закоммитить
отдельным PR (`chore(mypy): add overrides for stockfish and chess`).

### 8.8. CI красный, локально зелёный

**Причина:** разное окружение (версии Python/Node, отсутствие сервисов в
CI, кэш).

**Решение:**

1. Открыть лог CI, найти первый упавший шаг.
2. Воспроизвести локально в чистом окружении:
   ```bash
   rm -rf .venv && python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   ruff check . && mypy app && pytest -q
   ```
3. Если воспроизвелось — починить. Если нет — завести `KI-NNN` с
   описанием разницы окружений.

### 8.9. `vllm` не стартует / OOM

**Симптом:** контейнер `vllm` в статусе `exited` или `unhealthy`, в логах
`CUDA out of memory`.

**Решение:**

- Проверить VRAM: `nvidia-smi` (нужно ≥ 24 GB для Qwen3-8B).
- Уменьшить модель: `VLLM_MODEL=Qwen/Qwen3-1.7B` (нужно ≥ 8 GB).
- Уменьшить `--max-model-len` (по умолчанию 8192 → 4096).
- Закрыть другие GPU-процессы.

Если GPU нет — использовать облачную LLM (§5.1) или отложить локальную
LLM до появления GPU.

### 8.10. PDF-экспорт → `503 pdf_font_unavailable`

**Симптом:** `GET /v1/commentaries/{id}/export/pdf` возвращает
`503 {"detail": "...", "code": "pdf_font_unavailable"}`.

**Причина:** для кириллицы в PDF нужен TrueType-шрифт; в минимальных
Linux-образах (`python:3.12-slim`) системных шрифтов нет.

**Решение:**

```bash
# Debian/Ubuntu:
sudo apt-get install -y fonts-dejavu-core
# или задать явный путь в .env:
# PDF_FONT_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
```

В Docker-образе (`Dockerfile`) `fonts-dejavu-core` уже установлен.
Проверка шрифтов: `python -c "from app.export.pdf import find_pdf_font; print(find_pdf_font())"`.

### 8.11. PDF без диаграммы доски (нет cairo)

**Симптом:** PDF скачивается, текст и ходы на месте, но раздела
«Позиции на доске» нет.

**Причина:** `cairosvg` не может загрузить `libcairo2` (Windows без
GTK/Cairo, slim-образ без пакета). Это **graceful degradation**, а не
ошибка: `app.export.board.render_board_png` возвращает `None`, PDF
собирается без картинок.

**Решение:**

```bash
# Debian/Ubuntu
sudo apt-get install -y libcairo2
# macOS
brew install cairo
# Windows: установите GTK3 Runtime (Cairo DLL) или используйте WSL/Docker
```

Проверка: `python -c "from app.export.board import cairosvg_available; print(cairosvg_available())"`
→ `True`. В production-образе `libcairo2` уже установлен.

---

## 9. Обслуживание: retention и FinOps

Постоянно растут `llm_calls` (durable-история вызовов LLM, T-030),
`usage_daily` (FinOps-агрегаты, T-031b) и `export_jobs`/`EXPORT_DIR`
(фоновые выгрузки, KI-050/KI-054). Их чистку и месячные отчёты можно
повесить на cron/планировщик: скрипты идемпотентны, читают `Settings`
(`.env`/ENV) и возвращают exit code `0` (успех) или `2` (неверные
аргументы / БД недоступна).

`engine_cache` (T-108) в cron не нужен: устаревшие строки удаляются
автоматически при старте приложения (`ENGINE_CACHE_TTL_DAYS`, 30 дней).

### 9.1. Retention `llm_calls` (KI-048)

Храним последние `LLM_CALLS_RETENTION_DAYS` дней (по умолчанию **90**).
Скрипт удаляет старые строки **батчами** (по 1000; каждая транзакция
короткая) — один большой `DELETE` дольше держит блокировки и раздувает
WAL. `--dry-run` показывает объём без удаления, `--max-batches`
ограничивает работу за один запуск (удобно для «мягкой» фоновой уборки).

```bash
# Посмотреть, сколько строк старше 90 дней (ничего не удаляет)
python scripts/prune_llm_calls.py --dry-run

# Удалить батчами по 1000 с паузой 50 мс; JSON-отчёт в stdout
python scripts/prune_llm_calls.py --batch-size 1000 --sleep-ms 50 --json

# Переопределить срок хранения для разового запуска
python scripts/prune_llm_calls.py --retention-days 30
```

**Cron (Linux, ежедневно в 03:15):**

```cron
15 3 * * * cd /srv/chessannotator && .venv/bin/python scripts/prune_llm_calls.py --json >> /var/log/chessannotator/prune_llm_calls.log 2>&1
```

**systemd timer** (альтернатива cron): unit `chessannotator-prune.service`
с `ExecStart=/srv/chessannotator/.venv/bin/python scripts/prune_llm_calls.py`
и `chessannotator-prune.timer` с `OnCalendar=*-*-* 03:15:00`.

**Windows Task Scheduler:**

```powershell
schtasks /Create /TN "ChessAnnotator prune llm_calls" /SC DAILY /ST 03:15 `
  /TR "C:\Projects\AI\ai-chess-annotator\.venv\Scripts\python.exe C:\Projects\AI\ai-chess-annotator\scripts\prune_llm_calls.py"
```

Проверить результат: `python scripts/prune_llm_calls.py --dry-run --json`
(поле `deleted` — сколько строк осталось бы к удалению).

Дашборд с метриками (latency p50/p95/p99, error rate, стоимость,
cache-hit rate, топ-5 пользователей) — в админке: `/admin/llmops`
(см. `DESIGN_ADMIN_AND_OPS.md` §7.4).

### 9.2. Retention и месячный отчёт `usage_daily`

`scripts/aggregate_usage.py` печатает суммы месяца и (по умолчанию)
удаляет агрегаты старше 90 дней:

```bash
python scripts/aggregate_usage.py --month 2026-10 --json --retention-days 90
python scripts/aggregate_usage.py --no-cleanup        # только отчёт
```

Cron (1-е число месяца, 04:00):

```cron
0 4 1 * * cd /srv/chessannotator && .venv/bin/python scripts/aggregate_usage.py --json --retention-days 90 >> /var/log/chessannotator/aggregate_usage.log 2>&1
```

### 9.3. Retention файлов и строк экспортов (KI-054)

Готовые JSONL лежат в `EXPORT_DIR` (по умолчанию `data/exports/`, вне
web-root), а метаданные — в `export_jobs`. Скрипт
`scripts/prune_export_jobs.py` удаляет строки и файлы старше
`EXPORT_RETENTION_DAYS` дней (по умолчанию **7**).

Безопасность и порядок:

- удаляются только пути **внутри** `EXPORT_DIR`; `file_path` вне корня
  (подмена в БД, симлинк) пропускается с warning — произвольный файл хоста
  скрипт не удалит;
- сначала удаляется файл, потом строка: падение между шагами оставит
  строку без файла (download → 410), а не файл-сироту;
- работа идёт батчами (`--batch-size`, по умолчанию 100) с коммитом на
  батч; `--max-batches` ограничивает объём одного запуска;
- `--dry-run` только считает строки/файлы, `--json` печатает машинный
  отчёт; exit code `0` — успех, `2` — БД недоступна/неверные аргументы.

```bash
# Посмотреть, что будет удалено (ничего не удаляет)
python scripts/prune_export_jobs.py --dry-run --json

# Удалить батчами по 100 с паузой 50 мс; отчёт — JSON
python scripts/prune_export_jobs.py --batch-size 100 --sleep-ms 50 --json

# Разово переопределить срок хранения и каталог
python scripts/prune_export_jobs.py --retention-days 30 --export-dir /srv/data/exports
```

**Cron (Linux, ежедневно в 03:45 — после `llm_calls`):**

```cron
45 3 * * * cd /srv/chessannotator && .venv/bin/python scripts/prune_export_jobs.py --json >> /var/log/chessannotator/prune_export_jobs.log 2>&1
```

В Docker-образе `scripts/` уже скопирован, поэтому из контейнера:
`docker compose exec app python scripts/prune_export_jobs.py --dry-run`
(или `docker compose run --rm app python scripts/prune_export_jobs.py`).

**systemd timer:** unit `chessannotator-prune-exports.service` с
`ExecStart=/srv/chessannotator/.venv/bin/python
scripts/prune_export_jobs.py` и `chessannotator-prune-exports.timer` с
`OnCalendar=*-*-* 03:45:00`.

**Windows Task Scheduler:**

```powershell
schtasks /Create /TN "ChessAnnotator prune exports" /SC DAILY /ST 03:45 `
  /TR "C:\Projects\AI\ai-chess-annotator\.venv\Scripts\python.exe C:\Projects\AI\ai-chess-annotator\scripts\prune_export_jobs.py"
```

Проверить результат: `python scripts/prune_export_jobs.py --dry-run --json`
(поля `deleted_rows`/`deleted_files`). Скрипт — единственный механизм
retention; подписанные ссылки на скачивание (signed URL с TTL) остаются
KI-057, доступ — по-прежнему JWT/cookie админки.

Старый ручной вариант (если скрипт ещё не настроен):

```sql
-- Только завершённые джобы старше 30 дней; файлы удалите отдельно и
-- внимательно проверьте путь!
DELETE FROM export_jobs
WHERE status IN ('done', 'failed')
  AND finished_at < now() - interval '30 days';
```

---

## 10. Что дальше

После успешного healthcheck:

1. **Прочитать `RULES.md`** — правила разработки (обязательно).
2. **Прочитать `SPEC.md`** — архитектура и контракты.
3. **Открыть `TODO.md`** — найти текущую задачу (сейчас — T-007:
   Ingestion на `python-chess`).
4. **Посмотреть `RELEASES.md`** — что уже сделано.
5. **Проверить `KNOWN_ISSUES.md`** — нет ли блокеров, влияющих на вашу
   задачу.

Полезные ссылки:

- [SPEC.md](SPEC.md) — источник истины.
- [RULES.md](RULES.md) — правила.
- [ARCHITECTURE.md](ARCHITECTURE.md) — как устроено (обновляется по мере
  роста).
- [USER_GUIDE.md](../user_guide/USER_GUIDE.md) — как пользоваться (после
  T-019).
- [../README.md](../README.md) — навигация по всей документации.