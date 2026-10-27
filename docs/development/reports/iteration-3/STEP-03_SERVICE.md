# STEP-03 — `EngineService` и асинхронный UCI-клиент

**Дата:** 2026-10-28
**Итерация:** 3 — Engine Service (T-008)
**Статус:** ✅ выполнен

---

## 1. Публичный API

```python
class EngineService:
    executable_path: Path                      # → EngineNotFoundError, если нет
    async def analyze(fen, level, *, depth=None, multipv=None,
                      movetime_ms=None) -> list[EngineLine]
    @staticmethod
    def classify_move(delta_cp, is_best=False) -> MoveClassification
    async def analyze_game_moves(moves, level, *, initial_fen=None)
        -> list[MoveAnalysis]
    async def aclose() -> None                 # + async context manager
```

Профили (SPEC §2.2) — `ENGINE_PROFILES`:

| Уровень | depth | MultiPV | movetime |
| :--- | ---: | ---: | ---: |
| beginner | 12 | 1 | 200 мс |
| club | 18 | 3 | 1000 мс |
| gm | 28 | 5 | 5000 мс |

`analyze` валидирует FEN через `app.ingestion.parse_fen` (это и защита от
инъекции команд в UCI: нормализованный FEN не содержит переводов строк),
затем берёт профиль по уровню и выполняет поиск под `asyncio.Lock`.

## 2. Как устроен UCI-клиент

```
EngineService.analyze
  └─ asyncio.Lock                       (UCI-сессия однопоточная)
      └─ _UciEngine.go
           ucinewgame
           setoption name MultiPV value N
           position fen <fen>
           isready → ждём readyok      (таймаут 5 с)
           go depth D movetime T
           читаем строки до bestmove:
             info … multipv K depth D score cp|mate X pv m1 m2 …
             (на каждый ранг храним последнюю = самую глубокую линию)
           сортируем по multipv
```

* **Таймаут поиска** = `movetime + 4 с` на чтение (MultiPV-строки + IPC).
  При превышении процесс **убивается** (`quit` → `kill`), поднимается
  `EngineTimeoutError`; следующий вызов стартует новый процесс
  (`_ensure_engine` проверяет `is_running`).
* **EOF от процесса** (`readline() == b""`) → `EngineError`, а не вечное
  ожидание.
* **Ошибки запуска** (`OSError`, нет файла) → `EngineNotFoundError`.
* **`stderr`** уходит в `DEVNULL`: движок не должен засорять поток
  `info`/`bestmove`.
* **Закрытие**: `quit` → `wait(2 c)` → `kill`, идемпотентно.

## 3. `analyze_game_moves`: как считаются оценки

1. Доска восстанавливается из `initial_fen` (по умолчанию — начальная
   расстановка).
2. Каждый `ParsedMove.san` применяется `parse_san`; результат сверяется с
   `ParsedMove.fen` — если история расходится, `InvalidPgnError`
   (защита от неверного `initial_fen` и битых данных).
3. Анализируются **все** позиции: `N + 1` поисков (до первого хода и
   после каждого).
4. Оценки приводятся к белым, затем к точке зрения сделавшего ход:
   `eval_before`, `eval_after`; `delta_cp = max(0, before − after)`.
5. `best_move_uci` — первый ход лучшей линии, `best_move_san` — через
   `board.san()`; если движок вернул недопустимый ход — `EngineError`
   (а не тихий пропуск).
6. `classification` — `classify_move(delta_cp, is_best)`.

## 4. `classify_move` — пороги

| Условие | Класс |
| :--- | :--- |
| `delta_cp ≤ 0` **или** `is_best` | `best` |
| `0 < delta < 50` | `good` |
| `50 ≤ delta < 100` | `inaccuracy` |
| `100 ≤ delta ≤ 200` | `mistake` |
| `delta > 200` | `blunder` |

Ровно по постановке («0 cp или лучший ход → best», «> 200 → blunder»).
Следствие, которое стоит помнить: ход, равный по оценке лучшему, но не
совпадающий с ним (например, `1.c4` при `+27` против `1.Nf3` при `+27`),
получает `best` — так задумано, потому что потерь нет.

## 5. Проверки

| Проверка | Результат |
| :--- | :--- |
| `ruff check .` | All checks passed |
| `mypy app` (strict) | 0 ошибок, 18 файлов |
| Первый живой прогон | `engine: D:\Tools\stockfish\stockfish.exe`; `beginner` 131 мс (глубина 12), `club` 1004 мс (глубина 17, MultiPV 3) |

## 6. Что дальше

Шаг 4 — тесты, бенчмарк, документация, коммит.
