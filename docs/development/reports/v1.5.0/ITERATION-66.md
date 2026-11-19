# Итерация 66 — Cache Hardening: KI-034 + T-018 (v1.2.0)

**Дата:** 2026-11-19
**Статус:** ✅ завершена — утечка контекста чужих партий устранена,
`retrieval_cache` активирован, отчёт по RULES §2.18 (7 разделов).
**Ветка:** `iter/66-cache-hardening` (от `main` @ `d280042`).
**Коммиты:** `27290dd` (cache), `2fd2c9a` (RAG), `6b5c2d3` (docs),
`a73a1fb` (fix RAG).

**Исходное состояние.** `semantic_cache` искал ответ по FEN позиции и
`prompt_hash(level, model, system_prompt, style/question/history)`. Ходов
партии в ключе не было: две партии, пришедшие в одну позицию, могли получить
один и тот же текст (KI-034). Таблица `retrieval_cache` присутствовала в
схеме с первой миграции, но не использовалась — RAG каждый раз считал
эмбеддинг и делал векторный поиск (T-018).

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `app/rag/cache.py` | Слой чтения/записи `retrieval_cache`: `retrieval_fen_hash`, `RetrievalCache.lookup/store/cleanup_expired`, TTL и graceful degradation |
| `tests/unit/test_cache_isolation.py` | Unit-тесты KI-034: транспозиции, `analysis_hash`, `prompt_hash`, exact-only lookup, флаг `use_cache` в схемах API |
| `tests/unit/test_retrieval_cache.py` | Unit-тесты T-018: нормализация FEN, TTL, выключенный кэш, отказ БД, пустые id |
| `tests/integration/test_commentary_cache_isolation.py` | Интеграция KI-034: две партии с одинаковым FEN получают разные комментарии; `use_cache=false` обходит кэш |
| `tests/integration/test_retrieval_cache.py` | Интеграция T-018: miss → запись `top_k_ids` → hit без вызова `embedder`, TTL, club/gm `top_k` |
| `docs/development/reports/v1.5.0/ITERATION-66.md` | Этот отчёт |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/cache/semantic.py` | `analysis_hash(parsed, analyses)`, пятое поле в `make_prompt_hash`, флаг `allow_semantic` в `lookup()` |
| `app/cache/__init__.py` | Реэкспорт `analysis_hash` |
| `app/commentary/service.py`, `app/commentary/streaming.py` | Ключ с `analysis_hash`, точный поиск, `use_cache`, RAG-контекст с уровнем |
| `app/chat/service.py`, `app/chat/streaming.py` | Флаг `use_cache` (обход чтения и записи) |
| `app/api/v1/commentary.py`, `app/api/v1/chat.py` | Поле `use_cache: bool = True` в теле запроса; club-RAG только при `RAG_CLUB_ENABLED` |
| `app/config.py` | `rag_club_enabled`, `rag_club_top_k`, `retrieval_cache_enabled`, `retrieval_cache_ttl_days` |
| `app/rag/service.py` | `find_similar_positions(..., cache=...)`: чтение аннотаций по `top_k_ids`, запись после векторного поиска, `_load_by_ids` |
| `app/rag/context.py` | Уровень в билдере, gm/club `top_k`, передача кэша |
| `app/rag/__init__.py`, `app/main.py` | Реэкспорт, best-effort чистка `retrieval_cache` при старте |
| `.env.example` | Новые переменные RAG/retrieval-cache и комментарий про KI-034 |
| `tests/integration/test_rag.py` | Фейк RAG-билдера обновлён под 3-аргументный вызов |
| `docs/KNOWN_ISSUES.md` | KI-034 → Closed, реестр и история обновлены |
| `TODO.md` | T-018 и T-032b → `[x]` с фактическим статусом; уточнён остаток T-032 |
| `docs/CHANGELOG.md` | `Added` (retrieval_cache), `Fixed` (KI-034) в `[Unreleased]` |
| `docs/development/SPEC.md` | §2.3: retrieval-кэш и уровни top_k; §4: ключ semantic cache с `analysis_hash` |

**Не трогались:** `.gitignore` и `docs/development/reports/v1.2.0/ITERATION-66-INFRA.md`
(параллельная инфраструктурная работа) и `GPU_TRAINING_GUIDE.md`.
Предшествующие правки RoadMap в `TODO.md`/`CHANGELOG.md`/`SPEC.md`
сохранены: наши изменения вносились точечными правками и вошли в
docs-коммит вместе с ними.

---

## 2. Покрытие

| Слой | Тест | Что проверено |
| :--- | :--- | :--- |
| Unit | `test_cache_isolation.py` (6) | Две транспозиции с одинаковым финальным FEN дают разные `analysis_hash`; хеш детерминирован и не зависит от метаданных; `prompt_hash` меняется; exact-only lookup не вызывает эмбеддер; семантический lookup его вызывает; поля `use_cache` в схемах API |
| Unit | `test_retrieval_cache.py` (5) | FEN без номера хода; TTL ≥ 1; выключенный кэш не трогает БД; отказ БД → `None`/без исключения; пустые id не пишутся |
| Integration | `test_commentary_cache_isolation.py` (2) | Две партии с идентичным FEN → разные тексты, `cached=False`, `llm.calls == 2`; `use_cache=False` не создаёт строк в `semantic_cache` |
| Integration | `test_retrieval_cache.py` (5) | miss → `top_k_ids` → hit без вызова `embedder`; устаревшая запись — miss; `cleanup_expired`; club `top_k=1`, gm `top_k=3`, beginner — без RAG; club выключен по умолчанию |
| Регресс | `tests/integration/test_rag.py::test_gm_commentary_includes_rag_context` | gm видит RAG, club — нет при дефолтном `RAG_CLUB_ENABLED=false` |

Новых тестов: **18** (6 + 5 + 2 + 5). Интеграционные требуют PostgreSQL;
для проверки в этой итерации БД поднята через `docker compose up -d postgres
redis`, схема — `alembic upgrade head`.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** 1487 passed, 17 skipped, 1 xfailed, 6 warnings
(138.65 с, exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Новые | `pytest -q <4 файла>` | `18 passed` (exit 0) |
| Полная сюита | `pytest -q` | `1487 passed, 17 skipped, 1 xfailed, 6 warnings in 138.65 s` (exit 0) |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 92 source files` |
| Схема | `alembic upgrade head && alembic check` | `No new upgrade operations detected.` (миграция не нужна) |

**Миграции.** Изменений схемы нет: `analysis_hash` встроен в существующее
поле `prompt_hash` (String(64)), `retrieval_cache` уже содержит `fen_hash`,
`top_k_ids`, `created_at`. Поэтому правило RULES §5 (upgrade → downgrade →
upgrade) не применялось; `alembic check` подтверждает отсутствие diff.

---

## 4. Smoke

Сценарий 1 (KI-034, integration):

```text
PGN A: 1. e4 e5 2. Nf3 Nc6 3. Bb5 a6
PGN B: 1. Nf3 Nc6 2. e4 e5 3. Bb5 a6
final FEN: r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4
first.cached=False, second.cached=False, llm.calls=2
first.text="Текст партии A.", second.text="Текст партии B."
```

Сценарий 2 (T-018, integration):

```text
cache.lookup(FEN) → None       # miss
find_similar_positions(...)    # embedder.calls == 1, сохранены top_k_ids
cache.lookup(FEN) → [id, id]   # hit
find_similar_positions(...)    # embedder.calls == 0, текст тот же
UPDATE created_at -= 31 day    # cache.lookup(FEN) → None
```

Сценарий 3 (CLI-проверки): `ruff check .`, `mypy app`, `alembic check` —
все exit 0; `docker compose ps` — postgres/redis healthy.

---

## 5. Что не так

- **Векторный fallback в чате оставлен включённым.** Он ограничен точным
  `prompt_hash` (включает `selected_ply`, вопрос и историю), но формально
  остаётся эвристикой; полное отключение — отдельное решение продукта.
- **Redis hot-слой и hit rate в админке/OTel из T-032b не сделаны.** Это
  оптимизация и метрики, а не изоляция данных; KI-034 от них не зависит.
  В `TODO.md` остаток помечен явно.
- **Club-RAG выключен по умолчанию** (`RAG_CLUB_ENABLED=false`) — текущая
  матрица уровней и тесты не меняются; включение требует явной настройки.
- **Интеграционные тесты зависят от PostgreSQL.** Без БД они `skip`
  (существующая практика проекта); в этой итерации БД поднята локально.
- **Число skipped зависит от окружения.** С поднятыми Postgres/Redis
  пропусков 17 (LM Studio/libcairo2); без Redis их было 41. Live-тесты
  LLM на этой машине не запускались; acceptance-проверки (новые тесты,
  ruff, mypy, alembic) — зелёные в обоих конфигурациях.
- **Параллельные правки в рабочей копии** (RoadMap, `.gitignore`, infra-отчёт)
  не являются частью этой итерации и не были перезаписаны.

---

## 6. Новые KI

**Нет.** KI-034 закрыт, T-018 и T-032b (в части изоляции и `use_cache`)
закрыты. Остатки T-032b (Redis hot-слой, hit rate) зафиксированы в `TODO.md`
как отдельная задача, а не как новая known issue.

---

## 7. Обоснование

- **Почему `analysis_hash` внутри `prompt_hash`, а не новая колонка БД.**
  Требование KI-034 прямо говорит «включить в расчёт `prompt_hash`». Это
  даёт полный ключ `fen_hash + analysis_hash + level + prompt_hash` без
  миграции: уникальное ограничение `uq_semantic_cache_key` остаётся
  корректным, старые записи просто перестают совпадать по хешу (безопасный
  naturally-expire).
- **Почему exact-only для целой партии.** Даже при совпадении `analysis_hash`
  векторный поиск мог бы вернуть текст из другой записи с близким FEN. Для
  комментария это неприемлемо: RULES §8.1 запрещает выдуманные ходы, а самый
  короткий путь к ним — чужой текст. Чат — другой случай: вопрос/история
  уже часть ключа, поэтому там fallback сохранён.
- **Почему `use_cache` в теле запроса, а не query-параметр.** Все четыре
  эндпоинта уже принимают Pydantic-модель; поле с дефолтом `True` обратно
  совместимо и единообразно для обычного и SSE-вызова. `cache=off`
  реализован как `use_cache=false`.
- **Почему `RetrievalCache` mirrors `EngineCache`.** Та же схема best-effort:
  отдельные сессии, TTL из Settings, structlog-warning вместо исключения.
  Это сохраняет главное свойство RAG — сбой кэша не ломает генерацию.
- **Почему DELETE + INSERT вместо `on_conflict_do_update`.** У
  `retrieval_cache` нет уникального ограничения на `fen_hash` (только
  индекс), а менять схему ради кэша нецелесообразно: last-write-wins в
  транзакции даёт ту же семантику без миграции.
- **Почему club-RAG под флагом.** SPEC §3 описывает club как «средний»
  retrieval, но включение по умолчанию изменило бы промпты и существующие
  тесты. `RAG_CLUB_ENABLED=false` + `rag_club_top_k` дают управляемый
  переход.
