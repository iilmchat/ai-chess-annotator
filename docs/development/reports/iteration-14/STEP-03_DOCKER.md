# STEP-03 — Docker Production (T-025)

**Дата:** 2026-10-28
**Итерация:** 14
**Статус:** ✅ (публикация в GHCR не выполнялась)

## Что сделано

1. `Dockerfile` переведён на multi-stage:
   - `node:22-alpine` + pnpm 12.14.0 → `pnpm build` → `web/dist`;
   - `python:3.12-slim` + Stockfish 17 + зависимости + `alembic` + SPA;
   - non-root `appuser` (uid 10001), `no-new-privileges` в compose.
2. `docker-compose.prod.yml`:
   - `app` слушает `127.0.0.1:${API_PORT:-8000}:8000`, healthcheck
     `/v1/health`, `alembic upgrade head` перед uvicorn;
   - `postgres` — `127.0.0.1:${PROD_POSTGRES_PORT:-5434}:5432`,
     `redis` — `127.0.0.1:${PROD_REDIS_PORT:-6380}:6379`;
   - внутри сети `POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`,
     `REDIS_URL=redis://redis:6379/0`, `STOCKFISH_PATH=/usr/bin/stockfish`;
   - `VLLM_BASE_URL` по умолчанию `host.docker.internal:8034` (LM Studio
     на хосте), `extra_hosts: host-gateway`.
3. `.dockerignore` исключает `.git`, `.venv`, `web/node_modules`,
   `web/dist`, `data`, `docs`, `tests`, `.env*` — контекст сборки 350 КБ.
4. Собран образ, поднят изолированный стек (`-p chessannotator-prod`) на
   портах `8011/5435/6382`.

## Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Синтаксис compose | `docker compose -f docker-compose.prod.yml config` | exit 0 |
| Сборка | `docker build -t chessannotator-prod:test .` | exit 0, образ 512 MB |
| Запуск | `docker compose -p chessannotator-prod -f ... up -d --build` | 3 контейнера |
| Health | `docker inspect ... --format '{{.State.Health.Status}}'` | `healthy` |
| `/v1/health` | `curl 127.0.0.1:8011/v1/health` | 200, `{"status":"ok"}` |
| SPA | `GET /` | 200 `text/html`, `<div id="root">` |
| Analyze | `POST /v1/analyze` (4 полухода) | 200, `analysis_id=1` |
| Chat | `POST /v1/chat` | 200, 948 токенов |
| Порты | `docker ps` | `127.0.0.1:8011->8000`, `127.0.0.1:5435->5432`, `127.0.0.1:6382->6379` |

`/v1/health`:

```json
{"status":"ok","components":{
  "db":{"status":"ok"},"redis":{"status":"ok"},
  "stockfish":{"status":"ok"},"llm":{"status":"ok","detail":"cloud key present"}
}}
```

## Безопасность

- БД и Redis опубликованы только на `127.0.0.1`, не на `0.0.0.0`;
  наружу смотрит только reverse-proxy (в compose API тоже loopback).
- Секреты — через `env_file: .env`; в образ не копируются.
- Контейнер работает non-root, `no-new-privileges`.
- `JWT_SECRET`/`POSTGRES_PASSWORD` в dev-.env — заменить в production.

## Не сделано

- Публикация в GHCR (`latest`, `sha-*`, `vX.Y.Z`) — нет registry-доступа;
  это T-027.
- Reverse-proxy/TLS в compose не включён — выносится на инфраструктуру.

## Что дальше

T-026 (документация), затем T-027 (tag + GHCR) после настройки registry.
