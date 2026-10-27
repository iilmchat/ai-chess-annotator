# STEP-04 — Прогон, документация, коммит

**Дата:** 2026-10-28
**Итерация:** 6 — API + LLM (T-012, T-013)
**Статус:** ✅ выполнен

---

## 1. Полный прогон

```
pytest -q        → 130 passed, 1 xfailed, 2 warnings in 31.05 s
ruff check .     → All checks passed!
mypy app         → Success: no issues found in 29 files
```

Разбивка прироста (+19 к итерации 5):

| Файл | Тестов |
| :--- | ---: |
| `tests/unit/test_llm.py` (новый) | 13 |
| `tests/unit/test_policy.py` (+KI-020 и различие beginner/gm) | +4 |
| `tests/integration/test_llm_live.py` (новый, живые) | 2 |

`xfailed` — по-прежнему маркер KI-012 (ingestion).

## 2. Ответ API (фрагмент)

```json
{
  "analysis_id": "29",
  "status": "completed",
  "meta": {"white": "Gukesh, D.", "black": "Keymer, Vincent", "eco": "E21"},
  "moves_analyzed": 53,
  "time_taken_ms": 14900,
  "summary": {"blunders": 0, "mistakes": 4, "inaccuracies": 4},
  "moves": [
    {
      "ply": 1, "san": "d4", "uci": "d2d4", "eval_cp": 22, "mate": null,
      "best_move_san": "e4", "delta_cp": 5, "classification": "good",
      "motifs": [
        {"type": "king_safety", "importance": 4,
         "description": "Король белых в центре (e1), ферзи на доске"}
      ],
      "recommended_move": {"san": "e4", "uci": "e2e4", "eval_cp": 27, "mate": null}
    }
  ]
}
```

## 3. Реестр KI

* **KI-020 → Closed:** `mate > 0` разрешён новичку всегда (4 теста).
* Новые:
  * **KI-021** (Low, llm) — `cost_usd` не считается (нет прайс-листа);
  * **KI-022** (Low, llm/infra) — первый запрос к LM Studio ≈ 12 с.
* Итого: 22 записи, 19 Open, 3 Closed.

## 4. Документация

`TODO.md` (T-012, T-013 `[x]`), `RELEASES.md` («Итерация 6»), `CHANGELOG.md`,
`KNOWN_ISSUES.md`, `README.md` (статус, бейдж 130, строки API/LLM),
`docs/README.md` (тесты LLM, отчёты итерации 6, диапазон KI),
`PROMPT_CURRENT.md` (версия 1.5), `.env.example` (LM Studio, DeepSeek,
лимиты), отчёты `reports/iteration-6/`.

## 5. Коммит

```
feat(api,llm): expose motifs/recommended moves and add LLM adapter (T-012, T-013)
```

Хеш — `git log -1 --oneline`.

## 6. Definition of Done — T-012 / T-013

| Критерий | Статус | Факт |
| :--- | :---: | :--- |
| `/v1/analyze` содержит `motifs` и `recommended_move` | ✅ | блок `moves`, 53/53 хода |
| KI-020 решён | ✅ | `mate > 0` обходит фильтры; 4 теста; KI Closed |
| Пакет `app/llm/` с OpenAI-совместимым API | ✅ | `base`, `openai_client`, `factory` |
| Маппинг уровней на провайдеров | ✅ | `beginner`/`club` → LM Studio, `gm` → DeepSeek |
| Тесты с моками | ✅ | 13 тестов `MockTransport` |
| `ruff` / `mypy` — 0 ошибок | ✅ | 0 / 0 (29 файлов) |
| T-012 и T-013 отмечены `[x]` | ✅ | `TODO.md` |
| Живые тесты (по доп. требованию) | ✅ | LM Studio 1.7 с, DeepSeek 1.6 с |

## 7. Что осталось висеть

- KI-021 — стоимость LLM не считается (T-016).
- KI-022 — холодный старт LM Studio ≈ 12 с (прогрев в T-016/T-021).
- KI-019 — recall мотивов не измерен.
