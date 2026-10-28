# STEP-04 — Прогон, документация, коммит

**Дата:** 2026-10-28
**Итерация:** 7 — Промпты и валидатор (T-014, T-015)
**Статус:** ✅ выполнен

---

## 1. Полный прогон

```
pytest -q        → 158 passed, 1 xfailed, 2 warnings in 88.56 s
ruff check .     → All checks passed!
mypy app         → Success: no issues found in 33 files
```

Прирост: +28 тестов к итерации 6.

| Файл | Тестов |
| :--- | ---: |
| `tests/unit/test_prompts.py` (новый) | 11 |
| `tests/unit/test_validator.py` (новый) | 14 |
| `tests/unit/test_warmup.py` (новый) | 3 |
| `tests/integration/test_generation.py` (новый, живой) | 1 |

`xfailed` — прежний маркер KI-012 (ingestion). Полный прогон стал дольше
(88 с) из-за живого E2E: анализ 15 с + генерация 54 с. В CI без LM Studio
этот тест скипается.

## 2. Реестр KI

* **KI-022 → Closed:** прогрев LM Studio в lifespan (934 мс тёплый старт,
  ошибки не ломают приложение, есть 3 юнит-теста).
* Новые:
  * **KI-023** (Med, llm/validator) — валидатор проверяет только
    легальность SAN, а не семантику (оценки/атрибуция);
  * **KI-024** (Low, llm) — ~9 ток/с у локальной модели: комментарий
    ≈ 54 с.
* Итого: 24 записи, 20 Open, 4 Closed.

## 3. Документация

`TODO.md` (T-014, T-015 `[x]`), `RELEASES.md` («Итерация 7»),
`CHANGELOG.md`, `KNOWN_ISSUES.md`, `README.md` (статус, бейдж 158, строки
LLM/Prompts), `docs/README.md` (тесты, отчёты итерации 7, KI-024),
`PROMPT_CURRENT.md` (версия 1.6), `.env.example`
(`LLM_MAX_TOKENS=1024`, `LLM_TIMEOUT_S=120`), отчёты
`reports/iteration-7/`.

## 4. Коммит

```
feat(llm): add level prompts, fact package, warmup and SAN validator (T-014, T-015)
```

Хеш — `git log -1 --oneline`.

## 5. Definition of Done — T-014 / T-015

| Критерий | Статус | Факт |
| :--- | :---: | :--- |
| Созданы 3 файла промптов | ✅ | `beginner.txt`, `club.txt`, `gm.txt` + загрузчик |
| `build_fact_package` формирует контекст | ✅ | метаданные, ходы, до 10 моментов, PV, мотивы, рекомендации |
| Прогрев LLM в lifespan | ✅ | `warmup_local_llm`, 934 мс, best-effort; KI-022 Closed |
| Валидатор извлекает и проверяет ходы | ✅ | `extract_moves_from_text` + `validate_moves` + `validate_llm_response` |
| Интеграционный тест подтверждает легальность | ✅ | 22 хода, `illegal=[]`, тест 70.47 с |
| `ruff` / `mypy` — 0 ошибок | ✅ | 0 / 0 (33 файла) |
| T-014 и T-015 отмечены `[x]` | ✅ | `TODO.md` |

## 6. Что осталось висеть

- KI-023 — семантическая проверка текста (T-016/T-023).
- KI-024 — скорость локальной генерации (стриминг/фон в T-016).
- KI-019 — recall мотивов не измерен.
