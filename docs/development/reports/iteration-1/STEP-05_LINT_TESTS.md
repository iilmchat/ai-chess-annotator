# STEP-05 — Линтеры и тесты (T-005)

**Дата:** 2026-10-28
**Итерация:** 1 — Bootstrap (T-001…T-006)
**Статус:** ✅ выполнен

---

## 1. Итоговый прогон

```
ruff check .
All checks passed!                                     [exit=0]

mypy app
Success: no issues found in 10 source files            [exit=0]

pytest -q
5 passed, 1 warning in 10.93s                          [exit=0]
```

## 2. Что пришлось починить (bootstrap-код был не зелёным)

При первом прогоне `ruff` дал **14 ошибок**, `mypy` — **4**. Все — в
коде, который лежал в репозитории до итерации, плюс мои модели.

| Инструмент | Проблема | Исправление |
| :--- | :--- | :--- |
| ruff | `I001` в `alembic/env.py` (порядок импортов после добавления `models`) | `ruff check --fix` |
| ruff | `RUF100` ×2 — `# noqa: BLE001`, а правило `BLE` не включено | удалены |
| ruff | `F401` — неиспользуемый `Field` в `app/config.py` | удалён импорт |
| ruff | `UP035` — `typing.AsyncIterator` в `app/main.py` | `collections.abc` |
| ruff | `RUF002` ×9 — «ambiguous unicode» в русских докстрингах | `RUF002/RUF003` в `ignore` (документация проекта русская) |
| ruff | `RUF002`/`RUF003` | см. выше, pyproject |
| mypy | `"Mapped[str]" not callable` — атрибут `Commentary.text` перекрывал `sqlalchemy.text` внутри класса | импорт `from sqlalchemy import text as sql_text` |
| mypy | `"Redis[Any]" has no attribute "aclose"` — стабы `types-redis` 4.6 против redis 5.3 | `types-redis` убран из dev-зависимостей (redis 5.x несёт `py.typed`) |
| mypy | `Unused "type: ignore"` в `health.py:75` | ignore убран, `overall` типизирован как `Literal["ok","degraded"]` |
| mypy | `Call to untyped function "from_url"` | узкий `# type: ignore[no-untyped-call]` (KI-004) |

Проверено, что `alembic` (`env.py`) не входит в `mypy app` — это осознанно:
`pyproject` исключает `alembic/` из проверки (`exclude = ["alembic/"]`).

## 3. Тесты

| Тест | Тип | Что проверяет |
| :--- | :--- | :--- |
| `test_health_returns_ok_or_degraded` | unit (TestClient) | `/v1/health` → 200, `status ∈ {ok, degraded}`, 4 компонента, допустимые статусы |
| `test_metadata_contains_exactly_spec_tables` | unit | в `Base.metadata` ровно 8 таблиц SPEC §4 |
| `test_moves_index_and_embedding_dimension` | unit | индекс `ix_moves_game_id_ply`, `Vector(1024)`, `users.level NOT NULL` |
| `test_level_enum_values` | unit | `beginner/club/gm` и обратное преобразование `Level("gm")` |
| `test_server_defaults_and_cascade_delete` | integration (PostgreSQL) | `users.level` default `club`, `created_at` заполняется, `commentaries.validated/human_edited` = `false`, каскад `game → moves → motifs` и `game → commentaries`; транзакция откатывается, БД чистая |

Интеграционный тест сам делает `pytest.skip`, если PostgreSQL недоступен,
поэтому локальный прогон без инфраструктуры не падает (в CI БД есть).

## 4. Ошибка, пойманная тестом (полезно для T-016)

`session.refresh(user)` **просрочивает** связанные объекты
(`game.user`, `game.id` и т. д.). Следующий доступ к `game.id` из
синхронного контекста уходит в БД и падает с
`MissingGreenlet: greenlet_spawn has not been called`. В тесте defaults
теперь читаются явными `select(...)`, а не через `refresh`. Тот же
паттерн опасен в обработчиках (`await session.refresh(...)` в async-коде
без последующего `await`-доступа к связям).

## 5. Известные предупреждения

- `StarletteDeprecationWarning: Using httpx with starlette.testclient is
  deprecated; install httpx2` — upstream, на результат не влияет
  (KI-005).
- Предупреждение pytest-asyncio про `asyncio_default_fixture_loop_scope`
  снято настройкой в `pyproject.toml`.

## 6. Что дальше

Шаг 6 — живой healthcheck через `uvicorn`.
