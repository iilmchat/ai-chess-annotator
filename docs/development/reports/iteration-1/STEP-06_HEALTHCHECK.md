# STEP-06 — Живой healthcheck (T-003)

**Дата:** 2026-10-28
**Итерация:** 1 — Bootstrap (T-001…T-006)
**Статус:** ✅ выполнен с двумя отклонениями окружения (KI-002, KI-006)

---

## 1. Что сделано

1. **Установлен Stockfish 17.1** (на хосте бинарника не было, `healthcheck`
   давал бы `error`):

   ```powershell
   # https://github.com/official-stockfish/Stockfish/releases/tag/sf_17.1
   curl.exe -sS -L -o stockfish.zip .../stockfish-windows-x86-64-avx2.zip
   Expand-Archive ... ; copy stockfish.exe D:\Tools\stockfish\stockfish.exe
   ```

   Проверка UCI: `Stockfish 17.1 by the Stockfish developers`.
   Путь задан в локальном `.env`:
   `STOCKFISH_PATH=D:\Tools\stockfish\stockfish.exe`
   (ASCII-путь, чтобы не зависеть от кириллицы в `%LOCALAPPDATA%`).

2. Запущен `uvicorn app.main:app --host 127.0.0.1 --port 8010`
   (фоновый процесс; без `--reload`, см. § 4).

3. `GET /v1/health` → **200** и **ровно ожидаемый JSON**.

## 2. Фактический ответ

```json
{
  "status": "degraded",
  "components": {
    "db":        {"status": "ok",      "detail": null},
    "redis":     {"status": "ok",      "detail": null},
    "stockfish": {"status": "ok",      "detail": null},
    "llm":       {"status": "skipped", "detail": "local vllm configured"}
  }
}
```

`status: "degraded"` — ожидаемо: LLM-провайдер ещё не подключён
(итерация 1). `db`, `redis`, `stockfish` — `ok`.

Лог сервера:

```
INFO:     Started server process [22900]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8010 (Press CTRL+C to quit)
INFO:     127.0.0.1:61389 - "GET /v1/health HTTP/1.1" 200 OK
```

## 3. Два отклонения окружения

### 3.1. Порт 8000 занят чужим контейнером (KI-002)

`127.0.0.1:8000` держит `ai-opencamera-api-1` (другой проект, публикация
порта через Docker Desktop). Uvicorn на 8000 не поднимется, остановить
чужой контейнер — не наше дело. Проверка выполнена на **8010**;
API-контракт и код от номера порта не зависят. В CI/прод порт 8000
свободен.

### 3.2. Stockfish вне репозитория (KI-006)

Бинарник 79 МБ не коммитится. В CI он ставится `apt-get install -y
stockfish`, на Windows-dev — вручную (команды в § 1). `healthcheck`
поэтому может отдать `stockfish: error` на чистой машине — это
задокументировано, а не «замазано» заглушкой.

## 4. Почему без `--reload`

`--reload` поднимает родительский процесс-наблюдатель и дочерний
uvicorn: в фоновом job это оставляет «сироту» после остановки, а на
проверку контракта `/v1/health` не влияет. Для интерактивной разработки
`uvicorn app.main:app --reload` работает как в README.

## 5. Побочная находка: `localhost` → IPv6 (исправлено в конфиге)

Первый прогон дал `redis: error — Timeout connecting to server`:

| URL | Результат |
| :--- | :--- |
| `redis://localhost:6381/0` | `TimeoutError: Timeout connecting to server` |
| `redis://127.0.0.1:6381/0` | `True` |

Причина: на Windows `localhost` резолвится сначала в `::1`
(`C:\Windows\System32\drivers\etc\hosts` — «name resolution is handled
within DNS itself»), а compose публикует порты **только** на
`127.0.0.1` (loopback IPv4, требование безопасности). redis-py, в
отличие от asyncpg, не перебирает адреса.

Исправление (не «workaround», а корректные дефолты):

- `app/config.py`: `postgres_host = "127.0.0.1"`,
  `redis_url = "redis://127.0.0.1:6380/0"`;
- `.env.example` / `.env` — то же;
- CI: `POSTGRES_HOST: 127.0.0.1`, `REDIS_URL: redis://127.0.0.1:6380/0`
  (на Linux-раннере та же ловушка с `::1` — превентивно).

PostgreSQL с `localhost` работал случайно: asyncpg пробует все адреса из
DNS-ответа. Теперь оба клиента используют явный IPv4-loopback.

## 6. Проверки

| Проверка | Результат |
| :--- | :--- |
| `HTTP 200` | ✅ |
| `status == "degraded"` | ✅ |
| `db.status == "ok"` | ✅ (`SELECT 1` через asyncpg) |
| `redis.status == "ok"` | ✅ (`PING` в контейнер на 6381) |
| `stockfish.status == "ok"` | ✅ (`shutil.which("D:\\Tools\\stockfish\\stockfish.exe")`) |
| `llm.status == "skipped"` | ✅ (vLLM не поднят, ключей облака нет) |

## 7. Что дальше

Шаг 7 — сборка фронтенда (`pnpm --dir web install/typecheck/build`).
