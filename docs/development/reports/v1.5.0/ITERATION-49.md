# Итерация 49 — Data Generation & CJK Detector (v0.2.0, T-104b, KI-060)

**Дата:** 2026-11-03
**Статус:** ✅ завершена — CJK-детектор работает, `build_gold_dataset_v2.py`
умеет читать и PGN, и `positions.jsonl`; реальная генерация через DeepSeek
и обучение не запускались (подготовка данных и кода).
**Формат:** RULES §2.18 (7 разделов).

**Проверка исходного состояния.** KI-030 был закрыт в итерации 48, KI-060 —
открыт; `scripts/build_gold_dataset_v2.py` отсутствовал. В ходе итерации
сосед добавил коммит `89b9498` с `generate_positions.py` (генерирует
`data-chess/positions.jsonl`: `fen`, `best_move` в UCI, `evaluation` в
паунах, `move_number`, `opening`). Самого `positions.jsonl` в рабочем дереве
ещё нет — режим проверен на фикстурах и smoke-файле той же схемы.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `scripts/build_gold_dataset_v2.py` | Генератор v2-датасета: режимы PGN и `--positions`, DeepSeek, SAN+CJK-валидация, `--dry-run` |
| `tests/unit/test_cjk_detector.py` | 26 тестов детектора: диапазон, границы, вырезание, очистка/отказ, подсказки RU/EN |
| `tests/unit/test_commentary_cjk.py` | 8 тестов сервисной петли: один retry, вырезание, отказ, комбинированная подсказка, флаг semantic |
| `tests/unit/test_build_gold_dataset_v2.py` | 36 тестов билдера: MockTransport DeepSeek, PGN, positions, dry-run, CLI |
| `docs/development/reports/v1.5.0/ITERATION-49.md` | Этот отчёт |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/commentary/semantic.py` | Новый языковой слой: `CJK_PATTERN`, `CJK_MAX_SHARE`, `CjkSanitizeResult`, `count_cjk`, `has_cjk`, `cjk_share`, `strip_cjk_sentences`, `sanitize_cjk`, `cjk_warning`; `_SENTENCE_TOKEN` понимает китайские терминаторы `。！？` |
| `app/commentary/service.py` | `_generate_with_validation`: при CJK — ровно один retry с подсказкой «Отвечай только на русском…», затем вырезание/`LLMValidationError`; логи `commentary_cjk_detected`/`_stripped`/`_dropped` без текста LLM |
| `app/engine/validator.py` | Только docstring: проверка алфавита вынесена в `semantic.py` |
| `scripts/train_lora.py` | `DEFAULT_EPOCHS=3.0`, `DEFAULT_LR=2e-4`, help-тексты для `--epochs`/`--lr` (переопределяются флагами) |
| `tests/unit/test_train_lora.py` | +1 тест: `--lr 1e-4 --epochs 1` меняют дефолты и отражаются в `_format_hyperparams` |
| `generate_positions.py` | Только сортировка импортов (файл соседа; иначе `ruff check .` красный) |
| `docs/KNOWN_ISSUES.md` | KI-060 → «Open (частично исправлен)», описание детектора, режимов v2 и остатка (SSE/eval), история |
| `docs/CHANGELOG.md` | `[Unreleased] → Added`: CJK-детектор, v2-билдер (PGN+positions), гиперпараметры v0.2.0 |
| `docs/development/GPU_TRAINING_GUIDE.md` | §6.1 «Обучение v0.2.0 (DeepSeek Teacher)»: сборка PGN/positions, `--lr 1e-4 --epochs 1` |
| `docs/development/RELEASES.md` | Запись об итерации 49 |
| `TODO.md` | T-104b → `[~]`, отмечена подготовка данных/кода |

### Рабочее дерево

`data-chess/` и `data/*.jsonl` — gitignored. Новый `data/gold_dataset_v2.jsonl`
в этой итерации **не создавался** (реальный API не вызывался). Временные
`.pytest_iter49*.log` удалены.

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| CJK-детектор | `test_cjk_detector.py` (26) | «Конь идет на 马e4» → `True`/1; чистый RU/EN → `False`; границы U+4E00/U+9FFF и вне диапазона (U+4DFF, U+A000, U+3400, хирагана); доля без пробелов; вырезание одного предложения, абзацев, всех предложений; `sanitize_cjk` no-op/вырезание/отказ по доле/пустому остатку; порог 0..1; подсказки RU/EN |
| Сервисный retry | `test_commentary_cjk.py` (8) | CJK → один retry с точной русской подсказкой; чистый текст — 1 попытка; EN-подсказка; CJK + нелегальный ход в одной подсказке; вырезание на повторной ошибке; отказ при CJK-only; `semantic_validation_enabled=False` не отключает CJK |
| v2 builder | `test_build_gold_dataset_v2.py` (36) | PGN-контракт `schema_version=2`/роли/metadata; payload DeepSeek (URL, Bearer, модель, temperature, max_tokens, club-промпт, `ТОЛЬКО текст`, факт-пакет); retry нелегального `Qxf7`; CJK retry и вырезание; пропуск CJK-only; dry-run без API; отсутствие ключа; CLI. Positions: `parse_position_record` (пауны/cp/мат/Lichess-объект/UCI `e2e4`/нелегальный best move/битый FEN), `read_positions` с битыми строками, минимальный пакет без истории, run/dry-run/retry/CJK, `--positions` CLI |
| Гиперпараметры | `test_train_lora.py` | Дефолты `3.0`/`2e-4` и переопределение `1`/`1e-4` |
| Регрессия | полная сюита | **1267 passed, 9 skipped, 1 xfailed** |

Новые тесты: **70 passed** (26 + 8 + 36). Промпт-тесты, semantic/validator,
train_lora и integration-сюита не сломаны.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.
**Tests:** 1267/1267 passed, 9 skipped (cairosvg/DB-Redis), 1 xfailed (KI-012).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Полная сюита | `pytest -q` | **1267 passed, 9 skipped, 1 xfailed in ~4.5 min** |
| Новые тесты | `pytest tests/unit/test_cjk_detector.py tests/unit/test_commentary_cjk.py tests/unit/test_build_gold_dataset_v2.py -q` | **70 passed** |
| Semantic/validator | `pytest tests/unit/test_semantic_validation.py tests/unit/test_validator.py tests/integration/test_commentary_semantic.py -q` | **65 passed** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| Типы изменённых | `mypy app scripts/build_gold_dataset_v2.py tests/unit/…` | `Success: no issues found in 94 source files` |

`generate_positions.py` соседа не входит в `mypy app` (CI проверяет только
`app`); ruff в нём исправлен сортировкой импортов.

---

## 4. Smoke

1. **Детектор вживую.**

   ```python
   from app.commentary.semantic import has_cjk, sanitize_cjk
   assert has_cjk("Конь идет на 马e4")
   sanitize_cjk("Партия была интересной. Конь идет на 马e4.").text
   # → "Партия была интересной."
   sanitize_cjk("马马马 这是中文").rejected
   # → True
   ```

2. **PGN dry-run на реальных данных.**

   ```text
   python scripts/build_gold_dataset_v2.py --dry-run --limit 2
   Золотой датасет v2 (DeepSeek Teacher): schema_version=2
   PGN-файлов: 23
   Партий: 2
   Пакетов фактов (dry-run): 2
   Записей: 0 (dry-run, файл не пишется)
   API-вызовов: 0
   ```

3. **Positions dry-run на схеме `generate_positions.py`** (FEN, UCI
   `best_move`, `evaluation` в паунах):

   ```text
   python scripts/build_gold_dataset_v2.py --positions <tmp>.jsonl --dry-run
   Золотой датасет v2 (DeepSeek Teacher, positions): schema_version=2
   Позиций: 2
   Пакетов фактов (dry-run): 2
   Пропущено: 0
   ```

   В пакете: `# Позиция`, `FEN`, `Ход: белые/чёрные`,
   `Оценка движка: +0.35 (за белых)`, `Лучший ход: e4` (UCI `e2e4`
   нормализован в SAN), без `# Партия`/`# Ходы партии`/ключевых моментов.

4. **MockTransport DeepSeek.** Тест проверяет реальный endpoint
   `https://api.deepseek.com/chat/completions`, `Authorization: Bearer`,
   `model=deepseek-chat`, `temperature`, `max_tokens`, system-промпт club +
   «Верни ТОЛЬКО текст комментария», user-пакет фактов. Retry: первый ответ
   `Qxf7` → второй запрос содержит «нелегальные ходы» и `Qxf7`.

5. **Один retry на CJK.** При `max_retries=2` первый CJK-ответ даёт второй
   запрос; если и он с иероглифами — сразу вырезание (не третья попытка).

---

## 5. Что не так

| Ограничение | Детали / почему |
| :--- | :--- |
| Реальный DeepSeek не вызывался | DoD требует MockTransport; платный API/лимиты не тратились. `data/gold_dataset_v2.jsonl` не создан — это следующий шаг T-104b |
| Обучение не запускалось | В итерации 49 нет GPU; задача — только данные и код |
| `data-chess/positions.jsonl` ещё нет | Файл готовит сосед (`generate_positions.py`); схема выведена из его скрипта, проверена фикстурами. Если поля изменятся — добавить синонимы в `parse_position_record` |
| Единицы оценки в positions | Принято: `evaluation`/`eval`/`score` — **пауны** (×100), `eval_cp`/`cp`/`score_cp` — сантипешки. У соседа мат кодируется как `100.0` → отображается `+100.00`, а не `#N` |
| SSE-стриминг без CJK-фильтра | `app/commentary/streaming.py` проверяет только SAN; текст уже ушёл клиенту. Нужен буферизующий фильтр — остаток KI-060 |
| CJK-метрика в eval | `scripts/evaluate_llm.py` пока не считает долю иероглифов; KI-060 закрывается после переобучения и eval |
| Один retry может укоротить текст | Если второй ответ тоже с иероглифами, предложения вырезаются; при CJK-only ответе — 502. Это осознанная цена защиты от дрейфа |
| Запись датасета в конце | `run_build` пишет JSONL после обхода; ошибка API в середине теряет уже сгенерированные записи (поведение v1) |
| `generate_positions.py` соседа | Берёт только первую партию из каждого PGN и не аннотирован типами; мы поправили только импорты для `ruff check .`, логику не меняли |

---

## 6. Новые KI

**Нет.** KI-060 переведён в **Open (частично исправлен)**: детектор,
retry/вырезание и v2-генератор готовы; полное закрытие — после
переобучения 7B на v2-датасете и 5/5 чистых русских комментариев.
Остатки (SSE, eval-метрика, единицы positions) описаны в KI-060 и §5.

---

## 7. Обоснование

- **CJK в `app/commentary/semantic.py`, а не в `validator.py`.** SAN-валидатор
  отвечает за шахматную легальность; язык/алфавит — текстовый слой, который
  уже умеет резать предложения (`strip_invalid_claims`, `_SENTENCE_TOKEN`).
  Так один модуль владеет всеми текстовыми проверками, а `validator.py`
  остаётся чисто шахматным.
- **Один retry, затем вырезание.** Постановка требует «retry (1 раз), затем
  вырезаем». Это дешевле полного цикла из 3 попыток и не даёт модели
  бесконечно переписывать ответ; при доле иероглифов > 10% или пустом
  остатке ответ отклоняется, чтобы не отдавать бессмысленный огрызок.
- **`CJK_MAX_SHARE = 0.10`.** Один случайный иероглиф в длинном тексте
  вырезается вместе с предложением; текст, где иероглифы составляют десятую
  часть, уже не русский комментарий.
- **Два режима в одном скрипте.** Общая часть — DeepSeek-клиент, повторы,
  SAN/CJK-валидация, `schema_version=2`, `--dry-run` — не дублируется.
  Различается только сборка пакета: `build_fact_package` для партии и
  минимальный `build_position_fact_package` для изолированной позиции.
- **Толерантный парсер positions.** Сосед готовит файл параллельно, поэтому
  поддержаны синонимы полей, UCI и SAN для `best_move`, Lichess-объект
  оценки, пауны/cp по имени ключа. Битые JSON/FEN не роняют прогон — запись
  считается в `invalid_position`.
- **MockTransport вместо реального API.** Тесты проверяют весь HTTP-контракт
  DeepSeek (URL, заголовок, payload, ответ, usage) без сети и затрат; реальная
  генерация остаётся операционным шагом T-104b.
- **Переопределение LR/эпох без правки кода.** `DEFAULT_*` используются только
  как значения argparse; v0.2.0-команда передаёт `--lr 1e-4 --epochs 1`,
  старые прогоны воспроизводимы на дефолтах.
