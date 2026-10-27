# STEP-03 — Зависимости и окружение Python (T-003/T-004)

**Дата:** 2026-10-28
**Итерация:** 1 — Bootstrap (T-001…T-006)
**Статус:** ✅ выполнен (потребовалась установка Python 3.12 на хост)

---

## 1. Проблема окружения

На машине был **только Python 3.14.7** (`WindowsApps` + `pythoncore-3.14-64`).
Стек проекта — Python 3.12 (`requires-python = ">=3.12"`, `ruff.target-version`,
`mypy.python_version`). 3.14 отклонён:

- часть зависимостей (`asyncpg`, `greenlet`) на 3.14 ставится без
  гарантированных wheels и тянет сборку из исходников (MSVC);
- `mypy python_version = "3.12"` не проверял бы то, что реально исполняется;
- «не меняй стек» — 3.14 это и есть смена стека.

Установлен Python **3.12.10** per-user (без admin):

```powershell
winget install -e --id Python.Python.3.12 --scope user `
  --accept-package-agreements --accept-source-agreements --disable-interactivity
# → C:\Users\<user>\AppData\Local\Programs\Python\Python312\python.exe
```

## 2. Что сделано

1. `python -m venv .venv` от Python 3.12.10 → `.venv\Scripts\python.exe` = 3.12.10.
2. `pip install --upgrade pip` → pip 26.2.2.
3. `.env` создан из `.env.example` (в `.env` — `REDIS_PORT=6381`, см. KI-001).
4. `pip install -e ".[dev]"` — editable-установка проекта и dev-инструментов.
5. **Закрыт пробел bootstrap-набора:** в `pyproject.toml` добавлена
   зависимость `pgvector>=0.3.6` (без неё нельзя объявить
   `annotated_games.embedding`; сам стек не менялся — pgvector есть в
   SPEC.md §7).
6. `alembic current` — команда отрабатывает, ревизий ещё нет (`head` пуст),
   подключение к PostgreSQL 17 через asyncpg работает.

## 3. Ключевые версии

```
fastapi 0.162.0        chess 1.11.2 (python-chess 1.999)   stockfish 5.2.0
SQLAlchemy 2.1.6       alembic 1.20.1                     asyncpg 0.32.0
pydantic 2.14.1        pydantic-settings 2.16.1           pgvector 0.5.1
redis 5.3.1            uvicorn 0.54.0                     greenlet 3.5.6
ruff 0.17.3            mypy 2.4.0                         pytest 9.2.0
pytest-asyncio 1.4.1   chess-annotator 0.1.0 (editable)
```

## 4. Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| venv на 3.12 | `.venv\Scripts\python.exe --version` | `Python 3.12.10` |
| Пакеты на месте | `pip list \| Select-String "fastapi\|chess\|stockfish\|pgvector"` | все найдены |
| Alembic видит БД | `alembic current` | exit 0, ревизий нет (ожидаемо) |
| Alembic heads | `alembic heads` | пусто (моделей ещё нет) |

## 5. Попутные правки в bootstrap-файлах

Эти правки сделаны, чтобы `ruff`/`mypy` были зелёными (шаг 5), но
относятся к коду, существовавшему до итерации:

| Файл | Что и почему |
| :--- | :--- |
| `pyproject.toml` | +`pgvector`; `RUF002/RUF003` в `ignore` (кириллица в докстрингах — норма проекта); убран `types-redis` (redis 5.x поставляет `py.typed`, стабы 4.6 маскировали `aclose`); +`asyncio_default_fixture_loop_scope` (убирает deprecation pytest-asyncio) |
| `app/api/v1/health.py` | убраны бесполезные `# noqa: BLE001` (правило не включено) и `# type: ignore`; `from_url` помечен `no-untyped-call` (upstream-пробел redis-py 5.3); `status="degraded"`, если **любой** компонент не `ok` (требование DoD: LLM `skipped` ⇒ `degraded`) |
| `app/config.py` | убран неиспользуемый `from pydantic import Field` |
| `app/main.py` | `AsyncIterator` из `collections.abc` (UP035) |
| `alembic/env.py` | `from app.db import models` — autogenerate видит таблицы; порядок импортов |

## 6. Что дальше

Шаг 4 — модели БД и первая миграция.
