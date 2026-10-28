# STEP-01 — Сервис и эндпоинт комментария (T-016)

**Дата:** 2026-10-28
**Итерация:** 8 — `POST /v1/commentary`
**Статус:** ✅ выполнен

---

## 1. Поток

```
POST /v1/commentary {analysis_id, level, style?}
  ├─ load_game(session, analysis_id)            → 404, если нет
  ├─ parse_pgn(game.pgn)                        → ParsedGame
  ├─ load_analyses(session, game, parsed)       → list[MoveAnalysis]
  ├─ build_fact_package(parsed, analyses, level)
  ├─ [ gm ] rag_context(...)                    → T-017
  ├─ load_system_prompt(level) + style
  ├─ LLM.complete(package, system_prompt)
  ├─ validate_moves(...)                        → при нарушениях retry ×2
  ├─ INSERT commentaries (text, model, tokens, validated)
  └─ 200 {commentary_id, text, tokens_used, validated, model, attempts}
```

Почему сервис, а не всё в эндпоинте: сценарий с ретраями и валидацией
тестируется без HTTP и без реальной модели (`ScriptedLLM`).

## 2. Реконструкция анализа из БД

В `moves` хранятся `eval_cp`/`eval_mate` **с точки зрения белых**, `delta_cp`,
`multipv` (JSONB линий) и `engine_depth`; мотивы — в `motifs`.

`load_analyses` восстанавливает `MoveAnalysis`:

| Поле | Откуда |
| :--- | :--- |
| `played_move_uci` | реплей SAN по доске от `initial_fen` |
| `eval_after` | `eval_cp` × знак (белые/чёрные по чётности ply) |
| `eval_before` | `eval_after + delta_cp` (дельты хватает для фактов) |
| `mate_after` | `eval_mate` |
| `lines` | `multipv` → `EngineLine.model_validate` (битые элементы пропускаем) |
| `motifs` | строки `motifs` → `Motif` (невалидные типы пропускаем) |
| `best_move_uci` | по `best_move_san` в позиции до хода |

## 3. Ретраи и валидация

```python
for attempt in range(1, max_retries + 2):
    text = llm.complete(prompt)
    legal, illegal = validate_moves(extract_moves_from_text(text), initial_fen,
                                     mainline_san=mainline)
    if legal: return text
    prompt = package + "ВНИМАНИЕ: были ходы, которых нет в пакете: …"
raise LLMValidationError(...)   # 502
```

* `max_retries` — из `settings.commentary_max_retries` (по умолчанию 2).
* В запрос-исправление попадают **конкретные** нелегальные ходы.
* Если после всех попыток ходы всё ещё нелегальны — `502
  llm_invalid_output`, и **ничего не сохраняется**: пользователь не должен
  увидеть выдуманные ходы (SPEC §2.6).

## 4. Сохранение

`commentaries`: `game_id`, `level` (enum), `text`, `model` (фактическая
модель из ответа провайдера), `tokens_in`, `tokens_out`, `validated=True`,
`cost_usd` — `NULL` (KI-021: прайс-листа нет).

## 5. Контракт ошибок

| Ситуация | Ответ |
| :--- | :--- |
| партии нет / нет анализа | 404 `not_found` |
| неверный уровень / id | 422 |
| нет ключа DeepSeek (gm) | 503 `llm_not_configured` |
| таймаут LLM | 504 `llm_timeout` |
| нелегальные ходы после retry | 502 `llm_invalid_output` |

## 6. Побочный фикс

Тесты с несколькими `TestClient` падали `500 Event loop is closed`:
глобальный async-engine `app.db.session` переиспользовал соединения
asyncpg, созданные в уже закрытом event loop предыдущего TestClient.
Добавлено `await db_engine.dispose()` в `finally` lifespan — корректно и
для продакшена (закрытие пула при остановке).

## 7. Что дальше

Шаг 2 — тесты и живой прогон.
