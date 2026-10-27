# STEP-03 — Эндпоинт `/v1/analyze` и запись в БД

**Дата:** 2026-10-28
**Итерация:** 4 — API `/v1/analyze` (T-009)
**Статус:** ✅ выполнен

---

## 1. Контракт

```
POST /v1/analyze
{"pgn": "...", "level": "beginner|club|gm"}   # level по умолчанию club

200 →
{
  "analysis_id": "16",
  "status": "completed",
  "meta": {"white": "Gukesh, D.", "black": "Keymer, Vincent", "eco": "E21"},
  "moves_analyzed": 53,
  "time_taken_ms": 14646,
  "summary": {"blunders": 0, "mistakes": 4, "inaccuracies": 4}
}
```

`level` — `Literal["beginner", "club", "gm"]`: неверное значение ловит
Pydantic (422) до обращения к движку. `pgn` — 1…100 000 символов.

## 2. Поток обработки

1. `parse_pgn(payload.pgn)` — ingestion валидирует PGN, строит
   `ParsedGame` (ходы, метаданные, ECO, фазы, `initial_fen`).
2. `request.app.state.engine_pool.analyze_game_moves(...)` — пул (4
   процесса) параллельно считает 54 позиции.
3. `_persist(...)` — одна транзакция: `games` + 53 × `moves`.
4. Ответ: `analysis_id`, метаданные, счётчики, `time_taken_ms`.

## 3. Что пишется в БД

`games`: `pgn` (как пришёл), `eco_code`, `phase_breakdown` (JSONB
`{"opening_end_ply": 30, "endgame_start_ply": null}`), `user_id = NULL`
(анонимная партия до появления JWT).

`moves` (53 строки):

| Колонка | Откуда |
| :--- | :--- |
| `ply`, `san` | `MoveAnalysis.ply`, `played_move_san` |
| `fen` | `ParsedMove.fen` (позиция после хода) |
| `eval_cp` | `eval_after`, приведённая **к белым** (знак по чётности ply) |
| `eval_mate` | `mate_after`, если в позиции после хода найден мат |
| `best_move_san`, `delta_cp`, `classification` | из `MoveAnalysis` |
| `engine_depth` | глубина лучшей линии позиции «до хода» |
| `multipv` | **NULL** — KI-018 |

Почему `eval_cp` в белых: колонка должна читаться одинаково для ходов
обеих сторон (иначе знак зависит от того, кто ходил). Для мата
`eval_cp = NULL`, `eval_mate = ±N` — два поля не дублируют друг друга.

`summary` считается по классификациям (`blunder`/`mistake`/`inaccuracy`)
из тех же `MoveAnalysis`, что ушли в БД, — цифры в ответе и в таблице
совпадают по построению.

## 4. Ошибки

| Ситуация | Ответ |
| :--- | :--- |
| «мусорный» PGN / нет ходов / нелегальный ход | 400 `invalid_pgn` |
| пустой `pgn` или неверный `level` | 422 (Pydantic) |
| нет движка / сломался | 500 `Engine unavailable` |
| таймаут движка | 504 |

## 5. Почему `analysis_id` — строка с `games.id`

В примере постановки был UUID, но в схеме SPEC §4 публичного
идентификатора нет. Добавлять колонку с миграцией в T-009 — менять схему
вне задачи; вынесено в **KI-016**. Формат строки выбран, чтобы контракт
не поменялся, когда появится UUID.

## 6. Проверки

| Проверка | Результат |
| :--- | :--- |
| `POST` короткой партии (beginner) | 200, 4 хода, 890 мс |
| Битый PGN | 400 `invalid_pgn` |
| `level="master"` | 422 |
| Полная партия (club) | 200, 53 хода, 14 646 мс |

## 7. Что дальше

Шаг 4 — интеграционные тесты и SLA.
