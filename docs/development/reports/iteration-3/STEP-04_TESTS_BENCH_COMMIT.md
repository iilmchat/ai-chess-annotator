# STEP-04 — Тесты, бенчмарк, документация, коммит

**Дата:** 2026-10-28
**Итерация:** 3 — Engine Service (T-008)
**Статус:** ✅ выполнен

---

## 1. `tests/unit/test_engine.py` — 27 тестов

| Группа | Что проверяется |
| :--- | :--- |
| Классификация (12 кейсов) | границы 0/1/49/50/99/100/200/201/1500, отрицательная дельта, `is_best` перебивает дельту |
| Профили | глубины 12/18/28, MultiPV 1/3/5, бюджеты 200/1000/5000 мс; неизвестный уровень → `InvalidLevelError` |
| Модели | `EngineLine` требует ровно одну оценку (cp XOR mate); корректно принимает cp и mate |
| Ошибки без движка | нет бинарника → `EngineNotFoundError` (и через `analyze`, и через `executable_path`); плохой FEN → `InvalidFenError`; пустой список ходов → `[]`; несовместимая история → `InvalidPgnError`; `depth=0`/`multipv=0`/`movetime=0` → `ValueError` |
| Интеграция (реальный Stockfish, скип если бинарника нет) | `executable_path` существует и это Stockfish; `analyze(startpos, depth=5)` → легальный ход из `e2e4/d2d4/g1f3/c2c4`; MultiPV=3 → 3 разные линии с корректными рангами и `pv[0] == move_uci`; бенчмарк `club` ≤ 1.15 с; `analyze_game_moves` на первых 6 полуходах (пли, SAN, UCI, `delta_cp`, легальность `best_move`, согласованность `delta = max(0, before − after)`) |

Скип интеграционных тестов — по `STOCKFISH_PATH`/`PATH` (KI-006): на машине
без движка unit-часть всё равно проверяет контракт.

## 2. Прогон

```
pytest tests/unit/test_engine.py  → 27 passed in 4.30s
pytest -q                         → 65 passed, 1 xfailed, 1 warning in 7.06s
ruff check .                      → All checks passed!
mypy app                          → Success: no issues found in 18 files
```

`xfailed` — прежний маркер KI-012 (ingestion), не движковый.

## 3. Бенчмарк (фактические числа, Stockfish 17.1)

| Сценарий | Время | Детали |
| :--- | ---: | :--- |
| `analyze(startpos, "beginner")` | **131 мс** | глубина 12 достигнута, MultiPV 1 |
| `analyze(startpos, "club")` | **1004 мс** | movetime-кап 1000 мс, глубина 17, MultiPV 3 |
| `analyze_game_moves(4 полухода, "beginner")` | **501 мс** | 5 поисков |
| Один поиск «новичка» в партии | ≈ 100 мс | после прогрева |

Тест бенчмарка допускает `1000 + 150 мс` — 1000 мс это бюджет **движка**,
остальное — IPC и чтение MultiPV-строк; без допуска тест флакует на
границе.

## 4. JSON `analyze_game_moves` (первые 3 хода, уровень `club`)

```json
[
  {
    "ply": 1, "played_move_san": "d4", "played_move_uci": "d2d4",
    "eval_before": 27, "eval_after": 22,
    "best_move_uci": "e2e4", "best_move_san": "e4",
    "delta_cp": 5, "classification": "good"
  },
  {
    "ply": 2, "played_move_san": "Nf6", "played_move_uci": "g8f6",
    "eval_before": -22, "eval_after": -21,
    "best_move_uci": "g8f6", "best_move_san": "Nf6",
    "delta_cp": 0, "classification": "best"
  },
  {
    "ply": 3, "played_move_san": "c4", "played_move_uci": "c2c4",
    "eval_before": 21, "eval_after": 27,
    "best_move_uci": "g1f3", "best_move_san": "Nf3",
    "delta_cp": 0, "classification": "best"
  }
]
```

Читается корректно: `1.d4` чуть хуже `1.e4` (−5 cp → `good`), `1...Nf6` —
лучший ход, `2.c4` равен по оценке `2.Nf3` (0 потерь → `best`).

## 5. Документация

Обновлены: `TODO.md` (T-008 `[x]`), `docs/development/RELEASES.md`
(«Итерация 3»), `docs/CHANGELOG.md` (структура `[Unreleased]` приведена в
порядок: дубль `### Changed` из итерации 2 устранён), `docs/KNOWN_ISSUES.md`
(KI-013, KI-014), `README.md` (статус, бейдж, строка «Engine»),
`docs/README.md` (тесты движка, отчёты итерации 3),
`docs/development/PROMPT_CURRENT.md` (версия 1.2, метрики, T-009).

## 6. Коммит

```
feat(engine): async Stockfish UCI service with move classification (T-008)
```

Хеш — `git log -1 --oneline`.

## 7. Definition of Done — T-008

| Критерий | Статус | Факт |
| :--- | :---: | :--- |
| Создан пакет `app/engine/` | ✅ | `__init__.py`, `models.py`, `service.py` |
| `EngineService` умеет MultiPV-анализ | ✅ | `analyze()`; тест на 3 разные линии |
| Реализована классификация ходов | ✅ | `classify_move`, 12 граничных кейсов |
| Реализован `analyze_game_moves` | ✅ | `N+1` поисков, `MoveAnalysis` на каждый полуход |
| Тесты используют реальный Stockfish | ✅ | 4 интеграционных теста, скип при отсутствии бинарника (KI-006) |
| `ruff` / `mypy` без ошибок | ✅ | 0 / 0 (18 файлов) |
| T-008 отмечен `[x]` | ✅ | `TODO.md` |
| Отчёт в `RELEASES.md` | ✅ | «Итерация 3 — Engine Service» |

## 8. Что осталось висеть

- **KI-013** — TCP-транспорт движка не реализован (локальный бинарник).
- **KI-014** — 40 ходов на «клубе» ≈ 41 с > 30 с из SPEC §9; решать в
  T-009 (пул движков / кэш / только критические ходы).
- **`brilliant`** — не выставляется: нужна логика жертвы/единственности
  (T-011).
