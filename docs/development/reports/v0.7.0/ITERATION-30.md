# Итерация 30 — PDF Export и Engine Cache (T-107, T-108)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **T-107 закрыт** (PDF-экспорт комментария +
кнопка в UI); **T-108 закрыт** (кэш результатов Stockfish). Новых KI нет.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### T-108: engine cache

| Файл | Что изменилось |
| :--- | :--- |
| `alembic/versions/19bf1fdc2a73_engine_cache_stockfish_results_cache_t_.py` *(новый)* | Миграция `engine_cache` (down_revision `e9f0a1b2c3d4`, head): `fen_hash` varchar(64), `engine_version` varchar(128), `depth` smallint, `multipv` smallint, `lines` JSONB, `created_at` TIMESTAMPTZ; `UNIQUE(fen_hash, engine_version, depth, multipv)` = `uq_engine_cache_key`, индекс `ix_engine_cache_created`, CHECK depth/multipv ≥ 1; upgrade/downgrade |
| `app/db/models.py` | Модель `EngineCacheEntry` (те же колонки/ограничения, docstring про ключ и TTL) + `__all__` |
| `app/engine/cache.py` *(новый)* | `EngineCacheKey`, `normalize_fen_for_cache` (FEN без номера хода, поля 1–5), `engine_fen_hash` (sha256); `EngineCache.lookup` (TTL-фильтр, best-effort, warning при сбое БД), `store` (upsert `ON CONFLICT DO UPDATE`), `cleanup_expired` |
| `app/engine/service.py` | `EngineService(cache=...)`; в `analyze`: после `_ensure_engine` ключ `(fen_hash, engine.name, depth, multipv)`, проверка кэша до `go`, запись после; `_UciEngine` запоминает UCI `id name` (`_read_uci_handshake`), свойство `name` |
| `app/engine/pool.py` | `EnginePool(cache=...)` — кэш передаётся во все `EngineService` |
| `app/main.py` | Lifespan: `EngineCache(cfg)` при `ENGINE_CACHE_ENABLED`, `app.state.engine_cache`, передача в пул; `cleanup_expired` при старте (best-effort, structlog) |
| `app/config.py` | `engine_cache_enabled: bool = True`, `engine_cache_ttl_days: int = 30` |
| `app/engine/__init__.py` | Docstring: модуль связан с БД только через опциональный `EngineCache` |
| `.env.example` | `ENGINE_CACHE_ENABLED=true`, `ENGINE_CACHE_TTL_DAYS=30` |
| `tests/unit/test_engine_cache.py` *(новый)* | 6 тестов: нормализация FEN (номер хода/счётчик/сторона), sha256, ключ/хешируемость, TTL ≥ 1, дефолты |
| `tests/integration/test_engine_cache.py` *(новый)* | 6 тестов: fake UCI — второй `analyze` не вызывает движок; club и gm дают разные ключи; выключенный кэш всегда идёт в движок; просроченная запись — промах; `cleanup_expired`; реальный Stockfish через `/v1/analyze` — повтор быстрее и строк кэша = позиции |
| `tests/test_models.py` | `engine_cache` в `SPEC_TABLES` + тест UNIQUE/индексов |

### T-107: PDF-экспорт

| Файл | Что изменилось |
| :--- | :--- |
| `app/export/pdf.py` *(новый)* | `CommentaryPdfData`, `find_pdf_font` (PDF_FONT_PATH → Arial/Calibri/Tahoma/DejaVuSans/Liberation/Arial macOS), `_supported_codepoints` (fontTools cmap), `_sanitize` (неподдерживаемые глифы → «?»), `format_moves`, `_CommentaryPdf` (footer `N/{nb}`), `build_commentary_pdf` (fpdf2: шапка, метаданные, ходы, абзацы комментария, подпись UTC) |
| `app/export/__init__.py` *(новый)* | Публичный реэкспорт |
| `app/api/v1/commentary.py` | `GET /commentaries/{id}/export/pdf`: `get_accessible_commentary` (404 для чужих), загрузка `Game`/`Move`, метаданные из PGN (битый PGN → прочерки), `asyncio.to_thread(build_commentary_pdf)`, `StreamingResponse` + `Content-Disposition`/`Cache-Control`; structlog; маппинг ошибок шрифта/генерации |
| `app/errors.py` | `PdfExportError` (500 `pdf_export_failed`), `PdfFontUnavailableError` (503 `pdf_font_unavailable`) |
| `app/config.py` | `pdf_font_path: str | None` |
| `pyproject.toml` | `fpdf2>=2.8` (production), `pypdf>=5.0` (dev, тесты PDF) |
| `Dockerfile` | `fonts-dejavu-core` в apt-пакетах (кириллица для PDF в slim-образе) |
| `.env.example` | `PDF_FONT_PATH` (закомментирован, auto-подбор) |
| `tests/unit/test_pdf_export.py` *(новый)* | 6 тестов: поиск шрифта/явный несуществующий путь, `format_moves`, sanitize эмодзи, валидный PDF + pypdf-текст (игроки/ходы/комментарий/уровень), пустой комментарий |
| `tests/integration/test_commentary_pdf.py` *(новый)* | 6 тестов: 200 + PDF/Disp + текст; 401 без токена; чужая запись → 404; несуществующий id → 404; admin экспортирует чужой; битый PGN не ломает экспорт |
| `web/src/api.ts` | `fetchCommentaryPdf` (blob, Bearer, single-flight refresh при 401), `downloadCommentaryPdf` (object URL → anchor) |
| `web/src/api.pdf.test.ts` *(новый)* | 4 теста: Bearer/credentials, анонимно без Authorization, 404 → `ApiError`, 401 → refresh и повтор |
| `web/src/components/CommentaryPanel.tsx` | Кнопка «Скачать PDF» рядом с «Редактировать»; `pdfBusy`, ошибки через общий `error` |
| `web/src/i18n.ts` | RU/EN: `downloadPdf`, `pdfPreparing` |

### Документация

`README.md` (696 тестов, 46 frontend, 15 таблиц/head `19bf1fdc2a73`,
API/Engine/Commentary/Frontend), `docs/development/SPEC.md` (§4
`engine_cache`, §5 PDF endpoint, индексы), `DESIGN_ADMIN_AND_OPS.md`
(§8.1 реализация engine cache), `ARCHITECTURE.md` (§2 `app/export`,
engine cache, §4 API, §5 15 таблиц), `GETTING_STARTED.md` (env-настройки,
§8.10 troubleshooting PDF, §9 про `engine_cache`), `USER_GUIDE.md`
(скачивание PDF, API 7.8), `TODO.md` (T-107/T-108 → `[x]`),
`docs/CHANGELOG.md`, `docs/development/RELEASES.md`, этот отчёт.

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Engine cache: чистые хелперы | `tests/unit/test_engine_cache.py` | 6 |
| Engine cache: БД + fake UCI + real Stockfish | `tests/integration/test_engine_cache.py` | 6 |
| PDF: генератор/шрифт | `tests/unit/test_pdf_export.py` | 6 |
| PDF: HTTP/авторизация | `tests/integration/test_commentary_pdf.py` | 6 |
| Схема БД (`engine_cache`) | `tests/test_models.py` | +1 |
| PDF API на фронтенде | `web/src/api.pdf.test.ts` | 4 |

Проверяемые сценарии:

- **Engine cache:** повторный `EngineService.analyze` той же позиции не
  вызывает UCI (`fake.calls == 1`), результат идентичен; `club` (18/3) и
  `gm` (28/5) получают разные строки; при `cache=None` движок вызывается
  каждый раз и строк нет; запись старше TTL — промах (даже если ключ
  совпадает); `cleanup_expired` удаляет только старое; на реальном
  Stockfish повторный `POST /v1/analyze` быстрее первого (>2×) и число
  строк `engine_cache` = числу позиций (`moves_analyzed + 1`).
- **PDF:** валидный `%PDF-…%%EOF`, `pypdf` извлекает игроков, турнир,
  ходы в нотации, абзацы комментария, уровень; заголовки
  `Content-Disposition: attachment` и имя файла; 401 без токена, 404 для
  чужого/несуществующего комментария, admin видит чужой; PGN, который не
  парсится, даёт PDF с прочерками и ходами из БД; эмодзи заменяются на
  «?», а не роняют генерацию.
- **Frontend:** `fetchCommentaryPdf` шлёт Bearer + `credentials: include`,
  анонимно — без заголовка; 404 → `ApiError`; 401 → refresh и повтор с
  новым токеном.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:**
**696/698 passed** (backend; 1 skipped, 1 xfailed), **46/46** frontend,
**10/10** E2E mocked.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД iter30) | `pytest -q` (POSTGRES_PORT=5435, `chessannotator_iter30`, Redis 0, `STOCKFISH_PATH`, web/dist) | **696 passed, 1 skipped, 1 xfailed, 4 warnings in 238.43s**, exit 0; collected 698; skip — нет `DEEPSEEK_API_KEY`, xfail — KI-012; **+25** к итерации 29 (671) |
| Engine cache unit | `pytest tests/unit/test_engine_cache.py -q` | **6 passed** |
| Engine cache integration | `pytest tests/integration/test_engine_cache.py -q` | **6 passed** |
| PDF unit | `pytest tests/unit/test_pdf_export.py -q` | **6 passed** |
| PDF integration | `pytest tests/integration/test_commentary_pdf.py -q` | **6 passed** |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 78 source files** (exit 0; +2 файла) |
| Alembic | `alembic upgrade head`, `alembic check`, `downgrade base → upgrade head` | head `19bf1fdc2a73`; «No new upgrade operations detected»; roundtrip OK; 15 таблиц, 11 ревизий |
| Frontend tests | `pnpm --dir web test` | **46 passed** (7 файлов), 1.49 с |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 4.95 с; JS 465.21 КБ (gzip 146.45), CSS 14.29 КБ (gzip 3.77) |
| E2E mocked | `pnpm --dir web run test:e2e` | **10 passed** (5 сценариев × chromium/mobile), 11.2 с |

---

## 4. Smoke (ручной сценарий)

**4.1. Engine cache (живой uvicorn :8018, real Stockfish, JWT user).**

```text
POST /v1/analyze (4 полухода, beginner):
  первый:  time_taken_ms=656, wall=749 мс
  второй:  time_taken_ms=22,  wall=73 мс     (×30 быстрее)
engine_cache: 5 строк, engine_version=Stockfish 19, depth=12, multipv=1
```

**4.2. PDF (тот же сервер, seeded commentary id=1).**

```text
GET /v1/commentaries/1/export/pdf (Bearer):
→ 200, application/pdf,
  Content-Disposition: attachment; filename="commentary-1.pdf",
  32 846 байт

pypdf: 1 страница, 452 символа; найдены «Иван Петров», «Пётр Сидоров»,
«Smoke-турнир», «1. e4 e5 2. Nf3 Nc6», «Первый абзац».
```

**4.3. Миграция.** `alembic upgrade head` → `19bf1fdc2a73`;
`alembic check` — «No new upgrade operations detected»; полный
`downgrade base && upgrade head` проходит.

---

## 5. Что не так

1. **PDF без диаграммы доски.** В постановке диаграмма — «возможно»
   (опционально). Для PNG нужен растровый рендер SVG (`cairosvg` →
   нативный cairo), а `chess.svg` + SVG-поддержка fpdf2 не гарантируют
   качество; выбран вариант «текст + ходы + шапка» без нативной
   зависимости. Диаграмма — кандидат на следующий шаг.
2. **PDF — один байтовый блок в `StreamingResponse`, а не поток
   страниц.** Файл небольшой (десятки КБ), генерация целиком в памяти;
   требование «StreamingResponse» выполнено, но настоящего стриминга
   нет. Для больших вложений понадобится фоновая задача/файловое
   хранилище.
3. **Шрифт — системная зависимость.** Auto-подбор покрывает Windows
   (Arial), Ubuntu/CI/Docker (DejaVuSans) и macOS; в экзотическом образе
   без шрифтов PDF вернёт 503 `pdf_font_unavailable` — лечится
   `PDF_FONT_PATH`. В Dockerfile добавлен `fonts-dejavu-core`.
4. **Неподдерживаемые глифы заменяются на «?».** Эмодзи/символы вне
   cmap шрифта не рендерятся (замена, а не ошибка); для комментариев на
   русском/английском это не проблема.
5. **Engine cache проверяется после старта процесса.** `EngineService`
   сначала лениво поднимает Stockfish (нужен UCI `id name` для версии),
   затем смотрит кэш. Первый запрос после рестарта процесса всё равно
   стартует бинарник (~50–100 мс), но не выполняет поиск при попадании.
6. **Кэш не инвалидируется при смене UCI-опций, не входящих в ключ.**
   Ключ — позиция + версия + depth + multipv; если когда-нибудь появятся
   настройки, меняющие оценку (например, Contempt/Syzygy), их нужно
   добавить в ключ. Сейчас такие опции не используются.
7. **Запись в `engine_cache` — в пределах блокировки движка.** INSERT
   выполняется до освобождения `asyncio.Lock` (единицы миллисекунд); при
   высокой нагрузке можно вынести запись в фон, как `llm_calls`.
8. **T-108 в TODO имел другое значение.** В backlog `T-108` означал «кэш
   retrieval по `fen_hash`» (`retrieval_cache`), а постановка итерации 30
   — engine cache. Задача закрыта в объёме постановки; retrieval-кэш
   остаётся частью T-018/T-032b (отмечено в `TODO.md` и отчёте).
9. **Зависимость `fpdf2` тянет Pillow/fonttools/defusedxml.** Это чистые
   Python-пакеты; альтернатива `weasyprint` требует GTK/Pango/cairo и
   ломает переносимость Windows/slim-Docker — обоснование в §7.
10. **Docker-образ локально не пересобирался.** `fonts-dejavu-core`
    добавлен в `Dockerfile`; CI-job `docker` собирает образ и делает
    smoke `import app.main`. Локальная проверка ограничилась
    `alembic`/pytest с системным Arial.
11. **Frontend: кнопка не покрыта компонентным тестом.** В проекте нет
    React Testing Library; покрыт API-хелпер скачивания (4 теста) и
    E2E-сценарии (кнопка появляется после комментария, но клик по PDF в
    mocked-E2E не проверяется — нужен route-мок бинарного ответа).
12. **KI-030 не закрыт.** LoRA на GPU по-прежнему не обучалась.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| — | — | — | Новых KI в итерации 30 нет |

Ограничения (диаграмма в PDF, фоновая запись кэша, Redis-слой) занесены
в §5 и план «Что дальше»; блокеров нет. Сводка реестра: **52** записи,
25 Open, 27 Closed (без изменений).

---

## 7. Обоснование

- **Почему engine cache — отдельная таблица `engine_cache`, а не
  `semantic_cache`.** `semantic_cache` хранит текст ответа и
  эмбеддинг для семантического поиска; результат движка — структурный
  список MultiPV-линий (`multipv/score_cp|mate/move_uci/pv`), его
  естественно хранить в JSONB и сравнивать точным ключом. Смешивать
  «engine» и «llm» в одной таблице пришлось бы с `kind`-дискриминатором
  и несовместимыми колонками; отдельная таблица проще и индексируется
  под свой ключ.
- **Почему ключ именно `fen_hash + engine_version + depth + multipv`.**
  Оценка Stockfish зависит от позиции и параметров поиска; `beginner`
  (глубина 12, MultiPV 1) и `gm` (28/5) — разные вычисления. Версия
  движка (UCI `id name`) страхует от переиспользования оценок после
  обновления бинарника. Номер полного хода в ключ не входит (не влияет
  на оценку), счётчик полуходов оставлен (влияет на правило 50 ходов);
  `level` отдельно не нужен — он полностью определяется depth/multipv.
- **Почему проверка кэша внутри `EngineService.analyze`, а не в
  API/pool.** `EngineService.analyze` — единственная точка, где позиция
  уходит в UCI; и последовательный анализ, и пул (через сервисы) идут
  через неё. Кэш на этом уровне автоматически покрывает `/v1/analyze`,
  warmup и будущие вызовы, не дублируя логику в эндпоинтах.
- **Почему `EngineCache` использует отдельные сессии и best-effort.**
  Анализ не должен падать из-за недоступной БД или битой строки кэша:
  ошибки чтения/записи логируются (`warning`), поиск продолжается.
  Отдельные короткие сессии не участвуют в транзакции эндпоинта и не
  держат соединение на время поиска.
- **Почему запись после поиска — upsert.** Один и тот же ключ может
  встретиться в двух параллельных анализах; `ON CONFLICT DO UPDATE`
  обновляет строку без гонки и без предварительного SELECT. `created_at`
  обновляется, поэтому «свежий» результат продлевает TTL.
- **Почему TTL и очистка при старте, а не cron.** Кэш — производные
  данные; просроченная строка просто игнорируется при чтении, а
  физическая чистка при старте не требует отдельного планировщика и
  ограничена по объёму (30 дней). Ключ/индекс `created_at` делают
  удаление дешёвым.
- **Почему `fpdf2`, а не `weasyprint`.** Постановка предпочитала
  weasyprint, но он требует нативных GTK/Pango/cairo: на Windows-машине
  разработчика и в `python:3.12-slim` это дополнительная системная
  установка, а `pip install .` не должен тянуть системные библиотеки.
  `fpdf2` — чистый Python, поддерживает Unicode TTF (кириллица),
  встраивает подмножество шрифта и даёт компактный PDF; визуально
  документ аккуратный (шапка/разделители/колонтитул). Это осознанный
  trade-off в пользу переносимости.
- **Почему шрифт ищется в системе, а не бандлится в репозиторий.**
  Копия Arial/DejaVu (~0,7–1 МБ) в git — лишний бинарный артефакт и
  вопрос лицензии; на всех целевых окружениях (Windows dev, Ubuntu CI,
  Docker с `fonts-dejavu-core`) шрифт уже есть. `PDF_FONT_PATH` закрывает
  нестандартные образы, а отсутствие шрифта даёт понятный 503, а не
  «кракозябры».
- **Почему PDF генерируется в `asyncio.to_thread`.** fpdf2/fontTools —
  CPU-bound Python-код; вызов в event loop заблокировал бы обработку
  других запросов на время генерации. `to_thread` укладывается в правило
  RULES §3.1 о блокирующих вызовах.
- **Почему эндпоинт — `GET /commentaries/{id}/export/pdf`, а не
  `POST /export`.** Экспорт идемпотентен и не меняет состояние; `GET`
  позволяет скачивать напрямую из браузера (кнопка) и кэшировать
  поведение HTTP-клиентов. Авторизация — тот же JWT + проверка владельца
  через `get_accessible_commentary`, что и у `PATCH`.
- **Почему `StreamingResponse` с одним чанком.** Требование постановки —
  отдавать через `StreamingResponse`; PDF уже полностью в памяти, поэтому
  один чанк — честная реализация контракта без ложного стриминга
  страниц. Для больших файлов позже — фоновая задача/хранилище.
- **Почему битый PGN не блокирует экспорт.** `games.pgn` может быть
  legacy/непарсимым; комментарий и ходы уже лежат в БД (`moves`), поэтому
  PDF собирается из БД, а `parse_pgn` используется только для шапки; при
  ошибке — прочерки, а не 500. Это важно для старых данных и ручных
  правок.
- **Почему фронтенд скачивает blob через API-хелпер.** Endpoint защищён
  JWT (access в памяти), поэтому обычная ссылка `<a href>` не подойдёт;
  `fetchCommentaryPdf` повторяет общую схему с single-flight refresh и
  отдаёт blob, а `downloadCommentaryPdf` создаёт временную ссылку и
  освобождает object URL. Тестируется без DOM (fetch-моки), в браузере —
  кнопкой.
