# Итерация 35 — Final Polish & v1.0.0 Documentation (KI-058, UX, Docs)

**Дата:** 2026-11-01
**Статус:** ✅ завершена. Версия проекта — **1.0.0**.
**Формат:** отчёт по фазе, RULES §2.18 (7 разделов).

Итерация финальной полировки перед релизом v1.0.0: расширение английской
локализации (KI-058: PDF, чат, ошибки API), два UX-хвоста (плейсхолдер
`/history` для неподтверждённых, фильтр `export_jobs` в админке), подготовка
инструмента слепого теста (T-024) и полная переработка документации под
v1.0.0.

---

## 1. Файлы (+/-)

Итого: **37 изменённых файлов, 8 новых** (включая этот отчёт).

### 1.1. Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `app/i18n.py` | Карта `code → {ru, en}` (14 кодов) + `localize_error_detail(code, default, accept_language)`; `internal_error` не переводится |
| `app/prompts/chat_en.txt` | Английский системный промпт чата |
| `scripts/generate_blind_test.py` | Генератор слепого теста T-024: 3 партии × 3 уровня, слоты A/B/C (seeded), 9 `.txt`, `answer_form.md`, `answers_key.json`; `--dry-run/--seed/--game-ids/--language` |
| `tests/unit/test_i18n.py` | Юнит-тесты карты ошибок и выбора языка |
| `tests/integration/test_error_i18n.py` | Интеграционные тесты локализации `AppError` по `Accept-Language` |
| `tests/integration/test_admin_export_jobs.py` | 8 тестов read-only админ-вью `export_jobs` и фильтра по статусу |
| `tests/unit/test_generate_blind_test.py` | 27 тестов генератора (перестановки, детерминизм, анонимность, CLI) |
| `docs/development/reports/v1.0.0/ITERATION-35.md` | Этот отчёт |

### 1.2. Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/export/pdf.py` | `PdfLanguage`, `_PdfLabels`: язык подписей (заголовки, метаданные, уровни, статусы, футер); `build_commentary_pdf(..., language=)`; RU-вывод не изменился |
| `app/export/board.py` | Локализация подписей диаграмм (финальная позиция, критические моменты), SAN/номера не переводятся |
| `app/api/v1/commentary.py` | PDF-эндпоинт принимает `language` (query → `Accept-Language` → `DEFAULT_LANGUAGE`) |
| `app/api/v1/chat.py` | `ChatRequest.language`; тот же приоритет выбора языка для `/v1/chat` и `/v1/chat/stream` |
| `app/chat/service.py` | `load_chat_system_prompt(language)`, `answer_question(..., language=)`, английские заголовки промпта и retry-подсказка; `prompt_hash` учитывает выбранный системный промпт |
| `app/chat/streaming.py` | `stream_chat(..., language=)`, локализация SSE-ошибок, `language` в SSE `meta` |
| `app/main.py` | `_app_error_handler` использует `localize_error_detail` с `Accept-Language` |
| `app/admin.py` | `ExportJobAdmin(_ReadOnlyModelView)`: поля, `EnumField("status")`, `searchable_fields=(status, actor)`, `exclude_fields_from_list=(filters, sha256)`; регистрация в `create_admin` |
| `app/__init__.py`, `pyproject.toml`, `web/package.json` | Версия `1.0.0` |
| `web/src/api.ts`, `api.pdf.test.ts`, `api.auth.test.ts` | Передача нормализованного `language` в PDF/чат; тесты |
| `web/src/components/{CommentaryPanel,ChatPanel}.tsx` | Передача `i18n.language` |
| `web/src/components/AuthGate.tsx` | Проп `allowUnverified` (по умолчанию `false`) |
| `web/src/App.tsx` | `/history` рендерится через `<AuthGate allowUnverified>` |
| `web/src/pages/HistoryPage.tsx` | Плейсхолдер для неподтверждённых: «Подтвердите email, чтобы увидеть историю», кнопки «Проверить»/«Выйти», `listGames` не вызывается |
| `web/src/i18n.ts` | Ключи RU/EN для плейсхолдера истории |
| `web/e2e/history.spec.ts` | Сценарий неподтверждённого пользователя (placeholder, нет запросов `/v1/games`) |
| `tests/unit/{test_pdf_export,test_pdf_board,test_prompts,test_chat_history}.py` | Тесты EN-подписей PDF, диаграмм, промптов, чата |
| `tests/integration/{test_commentary_pdf,test_api_chat,test_streaming}.py` | Тесты `language` для PDF/чата/SSE |
| `README.md` | Переписан под v1.0.0: бейджи, возможности, быстрый старт (dev/prod/observability compose), статус-таблица, метрики |
| `docs/CHANGELOG.md` | `[1.0.0] - 2026-11-01`: весь `[Unreleased]` сгруппирован в Added/Changed/Fixed/Security, дубликаты убраны |
| `docs/development/ARCHITECTURE.md` | C4 L1/L2 (SPA, API, ARQ worker, PostgreSQL+pgvector, Redis, Stockfish, LLM, OTel → Prometheus/Tempo → Grafana), §5.4 локализация, актуализация API/тестов/развёртывания |
| `docs/development/GETTING_STARTED.md` | §4.2 «Запуск тестов и E2E локально», проверенные команды, исправления устаревших инструкций |
| `docs/development/SPEC.md` | `language` для chat/PDF, поведение локализации ошибок |
| `docs/README.md` | Отчёт v1.0.0 в индексе документации |
| `docs/KNOWN_ISSUES.md` | KI-058 → **Closed** с остатками (a/b/c); сводка 25 Open / 33 Closed |
| `TODO.md` | T-024 `[x]` («скрипт готов, живой тест вручную»), отметки итерации 35 по T-027/T-034b/T-045/T-106/T-107 |
| `docs/development/RELEASES.md` | Запись итерации 35 / v1.0.0 с финальными метриками |

---

## 2. Проверки и результаты

Полный прогон — на изолированной БД `chessannotator_iter35`
(PostgreSQL `127.0.0.1:5435`, Redis `127.0.0.1:6382/0`,
`STOCKFISH_PATH=…stockfish-windows-x86-64-universal.exe`), схема — `alembic upgrade head`.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита | `pytest -q` | **869 passed, 3 skipped, 1 xfailed in 338.69 s** (exit 0); 77 файлов. Skip: 2 cairo-кейса (Windows-dev) + `test_llm_live` (`DEEPSEEK_API_KEY` не задан); xfail — KI-012 |
| Линтер | `ruff check .` | All checks passed (exit 0) |
| Типы | `mypy app` | Success: no issues found in **85** source files |
| Миграции | `alembic check` | No new upgrade operations detected; head `dcf1da1421cd`, 13 ревизий, 16 таблиц |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend-тесты | `pnpm --dir web test` | **58 passed** (8 файлов) |
| Frontend build | `pnpm --dir web build` | **6.74 s**; JS 483.63 КБ (gzip 150.45), CSS 15.70 КБ (gzip 4.09) |
| E2E mocked | `E2E_PREVIEW=1 pnpm --dir web run test:e2e` | **30 passed** (15 сценариев × chromium/mobile), 21.5 s |
| Слепой тест (dry-run) | `python scripts/generate_blind_test.py --dry-run --game-ids …` | exit 0; 9 `.txt` + `answer_form.md` + `answers_key.json`; выбор «3 последние партии» проверен на временно засеянных данных (очищены) |
| Mermaid | парсер mermaid@11 + jsdom | 2/2 диаграммы ARCHITECTURE валидны |

Дополнительно по рабочим потокам (до финального прогона):

| Поток | Проверка | Результат |
| :--- | :--- | :--- |
| KI-058 (backend-l10n) | targeted unit / integration / regression | 79 passed + 1 skipped / 35 passed + 1 skipped / 94 passed; frontend typecheck 0, Vitest 58 |
| UX (web-ux) | typecheck, Vitest, E2E | 0 ошибок, 55 (на тот момент) passed, E2E 30 passed |
| Admin + blind test (admin-blindtest) | `pytest tests/unit/test_generate_blind_test.py tests/integration/test_admin_export_jobs.py tests/integration/test_admin.py` | **65 passed** (27 + 8 + 30); ruff/mypy чисто |

`pip-audit` не запускался: зависимости в итерации не менялись.

---

## 3. Отклонения от плана

| Пункт плана | Фактически | Причина |
| :--- | :--- | :--- |
| `language` для PDF | Query-параметр `?language=ru|en` у `GET /export/pdf`, приоритет: query → `Accept-Language` → `DEFAULT_LANGUAGE` | GET-эндпоинт без тела; переиспользован `resolve_language` |
| Английские заголовки PDF | «Game», «Moves», «Commentary», «Positions on the board» + метаданные/уровни/статусы/футер; RU-текст не изменился | Соответствует формулировке задачи; проверено тестами |
| Язык чата | Также `language` в SSE `meta`; фронтенд передаёт `i18n.language` | Единый выбор языка на клиенте |
| Локализация ошибок | 14 кодов в `app/i18n.py`; ошибки, которые middleware (`finops`, `ratelimit`) отдают напрямую (не `AppError`), оставлены как были | Не менять поведение вне scope; `code` стабилен |
| Плейсхолдер `/history` | Реализован через `AuthGate allowUnverified` — страница рендерится и сама показывает заглушку | Меньше дублирования; `listGames` не вызывается |
| Фильтр `export_jobs` | URL `/admin/export-job/list` (slugify), фильтр — строка `?filter=status__eq=failed`, multi `status__in=done,failed`; выпадающий список через `EnumField` | starlette-admin 1.0.3 использует Django-подобную строку фильтра, а не JSON |
| Blind-test script | Не чистит устаревшие файлы в `--output-dir`; реальный LLM-провайдер в этой среде не прогонялся (только инъекция фейка) | Задокументировано в docstring/README скрипта |
| CHANGELOG | 2 дублирующихся пункта объединены, дубль `### Changed` убран | Чистота структуры Keep a Changelog |
| ARCHITECTURE | C4 L1/L2 выполнены Mermaid `flowchart` (C4-плагин не рендерится GitHub) | Совместимость с GitHub |
| CI-бейдж README | URL исправлен на фактический remote `iilmchat/ai-chess-annotator` | Старый бейдж ссылался на несуществующий путь |
| Docker build / GHCR push | Не выполнялись | Действие мейнтейнера по тегу `v*` (T-027) |

---

## 4. Риски и блокеры

**Блокеров нет.** Все запланированные работы выполнены, тесты зелёные.

| Риск | Влияние | Митигация |
| :--- | :--- | :--- |
| EN-промпты и тексты не вычитаны носителем | Стилистические огрехи в английском | Остаток KI-058 (c); функционально покрыто тестами |
| Живой слепой тест не проведён | Нет валидации уровней на людях | T-024: инструмент и бланк готовы; тест — вручную |
| E2E только mocked | Real-backend сценарии не перепрогонялись (последний — итерация 32) | KI-040; mocked-режим покрывает UI-потоки |
| Live-LLM тесты пропущены | Нет проверки реального провайдера в этой среде | `DEEPSEEK_API_KEY` не задан; путь провайдера покрыт фейками |
| Зависимость от поведения starlette-admin 1.0.3 | Формат фильтра может отличаться в других версиях | Версия зафиксирована; интеграционные тесты на фильтр |
| Тестовая БД на нестандартных портах | Воспроизводимость на другой машине | Команды и env зафиксированы в отчёте и GETTING_STARTED §4.2 |

---

## 5. Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Живой слепой тест уровней (10 шахматистов, ≥ 80%) | Нужны реальные участники; инструмент готов | T-024 |
| Тег `v1.0.0` и публикация в GHCR | Действие мейнтейнера: `git tag v1.0.0 && git push origin v1.0.0`; workflow готов | T-027 |
| Docker build в этой сессии | Не подтверждён локально; CI-job `docker` собирает образ и делает smoke | CI |
| Вычитка EN носителем | Нет native speaker'а в команде | KI-058 (residual c) |
| `prompts.language` в БД (en-промпты в админке) | Требует миграции и UI; в v1.0.0 en — файлами | KI-058 (residual b) |
| Русский факт-пакет для en-генерации | Отдельный объём перевода фактов; LLM пересказывает ru-пакет | KI-058 (residual a) |
| Локализация ошибок middleware (`token_budget_exceeded`, `rate_limited`) | Middleware отвечают напрямую, не через `AppError` | SPEC §5 |
| LoRA-обучение | Нет GPU | KI-030 |
| Signed URL экспортов | Требует дизайна TTL/scope | KI-057 |

---

## 6. Что дальше

1. **Релиз v1.0.0:** создать тег `v1.0.0`, проверить job `publish-docker`,
   сделать GHCR-пакет публичным (T-027).
2. **T-024:** провести слепой тест на 10 шахматистах по готовому набору
   (`scripts/generate_blind_test.py`), собрать бланки, проверить порог ≥ 80%.
3. **KI-058 (остатки):** `prompts.language` + вычитка EN носителем.
4. **Технический долг:** T-111 (multi-tenant/RBAC), T-114 (PII-редакция),
   KI-057 (signed URL), KI-030 (LoRA, при появлении GPU).
5. **Следующая итерация:** планирование post-1.0.0 бэклога (S3-хранилище,
   TCP-Stockfish KI-013, real-backend E2E KI-040).

---

## 7. Итог

**Definition of Done — выполнен:**

- [x] `language=en` работает для PDF и чата, покрыто тестами
      (unit + integration, включая приоритет query над заголовком).
- [x] Ошибки API локализуются по `Accept-Language` при неизменных
      `code`/HTTP-статусе; `internal_error` не переводится.
- [x] `/history` показывает заглушку неподтверждённым пользователям
      (RU/EN, E2E-сценарий, без запросов `/v1/games`).
- [x] В админке есть read-only `export_jobs` с фильтром по статусу
      (`queued/running/done/failed`), проверено интеграционными тестами.
- [x] `scripts/generate_blind_test.py` формирует полный набор файлов
      (9 анонимизированных партий + бланк + ключ), dry-run проверен.
- [x] Документация v1.0.0 обновлена и достоверна: README, CHANGELOG
      (`[1.0.0] - 2026-11-01`), ARCHITECTURE, GETTING_STARTED, docs/README,
      KNOWN_ISSUES (KI-058 Closed), TODO, RELEASES.
- [x] Все проверки зелёные: backend **869 passed / 3 skipped / 1 xfailed**,
      frontend **58**, E2E **30**, ruff 0, mypy 0 (85 файлов), alembic — без
      новых операций.

**Вердикт:** итерация 35 завершена, проект **v1.0.0** готов к тегированию
и публикации.
