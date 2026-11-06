# Итерация 57 — Chat-SSE CJK & Documentation Update (KI-060, T-120, T-104b, T-122)

**Дата:** 2026-11-06
**Статус:** ✅ завершена — Chat-SSE больше не пропускает китайские
иероглифы (U+4E00–9FFF), eval-скрипт переживает сбой Stockfish на одной
партии, документация обновлена (в т.ч. запись о **556 записях** v4-датасета
без обрезки). GitHub Actions по-прежнему заблокирован billing-ом аккаунта
(внешний блокер итерации 56, не код; DoD этой итерации — локальные
`pytest`/`ruff`).
**Формат:** RULES §2.18 (7 разделов).

**Исходное состояние.** `main` = `b83527d` (итерация 56: CI-фикс
`STOCKFISH_PATH`, T-123, отчёт). KI-060 оставался Open: итерация 53 закрыла
CJK-фильтр только для SSE **комментария**; `app/chat/streaming.py` отдавал
чанки LLM (и legacy semantic-cache) в `delta` без фильтра. Параллельно:
сгенерировано **556 записей** v4-датасета через DeepSeek (максимальный
ответ — **1884 токена**, обрезки нет — `max_tokens=2048` из T-122/итерации
55 решил проблему), а `scripts/evaluate_llm.py` падал целиком, если
`/v1/analyze` бросал исключение на одной партии.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `docs/development/reports/v1.5.0/ITERATION-57.md` | Этот отчёт (7 разделов) |
| `tests/unit/test_evaluate_llm_analyze_errors.py` | 3 офлайн-теста: сбой `/v1/analyze` не роняет eval-прогон, error-строка на каждый уровень, `KeyboardInterrupt` не глотается |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/streaming.py` | Общий SSE-модуль: сюда перенесены `CjkStreamBuffer`, `_SENTENCE_END`, `MIN_DELTA_CHARS` (20), `MAX_DELTA_CHARS` (30); `__all__` расширен. Импортирует `count_cjk`/`has_cjk`/`strip_cjk_sentences` из `app.commentary.semantic` (циклов нет) |
| `app/commentary/streaming.py` | Локальная копия класса/констант/`import re` удалена; буфер ре-экспортируется из `app.streaming` (`__all__` сохранён), поведение `stream_commentary` не изменилось |
| `app/chat/streaming.py` | CJK-фильтр в Chat-SSE: LLM-чанки → `CjkStreamBuffer`; legacy cache-hit → `sanitize_cjk` (rejected/пустой → регенерация через LLM); `reply` для SAN-валидации, токенов и `store()` — очищенный текст; CJK-only → `event: error` (`llm_empty`); логи только со счётчиками (`chat_cjk_stream_stripped`, `chat_cache_cjk_dropped`) |
| `tests/integration/test_streaming.py` | +4 теста Chat-SSE: чистые `delta`, CJK-only → error, грязный cache-hit очищается, отклонённый cache → fallback на LLM |
| `scripts/evaluate_llm.py` | Per-game `POST /v1/analyze` обёрнут в `try/except Exception`: диагностика в stdout, по error-строке на каждый уровень, `continue` к следующей партии; non-200 и остальная логика не тронуты |
| `docs/CHANGELOG.md` | `[Unreleased] → Added`: CJK-фильтр chat-SSE, v4-датасет 556/1884; `Fixed`: eval не падает на сбое Stockfish |
| `docs/KNOWN_ISSUES.md` | KI-060 (остаётся **Open (частично исправлен)**): остаток «SSE чата без фильтра» заменён на «стриминговый остаток закрыт в итерации 57»; обновлены таблица KI и строка истории |
| `docs/development/RELEASES.md` | Раздел «Итерация 57» после итерации 56 (chat-SSE, 556 записей v4/1884 токена, eval, метрики) |
| `TODO.md` | T-120: chat-SSE-остаток закрыт (итерация 57); T-104b: 556 записей v4, max 1884 токена, без обрезки |

---

## 2. Покрытие

| Область | Тест | Что покрыто |
| :--- | :--- | :--- |
| Chat-SSE: LLM с CJK | `test_chat_stream_filters_cjk_from_deltas` | мок-LLM `MIXED_CHUNKS`; ни один `delta` не содержит CJK (независимый `_CJK_PATTERN`, не прод-детектор), склейка `delta` == `CLEAN_MIXED_TEXT`, `done.validated=true` |
| Chat-SSE: только CJK | `test_chat_stream_all_cjk_returns_error_without_saving` | `delta` нет, последнее событие `error`/`llm_empty`, detail про китайские иероглифы, записей нет |
| Chat-SSE: legacy cache | `test_chat_stream_sanitizes_dirty_cache_hit` | cache-hit с CJK очищается (`sanitize_cjk`), `meta.model=semantic-cache`, `delta` без CJK == чистый текст |
| Chat-SSE: rejected cache | `test_chat_stream_rejected_cache_falls_back_to_llm` | cache только из CJK → `meta.model=fake-stream` (регенерация), `delta` == чистый LLM-текст |
| Буфер (регресс) | `tests/unit/test_streaming_cjk.py` (16) | перенос в `app/streaming.py` не изменил поведение (файл тестов не менялся) |
| Eval: сбой analyze | `test_analyze_exception_does_not_abort_run` | 2 партии, первая падает → вторая обрабатывается (2 вызова), 2 error-строки, диагностика в stdout |
| Eval: уровни | `test_analyze_exception_adds_error_row_per_level` | одна партия × `club,gm` → 2 error-строки, исключение не выходит наружу |
| Eval: BaseException | `test_keyboard_interrupt_is_not_swallowed` | `except Exception` не перехватывает Ctrl+C |
| Adversarial (ревью) | fuzz независимого ревьюера | 3000 случайных разбиений CJK/латиницы и 4000 прогонов `sanitize_cjk` → **0** утечек CJK в отданном тексте |

Новые тесты: **7** (4 integration + 3 unit). Целевой прогон:
`tests/integration/test_streaming.py` — **13 passed**, unit-наборы буфера и
eval — **44 passed**.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** **1312 passed, 15 skipped, 1 xfailed** (166.70 с, exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Полная сюита | `pytest -q` | **1312 passed, 15 skipped, 1 xfailed in 166.70 s** (exit 0) |
| Chat-SSE | `pytest tests/integration/test_streaming.py -q` | **13 passed** (включая 4 новых), 0 skipped |
| Буфер CJK | `pytest tests/unit/test_streaming_cjk.py -q` | **16 passed** (файл не менялся) |
| Eval-устойчивость | `pytest tests/unit/test_evaluate_llm_analyze_errors.py tests/unit/test_evaluate_llm_explanations.py tests/unit/test_evaluate_llm_cjk.py -q` | **28 passed**; все 4 unit-файла — **44 passed** |
| Token budget (CI-подобное окружение) | `POSTGRES_PORT=5437 REDIS_URL=…6385 pytest tests/integration/test_token_budget_api.py -q` | **7 passed** (локальные 7 skip — env-mismatch, см. §5) |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| Импорт приложения | `python -c "import app.main"` | `import ok` (циклов нет) |
| GitHub Actions | run для `b83527d`/итерации 56 | ❌ billing-блокер аккаунта (все job падают до шага 1); в этой итерации push тоже даст красный бейдж, пока billing не исправлен |

**Примечание по skip (15).** 8 — live-тесты LM Studio/DeepSeek
(`test_api_chat`, `test_api_commentary`, `test_embeddings_live`,
`test_generation`, `test_llm_live`); 7 — `test_token_budget_api`, который
конструирует `Settings(_env_file=None)` с дефолтными портами (5434/6380), а
локальный compose поднимает 5437/6385. С CI-подобными env-переменными эти
7 тестов проходят (**7 passed**); в CI нужные переменные заданы в workflow.
Это окружение, не регресс.

---

## 4. Smoke

1. **Chat-SSE с иероглифами.** В интеграционном тесте мок-LLM шлёт
   `["Партия ", "началась с d4. ", "这是中文。", "Затем ", "последовало Nf6."]`;
   клиент получает `meta` → `delta…` (без единого CJK-символа) → `done`,
   склейка — `«Партия началась с d4. Затем последовало Nf6.»`. При
   CJK-only чанках — `error`/`llm_empty`, `delta` нет.
2. **Legacy cache.** Мок `lookup` возвращает грязную строку с `这是中文。` —
   в `delta` уходит очищенный текст, `meta.model=semantic-cache`; если после
   очистки пусто — `meta.model=fake-stream` (свежая генерация).
3. **Eval.** Фейковый `TestClient.post("/v1/analyze")` бросает
   `RuntimeError("Stockfish упал")`: скрипт печатает
   `ANALYZE …: ошибка RuntimeError: Stockfish упал`, добавляет error-строку
   на уровень и переходит к следующей партии; `KeyboardInterrupt`
   пробрасывается.
4. **v4-датасет.** `data/gold_dataset_v2_full.jsonl` — **556** записей,
   `teacher_provider=deepseek-flash`, 0 иероглифов в assistant-сообщениях;
   максимальный ответ **1884 токена** (из прогона генерации) при лимите
   `max_tokens=2048` — обрезки (`finish_reason=length`) нет. Именно этот
   фикс (T-122) снял прежние **729 из 824** отказов `invalid_llm`.
5. **Регресс комментария.** `pytest tests/unit/test_streaming_cjk.py` —
   16 passed; `stream_commentary` использует тот же класс через ре-экспорт.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| **GitHub Actions заблокирован billing-ом** | Все job-ы падают до первого шага: «recent account payments have failed or your spending limit needs to be increased» (итерация 56). Красный бейдж — не следствие этой итерации; после фикса Billing нужен `gh run rerun`/новый push. DoD-57 требовал локальные `pytest`/`ruff` — они зелёные |
| KI-060 остаётся **Open** | Закрыт только стриминговый остаток (Chat-SSE). Корневая причина — языковой дрейф LoRA 7B — устраняется переобучением на v4 (500+); диапазон детектора по-прежнему U+4E00–9FFF, полного v4-eval на обученной модели не было |
| Non-SSE `POST /v1/chat` без CJK-фильтра | DoD итерации — только Chat-SSE (streaming); синхронный чат-эндпоинт остаётся вне scope, как и в комментарии (там защита живёт в сервисе) |
| TTFT/печать в чате | Из-за буферизации первый `delta` приходит позже (до конца предложения или 20–30 символов) — осознанный компромисс ради отсутствия утечек, задокументирован в KI-060 |
| CJK-only detail — только RU | Для `language="en"` detail остаётся русским (как в commentary-SSE); локализация потребовала бы новой записи в `app/i18n.py` — кандидат в следующую итерацию |
| Отброшенный грязный cache-hit не пишет `record_cache_hit` | Небольшой недосчёт cache-hit метрики при отклонённой кэш-записи; поведение осознанное (ответ сгенерирован заново) |
| Связность `app.streaming` → `app.commentary.semantic` | Общий SSE-модуль теперь тянет пакет commentary (service/DB/LLM-импорты) в low-level модуль; циклов нет (проверено `import app.main` в разных порядках), но это будущий риск связности |
| Локальные 15 skip | 8 — LM Studio/DeepSeek live, 7 — `test_token_budget_api` с дефолтными портами (5434/6380) при compose 5437/6385; с CI-подобными env проходят. В CI переменные заданы |
| 1884 токена не воспроизводимы офлайн | Цифра из прогона генерации (нет токен-счётчиков в датасете); независимо проверено только наличие 556 записей, `deepseek-flash` и 0 CJK в assistant-тексте |

---

## 6. Новые KI

**Нет.** Это закрытие остатка существующего KI-060 (стриминговый слой) и
подготовка eval/документации, а не новая проблема. **KI-060** остаётся
**Open (частично исправлен)**: обновлены раздел «Остаток», таблица KI и
строка истории — «Chat-SSE теперь фильтруется». Новых KI не требуется;
выявленные ограничения (non-SSE чат, RU-only detail, связность модулей)
носят характер документации/будущих улучшений.

---

## 7. Обоснование

* **Почему класс вынесен в `app/streaming.py`, а не скопирован в
  `app/chat/streaming.py`.** Копия нарушила бы принцип единственного
  источника правды: два буфера разошлись бы при первой же правке. Общий
  SSE-модуль — естественное место для «формата и безопасной выдачи
  `delta`», оба стрима уже импортируют оттуда `sse_event`/`chunk_text`;
  `app.commentary.streaming` сохраняет ре-экспорт, поэтому публичные
  импорты и unit-тесты не меняются.
* **Почему фильтруются и LLM-чанки, и cache-hit.** Иероглифы могут прийти
  двумя путями: от модели сейчас и из старых записей semantic-cache,
  сохранённых до итерации 53. Cache-hit очищается `sanitize_cjk`; если от
  записи ничего не осталось — она считается непригодной и чат генерирует
  ответ заново, а не отдаёт пустоту.
* **Почему SAN-валидация и `store()` работают по очищенному тексту.**
  Клиент видит только очищенный поток; сохранять/проверять другой текст
  означало бы, что ответ в БД отличается от показанного (и нелегальный
  ход мог бы «спрятаться» в вырезанном CJK-предложении). Это тот же
  контракт, что в `stream_commentary` с итерации 53.
* **Почему `except Exception`, а не голый `except`.** Eval не должен
  глотать `KeyboardInterrupt`/`SystemExit`; `except Exception` покрывает
  сбои Stockfish/ASGI, но оставляет управление пользователю. Error-строка
  на каждый уровень сохраняет форму отчёта (строка = игра×уровень), а
  `_judge`/`_save_evaluations` уже пропускают строки с `error`.
* **Почему документация важна как код.** 556 записей v4 — ключевой
  результат, который меняет план закрытия KI-060 (переобучение 7B теперь
  возможно на выборке ≥500); фиксация в RELEASES/CHANGELOG/TODO
  предотвращает потерю контекста между итерациями. KI-060 не закрывается
  формально: причина (дрейф модели) не устранена, закрыт только её
  стриминговый симптом.
* **Почему DoD = локальные pytest/ruff, а не GitHub CI.** Actions
  заблокирован на уровне аккаунта (billing) с итерации 56; это внешнее
  состояние, которое нельзя исправить из репозитория. Локальный полный
  прогон (1312 passed) + независимое ревью и Linux-контейнерная проверка
  итерации 56 — максимально доступное подтверждение.
