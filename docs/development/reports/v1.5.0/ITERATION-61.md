# Итерация 61 — Fix Judge Token Truncation (T-128)

**Дата:** 2026-11-06
**Статус:** ✅ завершена — у судьи LLM-as-judge отдельный лимит ответа
(`LLM_JUDGE_MAX_TOKENS=2048`), JSON больше не обрезается на 1024; baseline
v3 (**3.89/10**) зафиксирован на замороженном наборе.
**Формат:** RULES §2.18 (7 разделов).

**Исходное состояние.** `main` = `a1f3270` (прогон v3 на замороженном
наборе: средняя **3.89/10**, 80% объяснений, 0% CJK). Из 5 партий
**2 упали** с ошибкой `judge: в ответе судьи нет JSON-объекта`: судья
(DeepSeek через прокси `opencode.ai`, модель `deepseek-v4.1-flash`) получал
общий лимит `LLM_MAX_TOKENS=1024`, и его JSON-ответ обрезался — `_extract_json`
не находил закрывающую скобку. Оценка 3.89/10 посчитана по 3 из 5 партий,
то есть baseline занижен выборкой.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `tests/unit/test_evaluate_llm_judge_tokens.py` | 6 офлайн-тестов: дефолт 2048 при общем 1024, env-override, `max_tokens` в payload, кастомный лимит, `_judge` через `_judge_client`, обрезанный JSON → ошибка строки |
| `docs/development/reports/v1.5.0/ITERATION-61.md` | Этот отчёт (7 разделов) |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/config.py` | Новая настройка `llm_judge_max_tokens: int = 2048` (env `LLM_JUDGE_MAX_TOKENS`) рядом с `llm_max_tokens`; docstring объясняет причину (обрезка JSON на 1024) |
| `.env.example` | `LLM_JUDGE_MAX_TOKENS=2048` + комментарий |
| `scripts/evaluate_llm.py` | `_judge_client(settings, *, client=None)` подставляет `llm_max_tokens = llm_judge_max_tokens`; `_judge` использует его и печатает `Судья: deepseek, max_tokens=…`; в отчёт добавлена строка о фактическом лимите; docstring сценария обновлён; `import httpx` для типа |
| `docs/CHANGELOG.md` | `[Unreleased] → Fixed`: лимит судьи 2048, потерянные 2/5 партий baseline |
| `docs/development/RELEASES.md` | Раздел «Итерация 61» + таблица baseline v3 (3.89/10) |
| `TODO.md` | +T-128 (**[x]**) |
| `README.md` | Бейдж/счётчик backend-тестов, строка итерации 61 |

---

## 2. Покрытие

| Область | Тест | Что покрыто |
| :--- | :--- | :--- |
| Дефолт | `test_judge_max_tokens_default_is_2048` | `llm_judge_max_tokens == 2048`, общий `llm_max_tokens == 1024` (комментарии не изменились) |
| Env | `test_judge_max_tokens_env_override` | `LLM_JUDGE_MAX_TOKENS=4096` из окружения |
| Payload судьи | `test_judge_client_sends_judge_max_tokens` | `max_tokens=2048` в JSON-запросе к DeepSeek, provider/model/endpoint |
| Кастомный лимит | `test_judge_client_honours_custom_limit` | `llm_judge_max_tokens=4096` → payload 4096 |
| `_judge` end-to-end | `test_judge_uses_judge_max_tokens_setting` | `_judge` собирает клиента с `llm_max_tokens=2048`, разбирает JSON, заполняет scores/comment/tokens, закрывает клиента |
| Устойчивость | `test_judge_records_truncated_json_as_row_error` | Если ответ всё же обрезан — ошибка только у строки, прогон не падает |
| Регресс | `test_evaluate_llm_model_name.py`, `test_evaluate_llm_frozen.py`, `test_evaluate_llm_cjk.py` и др. | Отчёт/JSON/frozen-набор не сломаны |

Новые тесты: **6**.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** **1386 passed, 7 skipped, 1 deselected, 1 xfailed** (245.87 с,
exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Полная сюита (CI-подобно) | `pytest -q --deselect tests/integration/test_llm_live.py::test_live_gm_uses_deepseek` при живых PostgreSQL `5437`/Redis `6385` | **1386 passed, 7 skipped, 1 deselected, 1 xfailed in 245.87 s** (exit 0) |
| Eval-набор тестов | `pytest -q tests/unit/test_evaluate_llm_judge_tokens.py …` (6 файлов) | **63 passed** |
| Новый файл | `pytest -q tests/unit/test_evaluate_llm_judge_tokens.py` | **6 passed** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app scripts/evaluate_llm.py` | `Success: no issues found in 92 source files` |
| Миграции | `alembic check` | `No new upgrade operations detected.` |
| CI | GitHub Actions после push | (заполняется после прогона) |

**Skip (7):** `test_token_budget_api` с env-зависимыми портами (в CI
проходят). В отличие от итераций 58–60, **8 live-тестов LM Studio на этот
раз выполнились** (локальная модель стала доступна в окружении) — поэтому
passed больше, а skipped меньше. **Deselected (1):** live DeepSeek-тест.

---

## 4. Smoke

| Сценарий | Команда | Результат |
| :--- | :--- | :--- |
| Дефолты | `python -c` (см. ниже) | `judge max_tokens: 2048`, `global: 1024` |
| Строка отчёта | `_report(..., judge_max_tokens=2048)` | `- Лимит ответа судьи: **2048 токенов** (LLM_JUDGE_MAX_TOKENS); при общем лимите 1024 JSON обрезался и партия терялась (итерация 61).` |
| Клиент судьи | `_judge_client(Settings(deepseek_api_key="test-key"))` | `OpenAICompatibleClient`, provider `deepseek`, model `deepseek-chat`, endpoint `https://api.deepseek.com/chat/completions` |
| Payload | unit-тест с `MockTransport` | `max_tokens == 2048` (и 4096 при override) |
| Живой перепрогон v3 | — | Не выполнялся в этой итерации: нужен живой судья/прокси; фикс проверен mock-тестами |

Фактический вывод smoke:

```text
judge max_tokens: 2048 | global: 1024
report: - Лимит ответа судьи: **2048 токенов** (`LLM_JUDGE_MAX_TOKENS`); при общем лимите 1024 JSON обрезался и партия терялась (итерация 61).
judge client: OpenAICompatibleClient | provider: deepseek | model: deepseek-chat | endpoint: https://api.deepseek.com/chat/completions
```

---

## 5. Что не так

- **Перепрогон v3 не выполнен.** В среде нет настроенного судьи через
  прокси `opencode.ai` и полного стека для живого eval; фикс проверен
  mock-тестами (`max_tokens` в payload) и smoke-сборкой клиента. Повторный
  прогон даст 5/5 оценённых и уточнённую среднюю.
- **Baseline 3.89/10 занижен выборкой 3/5.** Это не ошибка расчёта:
  скрипт честно считает среднюю по оценённым, а 2 партии не были оценены.
  После фикса baseline нужно пересчитать — сравнивать v4 с 3.89/10 можно,
  но с оговоркой, что выборка baseline была 3/5.
- **Лимит 2048 — эвристика, не измерение.** JSON судьи короткий (3 оценки +
  одно предложение), 2048 с большим запасом; если модель начнёт добавлять
  рассуждения, может понадобиться 4096 — настройка вынесена в env.
- **Судья не ретраится при обрезке.** Если ответ всё же обрезан, строка
  помечается ошибкой, а прогон продолжается (так задумано с итерации 57);
  автоматический повтор с `max_tokens=4096` — возможное улучшение, но не
  требование.
- **8 live LM Studio тестов в этом прогоне прошли**, потому что модель стала
  доступна в окружении; в CI они по-прежнему скипаются без LM Studio —
  числа passed/skipped локально и в CI будут различаться.

---

## 6. Новые KI

**Нет.** Обрезка JSON была не багом логики, а неверным лимитом — исправлена
и покрыта тестами; остальные ограничения (нет живого перепрогона, выборка
baseline 3/5) известны и задокументированы.

---

## 7. Обоснование

- **Почему отдельная настройка, а не `max_tokens=2048` глобально.**
  `LLM_MAX_TOKENS` управляет длиной обычных комментариев (1024 — осознанный
  лимит стоимости/латентности). Судья — служебный вызов с JSON-ответом;
  смешивать их значило бы удвоить стоимость каждого комментария ради
  редкого eval-скрипта. `LLM_JUDGE_MAX_TOKENS` — узкая настройка с
  отдельным env-именем.
- **Почему `_judge_client` — отдельная функция.** Это единственное место,
  где лимит судьи применяется; тесты проверяют его напрямую, а `_judge`
  не знает о деталях сборки клиента. Заодно `client=`-параметр позволяет
  проверять payload через `MockTransport` без сети.
- **Почему 2048, а не 4096.** JSON судьи — 3 числа и одно предложение;
  2048 с запасом покрывает ответ и не увеличивает стоимость. Настройка
  вынесена в env, чтобы при необходимости поднять без правок кода.
- **Почему строка о лимите попала в отчёт.** Артефакт eval должен
  объяснять, почему партия не оценена; теперь `LLM_EVAL.md` фиксирует
  фактический лимит судьи, и будущие прогоны с обрезкой видны сразу.
- **Почему baseline записан в RELEASES, а не только в CHANGELOG.**
  RELEASES — журнал итераций и точка сравнения версий; 3.89/10 на
  замороженном наборе — официальный baseline для v4, поэтому он
  зафиксирован рядом с описанием прогона (сам отчёт и JSON уже в
  `LLM_EVAL.md`/`llm_eval_results.json` из коммита `a1f3270`).
