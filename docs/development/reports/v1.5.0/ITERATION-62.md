# Итерация 62 — V4 Final Eval Prep (T-129)

**Дата:** 2026-11-07
**Статус:** ✅ завершена — добавлены `scripts/compare_evals.py`, гайд
`docs/development/V4_EVAL_GUIDE.md` и 28 unit-тестов; baseline v3
(**3.60/10**, 5/5 партий) зафиксирован на замороженном наборе;
`pytest`/`ruff`/`mypy` зелёные.
**Формат:** RULES §2.18 (7 разделов).

**Исходное состояние.** `main` = `c5cb432` (официальный baseline v3 на
`tests/fixtures/eval_games.json`: Факты **2.60**, Польза **3.40**, Стиль
**4.80**, средняя **3.60/10**, объяснения **60%**, CJK **0.00%**, 5/5
партий). Для v4 нужен воспроизводимый прогон на том же наборе и наглядное
сравнение, но готового инструмента не было: `evaluate_llm.py` пишет JSON,
а дельты считались вручную по `LLM_EVAL.md`.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `scripts/compare_evals.py` (667 строк) | CLI сравнения двух JSON-прогонов: Markdown-таблица дельт по трём шкалам, средней, объяснениям и CJK; проверки same-sample и меток модели; `--output`, `--strict-samples`; UTF-8-вывод на Windows |
| `tests/unit/test_compare_evals.py` (526 строк) | **28** офлайн-тестов: загрузка/валидация JSON, средние по шкалам, объяснения, CJK, формат таблицы, CLI и коды возврата, старый JSON без метрик, UTF-8 |
| `docs/development/V4_EVAL_GUIDE.md` (344 строки) | Гайд: сохранить baseline, переключить `.env` на v4 (LoRA/GGUF), прогнать `evaluate_llm.py`, проверить fallback, сравнить и интерпретировать |
| `docs/development/reports/v1.5.0/ITERATION-62.md` | Этот отчёт (7 разделов) |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `TODO.md` | +T-129 (**[x]**) с описанием скрипта, гайда и тестов |
| `docs/CHANGELOG.md` | `[Unreleased] → Added`: сравнение v3/v4 и гайд финального eval |
| `docs/development/RELEASES.md` | Раздел «Итерация 62» + таблица официального baseline v3 (3.60/10) |
| `README.md` | Бейдж/счётчик backend-тестов (1412), строка итерации 62, baseline 3.60/10, ссылка на гайд |
| `docs/README.md` | Индекс документации: гайд `V4_EVAL_GUIDE.md` и отчёты `v1.5.0` |

---

## 2. Покрытие

| Область | Тест | Что покрыто |
| :--- | :--- | :--- |
| Средние по шкалам | `test_compute_metrics_averages_each_scale` | Среднее по 3 шкалам и средняя из них |
| Неоценённые строки | `test_unscored_rows_do_not_change_scale_averages` | `scores={}` не разбавляет средние, `scored` считает только оценённые |
| Выборка | `test_levels_and_sample_keys_are_collected` | Уровни и ключи `(game_id, level)` для same-sample |
| Объяснения | `test_explanation_rate_excludes_generation_errors` | Сбой генерации (`commentary_id=None`) вне знаменателя |
| Объяснения | `test_explanation_rate_includes_judge_errors_when_text_exists` | Сбой только судьи (`commentary_id` есть) остаётся в знаменателе |
| Объяснения | `test_explanation_is_unavailable_for_old_json_schema` | Старый JSON без `has_explanation` → «—», не падение |
| CJK | `test_cjk_share_is_mean_of_comment_shares` | Средняя по комментариям, число комментариев с CJK, сумма иероглифов |
| CJK | `test_cjk_is_unavailable_for_old_json_schema` | Старый JSON без `cjk_*` → «—» |
| Таблица | `test_table_has_scale_rows_and_signed_deltas` | Строки шкал, средняя, знак `+` |
| Таблица | `test_table_shows_negative_and_zero_deltas` | `-1.00` и `0.00` |
| Таблица | `test_table_has_explanation_and_cjk_rows` | Дельты объяснений/CJK в п.п. |
| Таблица | `test_table_verdict_mentions_growth_and_threshold` | Итоговая строка и порог 7/10 |
| Совместимость | `test_old_schema_renders_dashes_instead_of_crashing` | Старый JSON: обе метрики «—» |
| CLI | `test_cli_prints_table_and_returns_zero` | Код 0 и таблица в stdout |
| CLI | `test_cli_derives_labels_from_model_name` / `test_cli_accepts_label_overrides` | `v3/v4` из `model_name`, ручные подписи |
| CLI | `test_cli_writes_markdown_to_output_file` | `--output`, завершающий перевод строки |
| CLI | `test_cli_missing_file_returns_one`, `test_cli_invalid_json_returns_one`, `test_cli_non_list_payload_returns_one`, `test_cli_empty_list_returns_one`, `test_cli_score_out_of_range_returns_one` | Ошибки ввода → код 1 с понятным сообщением |
| CLI | `test_cli_warns_when_samples_differ`, `test_cli_strict_samples_returns_one` | Предупреждение о межвыборочных дельтах; `--strict-samples` → 1 |
| CLI | `test_cli_warns_when_model_labels_are_equal` | Забыли `--model-name` для v4 |
| Схема | `test_load_records_rejects_non_object_item` | Не-объект в массиве → `EvalFileError` |
| Windows | `test_configure_utf8_output_*` | `reconfigure(encoding="utf-8")`; поток без `reconfigure` не ломает запуск |

Новые тесты: **28**. Регресс существующих eval-тестов
(`test_evaluate_llm_*.py`) не затронут — `evaluate_llm.py` не менялся.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** **1412 passed, 9 skipped, 1 deselected, 1 xfailed** (188.68 с,
exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Новый файл тестов | `pytest -q tests/unit/test_compare_evals.py` | **28 passed** (0.14 с) |
| Полная сюита | `pytest -q --deselect tests/integration/test_llm_live.py::test_live_gm_uses_deepseek` (PostgreSQL `5437`, Redis `6385`, реальный Stockfish) | **1412 passed, 9 skipped, 1 deselected, 1 xfailed in 188.68 s** (exit 0) |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app scripts/compare_evals.py scripts/evaluate_llm.py` | `Success: no issues found in 93 source files` |
| CLI (справка) | `python scripts/compare_evals.py --help` | Флаги `baseline/new`, `--baseline-label`, `--new-label`, `--output`, `--strict-samples` |
| Smoke | `compare_evals.py llm_eval_results.json llm_eval_results.json --baseline-label v3 --new-label v3-copy` | exit 0; дельты `0.00`; таблица совпала с baseline `2.60/3.40/4.80` |

**Skip (9):** 7 env-зависимых `token_budget` + 2 теста без `libcairo2`
(`test_commentary_pdf`, `test_pdf_board`). **Deselected (1):** live DeepSeek.
**Xfailed (1):** маркер KI-012 (`test_parse_pgn_unknown_token_is_rejected`).

---

## 4. Smoke

Реальный baseline v3 против себя — проверка загрузки, метрик и формата:

```text
# Сравнение LLM-eval: v3 (Baseline) → v3-copy (New)

**Baseline:** `llm_eval_results.json` · модель `qwen-7b-tokenizer_gguf-v3` · записей: 5, оценено: 5 · уровни: `club`
**New:** `llm_eval_results.json` · модель `qwen-7b-tokenizer_gguf-v3` · записей: 5, оценено: 5 · уровни: `club`

| Шкала | v3 (Baseline) | v3-copy (New) | Дельта |
| :--- | ---: | ---: | ---: |
| Факт. точность | 2.60 | 2.60 | 0.00 |
| Полезность | 3.40 | 3.40 | 0.00 |
| Стиль | 4.80 | 4.80 | 0.00 |
| **Средняя** | **3.60** | **3.60** | **0.00** |
| Объяснения | 60.0% (3/5) | 60.0% (3/5) | 0.0 п.п. |
| CJK-символы | 0.00% (0/5) | 0.00% (0/5) | 0.00 п.п. |

**Итог:**

- Средняя: **3.60 → 3.60** (без изменений); порог **7/10** не достигнут.
- Объяснения: **60.0% → 60.0%** (0.0 п.п.).
- CJK: **0.00% → 0.00%** (0.00 п.п.).
```

Код возврата — `0`; в stderr ожидаемое предупреждение о совпавшей метке
модели. Числа совпали с официальным `LLM_EVAL.md` (2.60/3.40/4.80, 3.60,
60%, 0.00%), то есть скрипт читает тот же baseline без искажений.

Проверка Windows-консоли: до фикса `print` падал на cp1251 на символе `→`
(`UnicodeEncodeError`); после `_configure_utf8_output()` таблица печатается
полностью. Потоки без `reconfigure` (тесты, перенаправление) не ломаются.

---

## 5. Что не так

- **v4 не прогнана.** В окружении нет обученной v4/поднятой модели, поэтому
  реальной дельты v3↔v4 в этой итерации нет. Проверены скрипт, гайд и
  baseline-путь; сам прогон — следующий шаг по `V4_EVAL_GUIDE.md`.
- **Один транзиентный сбой полной сюиты.** Во втором контрольном прогоне
  `test_analyze_example_pgn_meets_sla_and_persists` упал по таймауту
  UCI-handshake Stockfish (15 с) под нагрузкой; отдельно и в повторном
  полном прогоне — **passed**. Изменения итерации (JSON-скрипт и его тесты)
  движка не касаются; стабильного воспроизведения нет (1 прогон из 3),
  поэтому новый KI не заводился. Если сбой повторится — увеличить
  `_UCI_STARTUP_S` или добавить один retry на старте движка.
- **5 партий — мало.** Разница 0.2–0.4 балла сопоставима с шумом судьи;
  окончательное решение по v4 требует 30–50 партий (T-024, KI-023). Это
  ограничение методики, а не скрипта.
- **CJK в сравнении — средняя по комментариям доля**, а не агрегатная
  «иероглифы / все символы» из `LLM_EVAL.md`: в JSON нет длины текста,
  восстановить агрегат нельзя. Для обеих версий метрика считается
  одинаково, на baseline обе дают `0.00%`; расхождение возможно только при
  разной длине комментариев и ненулевом CJK — оговорка внесена в гайд и
  docstring.
- **Знаменатель объяснений — эвристика по `commentary_id`/`error`.**
  Строка, где упал только судья, считается «с текстом» (объяснение
  известно), строка со сбоем генерации — нет. В текущем JSON это
  различие однозначно, но при ручной правке схемы его можно потерять.
- **Строгая валидация схемы.** Записи с оценкой вне 1..10, NaN/Infinity
  или неверными типами отклоняются с кодом 1, а не «тихо» игнорируются.
  Это осознанно: сравнение должно падать на битом файле, а не показывать
  ложные дельты.
- **GitHub Actions не запускался.** Локальный полный прогон зелёный; CI
  проверит ветку после push.
- **Остальные известные ограничения не изменились:** LLM-as-judge bias,
  валидатор проверяет SAN, а не атрибуцию оценок (KI-023), CJK-дрейф
  лечится переобучением (KI-060).

---

## 6. Новые KI

**Нет.** Новых дефектов не обнаружено: оба «сюрприза» итерации (падение
`print` на cp1251 и отсутствие длины текста в JSON) — ограничения
представления/окружения, а не баги логики; они обработаны в коде и
задокументированы в §5.

---

## 7. Обоснование

- **Почему отдельный `compare_evals.py`, а не флаг `evaluate_llm.py`.**
  `evaluate_llm.py` отвечает за генерацию и судью (сеть, БД, Stockfish);
  сравнение — чистая операция над двумя артефактами. Отдельный скрипт
  запускается без БД/модели, тестируется на tmp-JSON и не рискует сломать
  eval-пайплайн.
- **Почему вход — JSON, а не `LLM_EVAL.md`.** JSON — машинный контракт
  (`scores`, `has_explanation`, `cjk_*`), он стабилен и уже пишется
  `_dump_json`. Парсинг Markdown был бы хрупким: таблицы и подписи менялись
  между итерациями.
- **Почему дельты в «п.п.» для процентов.** Абсолютная разница 60% → 80%
  — это `+20 п.п.`, а не `+20%` (относительный рост был бы +33%). Для шкал
  1–10 дельта — обычные баллы (`+0.60`).
- **Почему проверка same-sample встроена.** Дельты между разными наборами
  партий бессмысленны (итерация 52, T-024/KI-023). Скрипт предупреждает по
  `(game_id, level)`, а `--strict-samples` позволяет сделать это жёстким
  условием в CI. Отдельно проверяется совпадение метки модели — самая
  вероятная ошибка при копировании команды.
- **Почему подписи берутся из `model_name`.** Итерация 60 добавила метку
  явно, потому что API может вернуть базовое имя вместо имени адаптера.
  Из `qwen-7b-tokenizer_gguf-v3` скрипт делает короткое `v3`; если метки
  нет — fallback на имя файла.
- **Почему метрики объяснений/CJK считаются так.** Объяснения — по
  маркерам, уже записанным в JSON (`has_explanation`), сбой генерации не
  должен занижать долю. CJK — средняя по комментариям, единственная
  величина, полностью восстановимая из текущего JSON; агрегатную долю
  можно будет добавить, если в `_dump_json` появится длина текста (не
  требуется для v4-сравнения и не меняет схему baseline).
- **Почему UTF-8-фикс в скрипте, а не в оболочке.** Скрипт должен
  работать «из коробки» в PowerShell/CI без `chcp 65001`; `reconfigure`
  безопасен для не-консольных потоков и покрыт тестом.
- **Почему гайд отдельный, а не раздел `LLM_EVAL.md`.** `LLM_EVAL.md` —
  артефакт конкретного прогона; инструкция по запуску v4 должна жить
  рядом с `GPU_TRAINING_GUIDE.md` и не переписываться при каждом eval.
  В гайде зафиксированы две ловушки, которые уже стоили времени:
  перезапись baseline-JSON по умолчанию и «молчаливый» fallback `club` в
  DeepSeek.
