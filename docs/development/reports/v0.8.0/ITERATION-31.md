# Итерация 31 — Observability Stack & PDF Board Diagram (T-109b, T-107b, KI-050)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **T-107b закрыт** (диаграмма доски в PDF);
**T-109b закрыт** (Grafana/Tempo/Prometheus + провижининг дашборда);
**KI-050 закрыт** (фоновая выгрузка датасета). Новый KI — KI-053
(джоба не переживает рестарт процесса).
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### T-107b: диаграмма доски в PDF

| Файл | Что изменилось |
| :--- | :--- |
| `app/export/board.py` *(новый)* | `BoardDiagram`, `MoveSnapshot`; `board_svg` через `chess.svg.board()` (координаты, подсветка последнего хода, ориентация); `cairosvg_available()` — ленивый импорт + пробный рендер (кэш на процесс, ловит `OSError` отсутствующей libcairo); `render_board_png` (`None` при недоступном/сломанном рендерере); `select_diagrams` — финальная позиция + до 2 критических моментов (`blunder`/`mistake` по `|delta_cp|`), UCI последнего хода восстанавливается проигрыванием SAN |
| `app/export/pdf.py` | `CommentaryPdfData.diagrams`; `_draw_diagrams` (82 мм, подпись, перенос страницы, пропуск битой картинки); раздел «Позиции на доске» после ходов; docstring про graceful degradation |
| `app/export/__init__.py` | Реэкспорт `BoardDiagram`, `MoveSnapshot`, `render_board_png`, `select_diagrams` |
| `app/api/v1/commentary.py` | Эндпоинт PDF собирает `MoveSnapshot` из `moves`, `initial_fen` из PGN; `select_diagrams` в try/except (диаграммы не критичны для PDF); `diagrams=` в `CommentaryPdfData` |
| `pyproject.toml` | `cairosvg>=2.7` (production); mypy-override `cairosvg.*` |
| `Dockerfile` | `libcairo2` в apt-пакетах (нативная зависимость cairosvg) |
| `.github/workflows/ci.yml` | `libcairo2` в apt-пакетах backend-job (реальный рендер в CI) |
| `tests/unit/test_pdf_board.py` *(новый)* | 9 тестов: SVG стартовой позиции, битый UCI, выбор финальной/критических, `max_critical`, пустые ходы, деградация без рендерера, реальный PNG (skip без cairo), вставка PNG в PDF через pypdf, PDF без рендерера без картинок |
| `tests/integration/test_commentary_pdf.py` | +1 тест: PDF содержит диаграмму тогда и только тогда, когда `cairosvg_available()`; без cairo — 200 и ни одной картинки |

### T-109b: Observability Stack

| Файл | Что изменилось |
| :--- | :--- |
| `docker-compose.observability.yml` *(новый)* | Отдельный compose-проект: `otel-collector` (contrib 0.111, OTLP gRPC/HTTP, healthcheck), `tempo` 2.6.1, `prometheus` v2.55.1, `grafana` 11.3.0; порты только loopback; volumes; healthchecks |
| `deploy/otel-collector/config.yaml` *(новый)* | OTLP-приёмник, `memory_limiter`/`batch`, `spanmetrics`-коннектор (бакеты, dimensions `http.route`/`http.request.method`/`http.response.status_code`), экспортёры `otlp/tempo`, `prometheus` (:8889), `prometheus/spanmetrics` (:8890) |
| `deploy/tempo/tempo.yaml` *(новый)* | single-binary Tempo, OTLP receivers, local blocks/WAL, retention 24h |
| `deploy/prometheus/prometheus.yml` *(новый)* | Скрейп `otel-collector:8889`, `:8890`, `tempo:3200`, self |
| `deploy/grafana/provisioning/datasources/datasources.yaml` *(новый)* | Prometheus (default, uid `prometheus`) + Tempo (uid `tempo`, tracesToMetrics) |
| `deploy/grafana/provisioning/dashboards/dashboards.yaml` *(новый)* | File-провижининг папки `ChessAnnotator`, обновление каждые 30 с |
| `deploy/grafana/dashboards/chessannotator-observability.json` *(новый)* | 9 панелей: p95/p99 для `/v1/commentary` и `/v1/analyze`, 5xx error rate, request rate, стоимость, токены in/out, LLM calls by status, LLM latency p95 by model, текст-подсказка |
| `app/observability.py` | `configure_metrics` (OTLPMetricExporter `/v1/metrics`, PeriodicExportingMetricReader, resource), инструменты `llm.calls`/`llm.tokens`/`llm.cost.usd`/`llm.latency`, `record_llm_metrics`, `shutdown_metrics`, `reset_metrics_for_tests`; общий `_resource`; endpoint в логах |
| `app/tracing.py` | `request_span` отдаёт `Span | None`; HTTP-спан получает `http.request.method`, `http.route` (полный путь из FastAPI effective-route-context), `http.response.status_code`, статус ERROR для 5xx, имя `METHOD /v1/...`; `TraceMiddleware(tracer=...)` для тестов; `_route_path` |
| `app/main.py` | `configure_metrics`/`shutdown_metrics` в lifespan; нативная телеметрия FastAPI ≥ 0.162 отключена (`telemetry={"tracing": False, "metrics": False, "logs": False}`) через `inspect.signature` — иначе два HTTP-спана на запрос |
| `app/llm/calls.py` | `record_llm_metrics` из durable-рекордера — те же цифры, что в `llm_calls` |
| `app/config.py` | `otel_metrics_enabled`, `otel_metrics_interval_ms` |
| `.env.example` | `OTEL_METRICS_ENABLED`, `OTEL_METRICS_INTERVAL_MS`; observability-стек в комментарии |
| `tests/unit/test_observability_metrics.py` *(новый)* | 5 тестов: disabled-флаги, no-op без провайдера, значения счётчиков/гистограммы, cache-hit без токенов/стоимости |
| `tests/unit/test_observability_http_span.py` *(новый)* | 3 теста: route/method/status, полный префикс `include_router` (`/v1/analyze`), 5xx → ERROR |

### KI-050: фоновая выгрузка датасета

| Файл | Что изменилось |
| :--- | :--- |
| `app/db/models.py` | `ExportJobStatus` (queued/running/done/failed), `ExportJob`: `status`, `actor`, `filters` JSONB, `file_path`, `file_size`, `sha256`, `rows_exported`, `error`, `created_at/started_at/finished_at`; CHECK статуса/неотрицательных размеров, индексы `(status, created_at)` и `created_at` |
| `alembic/versions/b0c9d2f13425_export_jobs_background_dataset_export_.py` *(новый)* | Миграция `export_jobs` (down_revision `19bf1fdc2a73`, head): таблица, индексы, CHECK; upgrade/downgrade |
| `app/dataset/records.py` *(новый)* | Общая выборка: `ExportFilters` (+`to_dict`/`parse_filters`), `apply_export_filters`, `build_export_record`, `iter_export_records` (keyset-пагинация); вынесено из API, чтобы job и стрим не дублировали правила |
| `app/dataset/jobs.py` *(новый)* | `create_export_job`, `run_export_job` (queued→running→done/failed, временный файл → atomic rename, sha256/размер/rows, батчевая запись через `asyncio.to_thread`), `resolve_export_path` (защита от пути вне `EXPORT_DIR`), `list/get`, `audit_dataset_export` (`job_id`, `background=true`) |
| `app/api/v1/admin_exports.py` | `_authorize_export` → `ExportActor` (роль), `_require_export_actor` (чтение) и `_require_export_writer` (только admin/legacy); `POST /jobs` (202, BackgroundTasks), `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/download` (409/410); стрим переведён на `iter_export_records`/общий аудит |
| `app/admin.py` | `DatasetExportView` принимает `jobs_url`, передаёт `can_create` (auditor read-only) |
| `app/templates/dataset_export.html` | Карточка «Фоновая выгрузка»: кнопка «Создать экспорт», поллинг статуса, ссылка «Скачать JSONL», предупреждение для auditor; JS без innerHTML |
| `app/config.py` | `export_dir="data/exports"`, `export_jobs_page_size=20` |
| `app/errors.py` | `ExportJobNotReadyError` (409), `ExportJobFileMissingError` (410) |
| `.env.example` | `EXPORT_DIR`, `EXPORT_JOBS_PAGE_SIZE` |
| `tests/integration/test_export_jobs.py` *(новый)* | 9 тестов: create→done→download (sha256/размер/NDJSON), пустая выборка, список, auditor 403/read, 401/403, 409/404/410, path traversal, аудит с job_id, cookie-сессия админки |
| `tests/integration/test_admin_exports.py` | Страница экспорта: ссылка на `/jobs`, кнопка для admin, read-only текст для auditor |
| `tests/test_models.py` | `export_jobs` в `SPEC_TABLES` + тест полей/индексов/CHECK |
| `tests/integration/test_data_isolation.py` | `_settings()` переносит `stockfish_path` из `get_settings()` (Windows-dev: `_env_file=None` иначе искал `/usr/bin/stockfish`) |

### Документация

`docs/development/reports/v0.8.0/ITERATION-31.md` (этот файл),
`TODO.md` (T-107b/T-109b → `[x]`, T-034b/KI-050, KI-053),
`docs/CHANGELOG.md`, `docs/development/RELEASES.md`,
`docs/KNOWN_ISSUES.md` (KI-050 Closed, +KI-053),
`docs/development/SPEC.md` (§4 `export_jobs`, §5 API, §10 observability),
`docs/development/ARCHITECTURE.md` (§2 модули, §4 API, §5 таблицы,
§5.1 observability), `docs/development/GETTING_STARTED.md` (§6.2 стек,
§8.11 cairo, §9.3 файлы экспортов), `docs/user_guide/USER_GUIDE.md`
(диаграммы PDF, §7.9 фоновая выгрузка),
`docs/development/DESIGN_ADMIN_AND_OPS.md` (§5.4 Export, §6.4 метрики),
`README.md` (724 теста, 46 frontend, 16 таблиц/head `b0c9d2f13425`,
итерация 31, observability/PDF/джобы).

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Диаграммы доски | `tests/unit/test_pdf_board.py` | 9 (8 passed, 1 skipped без cairo) |
| PDF + диаграмма (HTTP) | `tests/integration/test_commentary_pdf.py` | +1 |
| OTLP-метрики LLM | `tests/unit/test_observability_metrics.py` | 5 |
| HTTP-спан/spanmetrics | `tests/unit/test_observability_http_span.py` | 3 |
| Фоновые выгрузки | `tests/integration/test_export_jobs.py` | 9 |
| Схема БД (`export_jobs`) | `tests/test_models.py` | +1 |
| Страница экспорта (RBAC/UI) | `tests/integration/test_admin_exports.py` | +0 (расширены assertions) |

Проверяемые сценарии:

- **PDF:** SVG стартовой позиции содержит фигуры; битый `last_move_uci` не
  мешает; `select_diagrams` ставит финальную позицию первой, затем
  `blunder`/`mistake` по убыванию `|delta_cp|`, кап `max_critical`,
  пустые ходы → `()`; без cairo `render_board_png → None`, PDF валиден и
  без image-XObject; с подменённым PNG pypdf видит картинку и подпись
  «Позиции на доске»; интеграционный контракт «есть cairo → есть
  картинка, нет cairo → нет картинки» не падает на Windows.
- **Метрики:** `configure_metrics` → `None` при `OTEL_ENABLED=false` или
  `OTEL_METRICS_ENABLED=false`; `record_llm_metrics` без провайдера —
  no-op; с `InMemoryMetricReader` — `llm.calls=1`, `llm.tokens in=10/out=5`,
  `llm.cost.usd≈0.0015`, latency count/sum; cache-hit не создаёт
  токены/стоимость; `llm.level=None` → `unknown`.
- **HTTP-спан:** `http.route`/`http.request.method`/`http.response.status_code`
  на Starlette; для FastAPI `include_router(prefix="/v1")` route —
  `/v1/analyze`, имя `POST /v1/analyze`; 5xx → статус ERROR.
- **Фоновые выгрузки:** 202 и `status=queued` в ответе, затем `done`;
  файл в `EXPORT_DIR`, `rows_exported=1`, `file_size`/`sha256` совпадают;
  скачивание отдаёт NDJSON, `Content-Disposition`, `X-Export-Sha256`;
  пустая выборка — пустой файл; auditor не создаёт (403), но читает и
  скачивает; аноним 401, role user 403; queued/failed → 409, чужой
  `file_path` → 410, несуществующая джоба → 404; аудит `dataset.export`
  с `job_id`/`background=true`/фильтрами/rows; cookie-сессия админки
  создаёт и скачивает джобу.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:**
**724/726 passed** (backend; 1 skipped, 1 xfailed), **46/46** frontend,
**10/10** E2E mocked.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД iter31 release) | `pytest -q` (POSTGRES_DB=`chessannotator_iter31_release`, Redis 6381, `STOCKFISH_PATH`) | **724 passed, 1 skipped, 1 xfailed, 3 warnings in 252.64s**, exit 0; collected 726; skip — нет cairo на Windows-dev, xfail — KI-012; **+28** к итерации 30 (696) |
| Диаграммы доски | `pytest tests/unit/test_pdf_board.py -q` | **8 passed, 1 skipped** (cairo) |
| Фоновые выгрузки | `pytest tests/integration/test_export_jobs.py -q` | **9 passed** |
| OTLP-метрики | `pytest tests/unit/test_observability_metrics.py -q` | **5 passed** |
| HTTP-спан/spanmetrics | `pytest tests/unit/test_observability_http_span.py -q` | **3 passed** |
| PDF integration | `pytest tests/integration/test_commentary_pdf.py -q` | **7 passed** |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 81 source files** (exit 0; +3 файла) |
| Alembic | `upgrade head`, `alembic check`, `downgrade base → upgrade head` | head `b0c9d2f13425`; «No new upgrade operations detected»; roundtrip OK; **16 доменных таблиц** (17 с `alembic_version`), 12 ревизий |
| Docker build | `docker build -t chessannotator:iter31 .` | exit 0; `docker run … python -c "import app.main"` → OK; `cairosvg_available()` → **True** |
| PDF в Docker | генерация + pypdf | cairo True; PNG 33 533 байта; PDF **77 952** байта, 1 страница, **1 image**; найдены «Позиции на доске», «Финальная позиция», игроки, комментарий |
| Observability stack | `docker compose -f docker-compose.observability.yml up -d` | 4/4 healthy; Grafana `/api/health` ok; datasources Prometheus/Tempo; дашборд **9 панелей** |
| Prometheus targets | `/api/v1/targets` | 4/4 `up` (`otel-collector-llm-metrics`, `-span-metrics`, `tempo`, `prometheus`) |
| Spanmetrics | Prometheus query | `traces_span_metrics_calls_total` с `http_route=/v1/analyze`, `http_response_status_code=200`; p95 `/v1/analyze` = **242.5 мс** |
| OTLP-метрики | smoke `record_llm_metrics` | `llm_calls_total=1`, `llm_tokens_total=1801`, `llm_cost_usd_total=0.000957`, `llm_latency_milliseconds_count=1` |
| Tempo | `/api/traces/<id>` | трейс нового запроса найден, scope `app.http`, 1 span |
| Frontend tests | `pnpm --dir web test` | **46 passed** (7 файлов), 1.96 с |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 6.74 с; JS 465.21 КБ (gzip 146.45), CSS 14.29 КБ (gzip 3.77) |
| E2E mocked | `pnpm --dir web run test:e2e` | **10 passed** (5 сценариев × chromium/mobile), 5.6 с (через `vite preview`, см. §5) |

---

## 4. Smoke (ручной сценарий)

**4.1. Observability-стек.**

```text
docker compose -f docker-compose.observability.yml up -d
grafana          Up (healthy)  127.0.0.1:3000
otel-collector   Up (healthy)  127.0.0.1:4317-4318, 8889-8890, 13133
prometheus       Up (healthy)  127.0.0.1:9090
tempo            Up (healthy)  127.0.0.1:3200

GET http://127.0.0.1:3000/api/health
→ {"database":"ok","version":"11.3.0"}
GET /api/datasources → Prometheus (uid=prometheus), Tempo (uid=tempo)
GET /api/dashboards/uid/chessannotator-observability
→ title="ChessAnnotator — Observability", panels=9, version=1

Prometheus targets: 4/4 up.
```

Живое приложение (`OTEL_ENABLED=true`, OTLP на `127.0.0.1:4318`,
`POST /v1/analyze`, beginner):

```text
Prometheus:
  traces_span_metrics_calls_total{http_route="/v1/analyze",http_response_status_code="200"}
  histogram_quantile(0.95, …{http_route="/v1/analyze"}) = 242.5 мс
Grafana /api/ds/query (панель p95 /v1/analyze) → 242.5
Tempo: trace id из X-Trace-Id → 1 span, scope app.http
```

OTLP-метрики (smoke `record_llm_metrics`, provider=deepseek,
tokens 1234/567, cost $0.000957):

```text
llm_calls_total=1
llm_tokens_total=1801
llm_cost_usd_total=0.000957
llm_latency_milliseconds_count=1
```

**4.2. PDF с диаграммой (Docker, libcairo2).**

```text
cairosvg_available: True
png_bytes: 33533 signature: b'\x89PNG'
pdf_bytes: 77952 pages: 1 images_per_page: [1]
has_section: True  has_caption: True
players: True      comment: True
```

На Windows-dev без Cairo: `cairosvg_available() == False`,
`render_board_png → None`, PDF собирается без раздела «Позиции на доске»
и без image-XObject; интеграционный тест проверяет именно этот контракт
(не 500).

**4.3. Фоновая выгрузка.** Интеграционный сценарий (PostgreSQL):
`POST /v1/admin/exports/jobs` → 202/queued → `done` (rows=1, sha256,
file_size) → `GET /jobs/{id}/download` → NDJSON с `schema_version=2`,
`Content-Disposition: attachment; filename="edits-{id}.jsonl"`,
`X-Export-Sha256`; аудит `dataset.export` с `job_id`. Пустая выборка —
`rows_exported=0`, файл 0 байт. Auditor: создание 403, чтение/скачивание
200.

**4.4. Миграция.** `alembic upgrade head` → `b0c9d2f13425`;
`alembic check` — «No new upgrade operations detected»; полный
`downgrade base && upgrade head` проходит; 16 доменных таблиц,
`\d export_jobs` соответствует модели.

---

## 5. Что не так

1. **На Windows-dev нет cairo.** `cairosvg` установлен, но импорт падает
   с `OSError: no library called "cairo-2"`; PDF генерируется без
   диаграммы. Это ожидаемая деградация; реальный путь проверен в Docker
   (`libcairo2`) и будет проверяться в CI (пакет добавлен в workflow).
   Для Windows нужен GTK3 Runtime (см. GETTING_STARTED §8.11).
2. **Диаграммы — только финальная позиция + до 2 критических.** Нет
   стрелок/вариантов, выбора ориентации по игроку, диаграмм по фазам;
   это осознанный объём T-107b. Каждая диаграмма — PNG ~33 КБ; PDF с
   тремя диаграммами ~100–150 КБ.
3. **E2E локально запускался против `vite preview`, а не dev-сервера.**
   В окружении DSH esbuild не может писать в `web/node_modules/.vite`
   (`Access is denied` из дочернего процесса), поэтому `vite dev` не
   стартует; `vite preview` раздаёт уже собранный `web/dist` и позволяет
   прогнать те же mocked-сценарии. CI использует `pnpm dev` на чистом
   окружении; код/конфиг E2E не менялись.
4. **Фоновая джоба не переживает рестарт процесса.** `BackgroundTasks`
   выполняет выгрузку в процессе API; при падении/остановке строка
   останется в `running`, автоповтора нет. Это KI-053 (переход на `arq`
   с очередью Redis, recovery зависших, retention файлов).
5. **Нет подписанных ссылок на скачивание.** Доступ — JWT/cookie
   админки; ссылка `jobs/{id}/download` не содержит подписи/срока.
   В DoD итерации не входили; для внешних получателей понадобится
   отдельный signed URL.
6. **Файлы и строки `export_jobs` не чистятся автоматически.** Каталог
   `EXPORT_DIR` растёт; в GETTING_STARTED §9.3 добавлены примеры ручной
   очистки (find/SQL). Retention — часть KI-053.
7. **Observability-стек — dev/staging, не production.** Локальные
   volumes Tempo/Prometheus, retention 24h/15d, Grafana admin/admin по
   умолчанию, нет TLS/SSO/HA; для прода — S3-бэкенд Tempo, remote
   write/Thanos, Alertmanager (T-113), секреты.
8. **Метрика стоимости — `llm_cost_usd_total`, а не `..._USD_total`.**
   OTel-экспортёр Prometheus добавляет суффикс единицы; пришлось задать
   пустую unit, иначе имя стало бы `llm_cost_usd_USD_total`. Это
   зафиксировано комментарием в `app/observability.py` и проверено
   smoke-ом.
9. **FastAPI 0.162 включает нативную OTel-телеметрию по умолчанию.**
   Она создавала второй HTTP-спан и дублировала spanmetrics; отключена
   через `telemetry={"tracing": False, ...}`. Код адаптивный
   (`inspect.signature`), на старых FastAPI параметра нет — там нативной
   телеметрии и не было.
10. **`http.route` в FastAPI 0.162 берётся из effective-route-context.**
    `scope["route"].path` — исходный путь без префикса include_router;
    без обхода spanmetrics считал бы `/analyze`, а панель фильтрует
    `/v1/analyze`. Использован публично доступный в scope контекст с
    fallback на `route.path`/`scope["path"]`.
11. **Диаграммы рендерятся в общем `asyncio.to_thread` PDF-генерации.**
    При 3 диаграммах это +~0.1–0.3 с CPU; отдельный пул/кэш PNG не
    делали (один PDF — одна генерация). При росте нагрузки — кэш
    `(fen,last_move,size)`.
12. **Тест `test_data_isolation` исправлен по окружению.** Его
    `Settings(_env_file=None)` не читал `.env`, а skipif проверял
    `get_settings()`; на Windows это давало 500 `engine_not_found`.
    Теперь путь Stockfish переносится явно; поведение в CI не меняется.
13. **PDF без диаграммы неотличим от «cairo сломался».** В логах есть
    `board_renderer_unavailable`/`board_render_failed`, но в PDF нет
    пометки; для диагностики смотреть лог/`cairosvg_available()`.
14. **KI-030 не закрыт.** LoRA на GPU по-прежнему не обучалась.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| KI-053 | Low | admin, api | Фоновая джоба `export_jobs` живёт в процессе API (`BackgroundTasks`): не переживает рестарт, зависшие `running` не восстанавливаются, файлы/строки не чистятся автоматически. План: `arq`-очередь, идемпотентный запуск, recovery `running→failed` старше N, retention `EXPORT_DIR`/`export_jobs` |

**KI-050 закрыт** (фоновая выгрузка + checksum/размер + UI + audit
`job_id`). Сводка реестра: **53** записи, 25 Open, 28 Closed.

---

## 7. Обоснование

- **Почему `cairosvg`, а не `wand`/ImageMagick.** `chess.svg.board()`
  даёт точную SVG-доску с фигурами/координатами/подсветкой; `cairosvg`
  — единственный распространённый Python-рендерер SVG, ставящийся из
  pip. `wand` требует системного ImageMagick + политики, а
  `weasyprint` — GTK/Pango. `libcairo2` в Docker/CI — одна apt-строка,
  а на Windows без Cairo работает деградация. Альтернатива «рисовать
  доску Pillow» дала бы худшее качество и второй рендерер.
- **Почему деградация, а не 503.** PDF — пользовательская операция:
  отсутствие картинки не должно отменять весь документ. Ошибка
  рендерера логируется, а текст/ходы/комментарий остаются. Это
  соответствует DoD «не падать с 500».
- **Почему финальная позиция + критические моменты.** Финальная — всегда
  полезна и дёшева; критические (`blunder`/`mistake`) — то, ради чего
  читают разбор. Ограничение двумя: PDF не превращается в фотоальбом,
  генерация остаётся быстрой.
- **Почему UCI восстанавливается проигрыванием SAN.** В `moves` нет
  UCI, но есть SAN+FEN; один проход `chess.Board.parse_san` даёт
  подсветку последнего хода. Битый SAN/FEN просто оставляет диаграмму
  без подсветки — экспорт не падает.
- **Почему отдельный compose-файл, а не профиль основного.** Стек
  observability — опциональная инфраструктура; `docker compose -f
  docker-compose.observability.yml up -d` не тянет БД/Redis/Stockfish и
  не конфликтует с dev-проектом. Профиль в основном compose смешал бы
  жизненные циклы.
- **Почему collector + spanmetrics, а не Grafana Agent/Tempo
  metrics-generator.** Коллектор уже принимает OTLP и умеет
  spanmetrics-коннектор без отдельного хранилища; Prometheus скрейпит
  два endpoint-а. Tempo metrics-generator потребовал бы remote write и
  больше конфигурации. Для dev/CI это минимальный рабочий путь.
- **Почему метрики LLM — из `record_llm_call`, а не из спанов.** В
  спанах есть атрибуты, но конвертировать произвольные атрибуты в
  суммы токенов/стоимости коллектором сложно и хрупко. `record_llm_call`
  — единственная точка, где уже посчитаны tokens/cost/status для
  `llm_calls`; вызов `record_llm_metrics` рядом гарантирует совпадение
  Grafana и админ-дашборда.
- **Почему `llm.cost.usd` с пустой unit.** OTel→Prometheus добавляет
  суффикс единицы; `USD` дал бы `llm_cost_usd_USD_total`. Имя метрики
  уже содержит валюту, поэтому unit пустая — стабильное имя
  `llm_cost_usd_total` для панели.
- **Почему отключена нативная телеметрия FastAPI.** Два HTTP-спана на
  запрос (наш W3C-совместимый `http_request` и нативный FastAPI) дают
  дубли в spanmetrics и два trace id, ломая корреляцию `X-Trace-Id` ↔
  Tempo. Отключение сохраняет один источник истины — `TraceMiddleware`.
- **Почему `BackgroundTasks`, а не `arq` сразу.** `arq` уже в
  зависимостях, но требует Redis-очередь, worker-процесс, сериализацию
  фильтров и отдельный деплой; для MVP фоновая задача в API-процессе
  решает исходную проблему (браузер не ждёт стрим) минимальной ценой.
  Ограничение зафиксировано KI-053, а `run_export_job` идемпотентен по
  статусу — его можно перенести в arq без переписывания.
- **Почему `export_jobs` в БД, а не in-memory.** Статус должен пережить
  HTTP-запрос и быть видимым из админки/другого воркера; таблица даёт
  историю, audit `job_id` и основу для recovery.
- **Почему файл в `EXPORT_DIR` и atomic rename.** Запись во временный
  файл + `os.replace` гарантирует, что скачивание не увидит
  недописанный JSONL; `EXPORT_DIR` вне `web/dist` исключает раздачу
  статикой, а `resolve_export_path` защищает от подмены пути в БД.
- **Почему только admin создаёт джобы.** Это тяжёлая операция
  (сканирование `edits`, пакеты фактов, диск); auditor по RBAC read-only.
  Чтение/скачивание готовых файлов ему разрешено — это не мутация.
- **Почему 409/410 для download.** 409 — «ещё не готово/failed» (клиент
  может повторить), 410 — «файл был, но пропал/вне каталога» (повтор не
  поможет). 404 зарезервирован для несуществующей джобы.
- **Почему общий `app/dataset/records.py`.** Streaming и job обязаны
  выдавать одинаковый JSONL; дублирование фильтров/пропусков
  (`no_game`/`invalid_pgn`/`no_analysis`) почти гарантировало бы
  расхождение. Вынос сохраняет один источник правил.
- **Почему аудит в отдельной сессии и best-effort.** Джоба уже
  завершилась/отдала файл; сбой аудита не должен превращать успешную
  выгрузку в failed. `job_id`/`background` связывают запись с джобой.
