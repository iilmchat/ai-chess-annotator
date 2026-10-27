# STEP-04 — Интеграционные тесты, SLA, документация, коммит

**Дата:** 2026-10-28
**Итерация:** 4 — API `/v1/analyze` (T-009)
**Статус:** ✅ выполнен

---

## 1. Интеграционные тесты (`tests/integration/test_api_analyze.py`)

| Тест | Что проверяет |
| :--- | :--- |
| `test_analyze_example_pgn_meets_sla_and_persists` | 53 полухода, `club`: ответ 200, `moves_analyzed == 53`, `meta` (Гукеш/Каймер/E21), `time_taken_ms ≤ 30 000`, **настенное время ≤ 30 000**; в БД: `games` с `eco_code`/`phase_breakdown`/`pgn`, 53 `moves` с `ply` 1…53, `classification ∈ {…}`, `eval_cp` **или** `eval_mate`, `best_move_san`, `delta_cp ≥ 0`, `engine_depth ≥ 1`; после теста запись удаляется |
| `test_analyze_rejects_garbage_pgn` | 400 + `code == "invalid_pgn"` |
| `test_analyze_rejects_empty_pgn` | 422 (Pydantic `min_length`) |
| `test_analyze_rejects_unknown_level` | 422 (`Literal`) |

Модуль скипается, если нет PostgreSQL или Stockfish (в CI оба есть).
Приложение в тесте собирается с `rate_limit_requests=1000`, чтобы
несколько запросов подряд не упирались в лимит.

## 2. SLA (KI-014)

```
HTTP: 200
{
  "analysis_id": "16",
  "status": "completed",
  "meta": {"white": "Gukesh, D.", "black": "Keymer, Vincent", "eco": "E21"},
  "moves_analyzed": 53,
  "time_taken_ms": 14646,
  "summary": {"blunders": 0, "mistakes": 4, "inaccuracies": 4}
}
wall: 14660 ms
```

| Показатель | Значение |
| :--- | ---: |
| Полных поисков | 54 (старт + 53 хода) |
| Пул | 4 процесса |
| Время | **14 646 мс** |
| Лимит SPEC §9 | 30 000 мс |
| Запас | ≈ ×2 |
| Последовательный вариант (итерация 3) | ≈ 54 000 мс |

Ускорение ≈ ×3.7 — чуть ниже идеальных ×4 из-за старта процессов и
неравномерной загрузки последней «волны».

## 3. Полный прогон

```
pytest -q        → 83 passed, 1 xfailed, 1 warning in 26.28s
ruff check .     → All checks passed!
mypy app         → Success: no issues found in 21 files
```

Прирост к итерации 3: +18 тестов (7 пул + 7 rate limit + 4 интеграционных).
`xfailed` — прежний маркер KI-012.

## 4. Реестр KI

* **KI-014 → Closed** (пул; доказательство — SLA-тест).
* Новые:
  * **KI-015** (Low, api/infra) — rate limit fail-open + гонка
    `INCR`/`EXPIRE`;
  * **KI-016** (Low, api) — `analysis_id` = `games.id`, публичный UUID не
    введён;
  * **KI-017** (Low, api/engine) — SLA не гарантируется для партий
    160+ полуходов;
  * **KI-018** (Low, db) — `moves.multipv` не заполняется.

Итого: 18 записей, 17 Open, 1 Closed.

## 5. Документация

`TODO.md` (T-009 `[x]`), `RELEASES.md` («Итерация 4»), `CHANGELOG.md`
(Added: API/пул/обработчик/rate limit; Changed: 500 вместо 503,
`create_app(settings)`), `KNOWN_ISSUES.md` (KI-014 закрыт, +4),
`README.md` (статус, бейдж, строки Engine/API), `docs/README.md`
(тесты, отчёты итерации 4), `PROMPT_CURRENT.md` (версия 1.3),
отчёты `reports/iteration-4/`.

## 6. Коммит

```
feat(api): add /v1/analyze with parallel engine pool and rate limiting (T-009)
```

Хеш — `git log -1 --oneline`.

## 7. Definition of Done — T-009

| Критерий | Статус | Факт |
| :--- | :---: | :--- |
| Пул движков / параллельный анализ (KI-014 решён) | ✅ | `EnginePool(4)`, 54 позиции за 14.6 с; KI-014 Closed |
| Глобальный обработчик `AppError` → HTTP | ✅ | 400/422/500/504, тело `{"detail", "code"}` |
| Базовый rate limiting | ✅ | Redis `INCR`/`EXPIRE`, 10/мин на IP, 429 + заголовки |
| `/v1/analyze` работает и сохраняет в БД | ✅ | `games` + 53 `moves`, проверено интеграционным тестом |
| Интеграционный тест: 40+ ходов ≤ 30 с | ✅ | 53 полухода, 14 646 мс, тест падает при превышении |
| `ruff` / `mypy` без ошибок | ✅ | 0 / 0 (21 файл) |
| T-009 отмечен `[x]` | ✅ | `TODO.md` |
| Отчёт в `RELEASES.md` | ✅ | «Итерация 4 — API `/v1/analyze` и пул движков» |

## 8. Что осталось висеть

- KI-015 — fail-open лимитера (осознанно).
- KI-016 — публичные UUID вместо bigint-id.
- KI-017 — длинные партии выходят за SLA.
- KI-018 — `moves.multipv` пустой; понадобится для Policy/LLM
  (T-011/T-012).
- KI-013 — TCP-транспорт движка.
