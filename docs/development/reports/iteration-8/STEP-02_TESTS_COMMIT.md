# STEP-02 — Тесты, живой прогон, коммит (T-016)

**Дата:** 2026-10-28
**Итерация:** 8 — `POST /v1/commentary`
**Статус:** ✅ выполнен

---

## 1. Тесты

`tests/integration/test_api_commentary.py` — 7 тестов:

| Тест | Что проверяет |
| :--- | :--- |
| `test_load_analyses_reconstructs_moves` | восстановление ходов из БД: SAN/UCI, знак оценки (`+20` у белых, `-20` у чёрных), пустые линии/мотивы |
| `test_retry_after_illegal_moves` | первый ответ с `Qxf7` → второй запрос содержит `ВНИМАНИЕ` и `Qxf7`, `attempts=2`, текст принят |
| `test_raises_after_retries_exhausted` | `commentary_max_retries=0` → `LLMValidationError`, ровно один запрос |
| `test_unknown_game_raises_not_found` | `NotFoundError` для чужого id |
| `test_commentary_unknown_analysis_returns_404` | HTTP 404, `code=not_found` |
| `test_commentary_validates_level_and_id` | 422 на `level=master` и `analysis_id=0` |
| `test_live_club_commentary_persisted_and_validated` | живой LM Studio через API: анализ → комментарий → `validated=true`, текст > 100, запись в БД, удаление после |

## 2. Живой прогон (API, уровень club)

```
analyze: 200
llm_completed elapsed_ms=52191 model=qwen/qwen3-4b-2507 provider=lmstudio tokens_in=2733 tokens_out=570
commentary: 200 52396 ms
{"commentary_id": 21, "validated": true, "tokens_used": 3303,
 "model": "qwen/qwen3-4b-2507", "attempts": 1}
validator: 29 moves, legal = True, illegal = []
```

Фрагмент текста:

> Партия прошла в структуре Э21 — дебют с пешкой d4 и c4, где белые сразу
> после 3...Bb4 и 4...d6 развивают активность. Важным моментом стало
> развитие пешки g5 у чёрных, что позволило им создать давление на центр и
> усилить контроль за полем b3. На полуходе 13 белые играют Bd3, что даёт
> оценку –0.17 за них — движок рекомендует d5 как более точный ход,
> улучшающий контроль над слабым полем b3. … Важным поворотом становится
> 22...d5, где оценка растёт до +4.03 за белых, и движок рекомендует Kg7,
> поскольку проходная пешка g6 у белых создаёт давление, а слабое поле b3
> остаётся уязвимым.

Наблюдение: формат и ходы корректны (валидатор — 29/29), но атрибуция
рекомендаций местами спорная — это KI-023 (семантика не проверяется).

## 3. Полный прогон

```
pytest -q      → 165 passed, 1 xfailed, 1 warning in 159.62 s
ruff check .   → All checks passed!
mypy app       → Success: no issues found in 36 files
```

## 4. Коммит

```
feat(api): add validated commentary generation endpoint (T-016)
```

Хеш — `git log -1 --oneline`.

## 5. Definition of Done — T-016

| Критерий | Статус | Факт |
| :---: | :---: | :--- |
| `/v1/commentary` работает и сохраняет в БД | ✅ | живой прогон, комментарий id 21, запись проверена тестом |
| Валидатор отсекает нелегальные ходы | ✅ | retry-тесты + 502 после исчерпания попыток |
| Тест: текст > 100 символов, `validated=true`, запись в БД | ✅ | `test_live_club_commentary_persisted_and_validated` |
| `ruff`/`mypy`/`pytest` зелёные | ✅ | 0 / 0 / 165 passed |
| T-016 `[x]` | ✅ | `TODO.md` |
