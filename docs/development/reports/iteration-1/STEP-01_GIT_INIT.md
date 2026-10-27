# STEP-01 — Инициализация git (T-001)

**Дата:** 2026-10-28
**Итерация:** 1 — Bootstrap (T-001…T-006)
**Статус:** ✅ выполнен

---

## 1. Что сделано

1. `git init` в `D:\Projects\AI\ai-chess-annotator` — репозиторий создан с нуля
   (до этого `.git/` отсутствовал, все файлы лежали как «bootstrap»-набор).
2. Ветка переименована `master` → `main`: `.github/workflows/ci.yml` триггерится
   только на `main`, бейдж в `README.md` указывает на `main`.
3. `git add . && git commit -m "chore: bootstrap (T-001..T-005)"` — один
   базовый коммит, 35 файлов, 0 untracked (кроме исключённых `.gitignore`).

Идентичность коммиттера взята из глобального конфига git:
`Nikiforov Andrey <avnikiforov2014@yandex.ru>`.

## 2. Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Один коммит | `git log --oneline` | `bb3b7ce chore: bootstrap (T-001..T-005)` |
| Ветка | `git branch --show-current` | `main` |
| Файлов в индексе | `git ls-files \| Measure-Object` | 35 |
| Рабочее дерево чистое | `git status --short` | пусто |

## 3. Что попало в коммит

- Backend: `app/{main,config}.py`, `app/db/{base,session}.py`,
  `app/api/v1/health.py`.
- Alembic: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`,
  `alembic/versions/.gitkeep`.
- Инфра: `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`,
  `docker/stockfish/Dockerfile`, `pyproject.toml`, `.env.example`.
- Web: `web/{index.html,package.json,tsconfig.json,vite.config.ts,src/*}`.
- Docs: `README.md`, `TODO.md`, `docs/development/{SPEC,PROMPT_FOR_DSH}.md`.
- Тесты: `tests/test_health.py`, `tests/fixtures/example.pgn`.

## 4. Наблюдения (не блокеры, зафиксированы для следующих шагов)

1. В bootstrap-наборе **отсутствует `docs/README.md`**, хотя на него ссылаются
   `README.md`, `TODO.md` (T-006 DoD) и `PROMPT_FOR_DSH.md`. Навигация docs
   создаётся в шаге 8.
2. `docs/development/PROMPT_FOR_DSH.md` лежит не в корне, а в
   `docs/development/` — `PROMPT_CURRENT.md` копируется оттуда.
3. `tests/fixtures/example.pgn` уже существует и содержит **ту же партию**
   (Гукеш — Каймер, 2024), что в постановке, но с полными headers из TWIC.
   Сверка ходов — в шаге 9.
4. `pyproject.toml` **не содержит `pgvector`** — без него невозможна модель
   `annotated_games.embedding`. Добавляется в шаге 4 (стек не меняется:
   pgvector уже в SPEC §7).

## 5. Что дальше

Шаг 2 — `docker compose up -d postgres redis` (T-002).
