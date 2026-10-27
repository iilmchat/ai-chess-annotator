# STEP-02 — Модели движка, ошибки, `initial_fen`

**Дата:** 2026-10-28
**Итерация:** 3 — Engine Service (T-008)
**Статус:** ✅ выполнен

---

## 1. `app/engine/models.py`

| Модель | Поля | Решения |
| :--- | :--- | :--- |
| `EngineLine` | `multipv`, `depth`, `score_cp`, `mate`, `move_uci`, `pv` | `model_validator`: ровно одна оценка (`score_cp` XOR `mate`) — иначе непонятно, что сравнивать; `move_uci` под паттерном UCI |
| `MoveAnalysis` | `ply`, `played_move_san/uci`, `eval_before`, `eval_after`, `best_move_uci/san`, `delta_cp`, `classification` | `delta_cp ≥ 0`; `classification` — `Literal[...]` из SPEC §2.2 (включая `brilliant` — задел на T-011) |

`MATE_CP_BASE = 10_000` — база кодирования мата; вынесена в модель, чтобы
`service.py` и тесты пользовались одним значением.

## 2. `app/errors.py` — ошибки движка

Добавлены к доменным ошибкам итерации 2:

| Класс | `code` | `http_status` | Когда |
| :--- | :--- | :--- | :--- |
| `InvalidLevelError` | `invalid_level` | 422 | уровень не из `beginner`/`club`/`gm` |
| `EngineError` | `engine_error` | 503 | база: движок сломан/не запущен |
| `EngineNotFoundError` | `engine_not_found` | 503 | бинарника нет (KI-006) |
| `EngineTimeoutError` | `engine_timeout` | 504 | не уложился в бюджет, процесс перезапущен |

Почему 503, а не 500: недоступность движка — это «сервис временно не
готов», клиенту имеет смысл повторить позже (и healthcheck уже отдаёт
`degraded`). 504 — для таймаута, чтобы отличать «долго» от «сломалось».

## 3. `ParsedGame.initial_fen` (изменение ingestion)

**Зачем.** `analyze_game_moves(moves, level)` восстанавливает доску, чтобы
отдать движку позиции «до/после». Для партии с FEN-стартом
(`[SetUp "1"][FEN "..."]`) начальная расстановка из `ParsedMove.fen` не
восстанавливается — нужна точка старта.

**Что сделано:**

- `app/ingestion/models.py`: `STARTING_FEN` + поле
  `initial_fen: str = STARTING_FEN` в `ParsedGame`;
- `app/ingestion/parser.py`: `initial_fen = board.fen()` до первого хода
  (учитывает `SetUp`), передаётся в `ParsedGame`.

Изменение аддитивное: старые тесты итериции 2 не ломаются, JSON получает
один новый ключ (`initial_fen`). Для обычной партии он равен начальной
расстановке.

**Почему не «угадывать» стартовую позицию:** `parse_san` без правильной
доски даст другой ход или упадёт, а ошибка будет выглядеть как «нелегальный
PGN». Лучше передать FEN явно и проверить целостность истории.

## 4. Проверки

| Проверка | Результат |
| :--- | :--- |
| `mypy app` (strict) | 0 ошибок |
| `ruff check .` | All checks passed |
| Тесты ingestion после добавления `initial_fen` | 33 passed (не сломались) |

## 5. Что дальше

Шаг 3 — `EngineService` и UCI-клиент.
