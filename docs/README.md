# Документация ChessAnnotator

Навигационный хаб. Здесь — что читать в каком порядке и что ещё не написано.
Правило: каждый новый документ попадает в этот индекс **в том же коммите**,
где он создан (`RULES.md` §5).

---

## С чего начать

| Роль | Порядок чтения |
| :--- | :--- |
| **Новый разработчик** | [README.md](../README.md) → [GETTING_STARTED.md](development/GETTING_STARTED.md) → [SPEC.md](development/SPEC.md) → [RULES.md](development/RULES.md) → [TODO.md](../TODO.md) |
| **AI-ассистент (DSH)** | [PROMPT_CURRENT.md](development/PROMPT_CURRENT.md) → [RULES.md](development/RULES.md) → [SPEC.md](development/SPEC.md) → [TODO.md](../TODO.md) → [KNOWN_ISSUES.md](KNOWN_ISSUES.md) |
| **Ревьюер** | [RULES.md](development/RULES.md) §10 (чек-лист) → [RELEASES.md](development/RELEASES.md) → отчёты итерации |
| **Продукт / заказчик** | [SPEC.md](development/SPEC.md) §1–§3, §9 → [CHANGELOG.md](CHANGELOG.md) → [KNOWN_ISSUES.md](KNOWN_ISSUES.md) |

---

## Существующие документы

### Продукт и архитектура

| Документ | Что внутри |
| :--- | :--- |
| [SPEC.md](development/SPEC.md) | **Источник истины:** цель, 7 модулей, три уровня, схема БД, API, промпты, стек, roadmap, критерии приёмки, non-functional |
| [GETTING_STARTED.md](development/GETTING_STARTED.md) | Разворачивание за ~20 минут: Docker, venv, миграции, uvicorn, фронтенд, troubleshooting |
| [../TODO.md](../TODO.md) | Последовательность задач T-001…T-027 с зависимостями, DoD и оценками |

### Правила и процесс

| Документ | Что внутри |
| :--- | :--- |
| [RULES.md](development/RULES.md) | Ветки и коммиты, код (ruff/mypy/async/SQLAlchemy), тесты, документация, итерации, безопасность, LLM/движок, блокеры, чек-лист, окружение (§11), формат отчётов (§12) |
| [PROMPT_CURRENT.md](development/PROMPT_CURRENT.md) | Актуальные системные инструкции для DSH + метрики на момент синхронизации |
| [PROMPT_FOR_DSH.md](development/PROMPT_FOR_DSH.md) | Исходная версия инструкций (источник для `PROMPT_CURRENT.md`) |
| [RELEASES.md](development/RELEASES.md) | Журнал итераций и шаблон отчёта |
| [reports/iteration-1/](development/reports/iteration-1/) | Пошаговые отчёты итерации 1 (STEP-01…STEP-11) |
| [reports/iteration-2/](development/reports/iteration-2/) | Пошаговые отчёты итерации 2 — Ingestion (STEP-01…STEP-05) |
| [reports/iteration-3/](development/reports/iteration-3/) | Пошаговые отчёты итерации 3 — Engine Service (STEP-01…STEP-04) |
| [reports/iteration-4/](development/reports/iteration-4/) | Пошаговые отчёты итерации 4 — API `/v1/analyze` и пул (STEP-01…STEP-04) |
| [reports/iteration-5/](development/reports/iteration-5/) | Пошаговые отчёты итерации 5 — мотивы и Policy (STEP-01…STEP-04) |
| [reports/iteration-6/](development/reports/iteration-6/) | Пошаговые отчёты итерации 6 — API и LLM-адаптер (STEP-01…STEP-04) |
| [reports/iteration-7/](development/reports/iteration-7/) | Пошаговые отчёты итерации 7 — промпты и валидатор (STEP-01…STEP-04) |
| [reports/iteration-8/](development/reports/iteration-8/) | Отчёты итерации 8 — комментарий (STEP-01…STEP-02) |

### Состояние проекта

| Документ | Что внутри |
| :--- | :--- |
| [CHANGELOG.md](CHANGELOG.md) | История версий (Keep a Changelog), секция `[Unreleased]` |
| [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | Реестр проблем KI-001…KI-024 со статусами, severity и планом закрытия |

### Тесты и данные

| Артефакт | Что внутри |
| :--- | :--- |
| [../tests/](../tests/) | `test_health.py`, `test_models.py` — healthcheck и схема БД (итерация 1) |
| [../tests/unit/test_ingestion.py](../tests/unit/test_ingestion.py) | 33 теста ingestion: PGN/FEN, ECO, фазы, флаги хода, ошибки, повторы (T-007) |
| [../tests/unit/test_engine.py](../tests/unit/test_engine.py) | 27 тестов движка: классификация, профили, ошибки, интеграция с Stockfish (T-008) |
| [../tests/unit/test_engine_pool.py](../tests/unit/test_engine_pool.py) | 7 тестов пула: параллелизм, порядок, ошибки, закрытие (T-009) |
| [../tests/unit/test_rate_limit.py](../tests/unit/test_rate_limit.py) | 7 тестов rate limiting: окно, 429, fail-open, разделение по IP (T-009) |
| [../tests/unit/test_motifs.py](../tests/unit/test_motifs.py) | 13 тестов мотивов: проходные, слабые поля, линии, вилки/связки, мат (T-010) |
| [../tests/unit/test_policy.py](../tests/unit/test_policy.py) | 15 тестов Policy: gm/club/beginner, ранний ферзь, зевки (T-011) |
| [../tests/integration/test_api_analyze.py](../tests/integration/test_api_analyze.py) | 4 интеграционных теста `/v1/analyze`: SLA ≤ 30 с, БД, мотивы, multipv, `moves`/`recommended_move` (T-009…T-012) |
| [../tests/unit/test_llm.py](../tests/unit/test_llm.py) | 13 тестов LLM-адаптера на моках: маршрутизация, ретраи, таймаут, парсинг (T-013) |
| [../tests/integration/test_llm_live.py](../tests/integration/test_llm_live.py) | 2 живых теста: LM Studio (club) и DeepSeek (gm); скипаются без провайдера (T-013) |
| [../tests/unit/test_prompts.py](../tests/unit/test_prompts.py) | 11 тестов промптов и пакета фактов (T-014) |
| [../tests/unit/test_validator.py](../tests/unit/test_validator.py) | 14 тестов валидатора: SAN-регэксп, легальность, альтернативы (T-015) |
| [../tests/unit/test_warmup.py](../tests/unit/test_warmup.py) | 3 теста прогрева LLM при старте (KI-022) |
| [../tests/integration/test_generation.py](../tests/integration/test_generation.py) | Живой E2E: пакет фактов → LM Studio → валидатор (T-014/T-015) |
| [../tests/integration/test_api_commentary.py](../tests/integration/test_api_commentary.py) | 7 тестов `/v1/commentary`: реконструкция анализа, retry, 404/422, живой прогон (T-016) |
| [../tests/fixtures/example.pgn](../tests/fixtures/example.pgn) | Партия Гукеш — Каймер (Прага 2024), `1.d4 Nf6 2.c4 e6 … 27.Bg6+ 1-0`, ECO `E21` |
| [../tests/fixtures/test_game.pgn](../tests/fixtures/test_game.pgn) | Та же партия с `Test White` / `Test Black` и без `[ECO]` — проверка метаданных и fallback-а ECO |
| [../tests/fixtures/README.md](../tests/fixtures/README.md) | Инварианты обеих фикстур (полуходы, результат, ECO, фазы) |

---

## Планируемые документы

Создаются позже (ссылки появятся вместе с файлами — сейчас их нет,
чтобы не было битых ссылок; см. KI-009):

| Документ | Задача | Назначение |
| :--- | :--- | :--- |
| `ARCHITECTURE.md` | T-026 | Как система устроена сейчас: модули, потоки данных, контракты |
| `USER_GUIDE.md` | T-026 | Пользовательский сценарий: загрузка PGN, уровни, чат, правки |
| `ASSISTANT_LEVELS.md` | T-026 | Чем отличаются новичок / клуб / гроссмейстер |
| `MANUAL_TESTING_GUIDE.md` | T-026 | Ручные кейсы и чек-лист приёмки |
| `v0.1/DESIGN_V010.md` | T-017/T-019 | Дизайн-документ MVP: scope, схема, API, промпты |

---

## Соглашения по документам

1. **Язык** — русский; код, идентификаторы и коммиты — английский.
2. **Markdown** — GFM; оглавление обязательно для документов длиннее
   трёх разделов.
3. **Один факт — один источник.** `SPEC.md` владеет архитектурой,
   `RULES.md` — процессом, `TODO.md` — порядком работ. Остальные
   документы ссылаются, а не копируют.
4. **Даты** — `YYYY-MM-DD`, часовой пояс Europe/Moscow.
5. **Факты в отчётах** — только проверяемые: команда + фактический вывод,
   а не «работает».

---

## Отклонения окружения на текущей dev-машине

Продукт от них не зависит, но команды проверки отличаются
(подробности — [KNOWN_ISSUES.md](KNOWN_ISSUES.md)):

| Что | Канон | На машине итерации 1 | KI |
| :--- | :--- | :--- | :--- |
| HTTP-порт Redis | `127.0.0.1:6380` | `127.0.0.1:6381` (`REDIS_PORT`) | [KI-001](KNOWN_ISSUES.md#ki-001--open--low--infra-dev-env) |
| HTTP-порт API | `127.0.0.1:8000` | `127.0.0.1:8010` | [KI-002](KNOWN_ISSUES.md#ki-002--open--low--api-dev-env) |
| Stockfish | `STOCKFISH_PATH` в ENV | `D:\Tools\stockfish\stockfish.exe` | [KI-006](KNOWN_ISSUES.md#ki-006--open--low--engine-env) |

При этом Postgres — канонический `127.0.0.1:5434`, healthcheck:
`db / redis / stockfish = ok`, `llm = skipped`, общий статус `degraded`.
