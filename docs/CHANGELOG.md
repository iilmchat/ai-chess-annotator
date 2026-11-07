# Changelog

Все значимые изменения проекта ChessAnnotator.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Сравнение v3/v4 и гайд финального eval (T-129, итерация 62):**
  `scripts/compare_evals.py` читает два JSON-прогона `evaluate_llm.py` и
  печатает Markdown-таблицу с дельтами по трём шкалам, доле объяснений и
  CJK-дрейфу; проверяет same-sample (`game_id`/`level`), предупреждает о
  совпавших метках модели, умеет `--output`, `--baseline-label`,
  `--new-label`, `--strict-samples` и переводит Windows-консоль в UTF-8.
  Гайд [`docs/development/V4_EVAL_GUIDE.md`](development/V4_EVAL_GUIDE.md):
  сохранение baseline v3, переключение `.env` на v4 (LoRA-адаптер или
  смердженный GGUF), команда прогона, обязательная проверка fallback в
  DeepSeek, команда сравнения и интерпретация результата. Тесты:
  `tests/unit/test_compare_evals.py` (+28).
- **GPU-туннель: настройки в `Settings` и гайд (T-123/T-126, итерация 59):**
  `LLM_LOCAL_BACKEND_URL` — адрес LM Studio глазами приложения (на dev —
  `http://127.0.0.1:8034/v1`, на облачном VPS — вход обратного SSH-туннеля,
  например `http://172.17.0.1:8034/v1`; пусто → legacy `VLLM_BASE_URL`);
  `LLM_LOCAL_TIMEOUT_S=2` — fail-fast таймаут локального бэкенда
  (переименован из `LLM_ROUTER_LOCAL_TIMEOUT_S`); `LLM_ROUTER_COOLDOWN_S=60`
  — cooldown circuit-breaker'а. `BackendRouter`/factory берут значения из
  `Settings`; `beginner` тоже ходит через `LLM_LOCAL_BACKEND_URL` (на VPS
  локальная модель доступна только через туннель); добавлен публичный
  `BackendRouter.cooldown_s`. Новый гайд
  [`docs/development/DEPLOYMENT_GPU_TUNNEL.md`](development/DEPLOYMENT_GPU_TUNNEL.md):
  Windows + LM Studio → Selectel VPS (Ubuntu), `ssh -N -R`, GatewayPorts,
  проверка из контейнера, fallback при выключении ПК, troubleshooting,
  rollback. Тесты: `tests/unit/test_llm.py` (+6).
- **Замороженный eval-набор и `BackendRouter` (итерация 58, T-124/T-125):**
  `tests/fixtures/eval_games.json` фиксирует **5 PGN-партий** Moscow zt 1964
  (Stein–Geller, Stein–Suetin, Bronstein–Spassky, Bronstein–Kholmov,
  Bronstein–Stein) с `game_id=-1..-5` и sha256 каждого PGN;
  `scripts/evaluate_llm.py` по умолчанию берёт партии из файла
  (`--frozen-set`/`--frozen-file`, режим БД — только явный
  `--no-frozen-set --source ...`), что даёт **same-sample** сравнение версий
  модели (T-024/KI-023). В `app/llm/router.py` появился `BackendRouter`:
  для уровня `club` сначала LM Studio с таймаутом
  `LLM_ROUTER_LOCAL_TIMEOUT_S=2` и без ретраев, при
  `httpx.ConnectError`/таймауте — лог `local_gpu_unavailable` и fallback в
  DeepSeek; circuit-breaker с `LLM_ROUTER_COOLDOWN_S=60`; `beginner`
  остаётся строго локальным, `gm` — напрямую DeepSeek (SPEC §10, T-123).
  Тесты: `tests/unit/test_llm_router.py`, `tests/unit/test_llm.py`,
  `tests/unit/test_evaluate_llm_frozen.py`. С итерации 59 таймаут
  называется `LLM_LOCAL_TIMEOUT_S`, а адрес локального бэкенда —
  `LLM_LOCAL_BACKEND_URL`.
- **CJK-фильтр chat-SSE (KI-060, итерация 57):** `CjkStreamBuffer` вынесен
  из `app/commentary/streaming.py` в общий `app/streaming.py` (комментарий
  ре-экспортирует его, поведение не изменилось), а `app/chat/streaming.py`
  прогоняет чанки LLM через буфер — SSE `delta` больше не содержит
  иероглифов U+4E00–U+9FFF, для SAN-валидации/`store()` используется
  очищенный текст. Legacy semantic-cache очищается `sanitize_cjk`,
  отклонённый или пустой после очистки ответ генерируется заново; ответ
  только из иероглифов → `error` `llm_empty` без записи; в лог пишутся
  только счётчики (`chat_cjk_stream_stripped`, `chat_cache_cjk_dropped`).
  Тесты: 4 integration в `tests/integration/test_streaming.py`. С итерации 57
  стриминговый остаток KI-060 закрыт; KI-060 остаётся Open до переобучения
  7B.
- **v4-датасет: 556 записей без обрезки (T-104b, T-122, итерация 57):**
  через DeepSeek сгенерировано **556 записей** v4-датасета; максимальный
  ответ — **1884 токена**, обрезки нет. `max_tokens=2048` (итерация 55)
  решил проблему, из-за которой 729 из 824 записей падали на валидаторе
  (`invalid_llm`). Переобучение 7B на v4 и полный eval — следующие шаги.
- **CJK-фильтр SSE-стриминга комментария (KI-060, итерация 53):**
  в `app/commentary/streaming.py` добавлен буферизующий
  `CjkStreamBuffer`: текст копится до конца предложения (CJK `。！？` —
  всегда, латинские терминаторы — перед пробелом/концом) или 20–30
  символов по границе слова; каждый кусок проверяется `has_cjk`.
  Предложения с иероглифами вырезаются `strip_cjk_sentences` (как в
  `service.py`), иероглифы не попадают ни в один `delta`; в
  `commentaries.text` и semantic cache сохраняется очищенный текст,
  `done` — после SAN-проверки. Legacy cache-hit очищается
  `sanitize_cjk`, почти целиком CJK-ответ генерируется заново; CJK-only
  ответ → `llm_empty` без записи; в лог пишутся только счётчики
  (`commentary_cjk_stream_stripped`, `commentary_cache_cjk_dropped`).
  Тесты: `tests/unit/test_streaming_cjk.py` (16) и
  `tests/integration/test_streaming.py` (мок-LLM + SSE, грязный и
  отклонённый cache-hit). Остаток — chat-SSE
  (`app/chat/streaming.py`) и диапазон U+4E00–9FFF; KI-060 остаётся
  Open до v4.
- **Результаты v3-PGN и сравнение версий модели (итерация 52):**
  `qwen-7b-tokenizer_gguf-v3` (LoRA 7B на 50 PGN-партиях DeepSeek) —
  **3.67/10**, объяснения **67% (2/3)**, CJK **0.00% (0/3)**. В
  `LLM_EVAL.md` добавлена сравнительная таблица v1 (3.33), v2-FEN (2.67),
  v3-PGN (3.67) и baseline 4B (4.40) с дельтами и оговоркой, что наборы
  партий у прогонов разные (дельты межверсионные, не same-sample), и
  ограничениями (оценён 1 из 5 комментариев: 2 ошибки Stockfish, 2 —
  судья без JSON).
  В `GPU_TRAINING_GUIDE.md` добавлен **§6.2**: для финального качества 7B
  нужно **≥500 PGN-записей** (ориентир 1000–2000), FEN-режим
  `--positions` для основного обучения не используется. План
  масштабирования v4 — `RELEASES.md`, итерация 52.
- **CJK-метрика в LLM-eval (KI-060, итерация 50):**
  `scripts/evaluate_llm.py` считает долю иероглифов U+4E00–U+9FFF среди
  непустых символов и число комментариев с CJK. Метрика печатается в
  консоль, попадает в `LLM_EVAL.md` (итог, колонки по уровням и партиям)
  и в `llm_eval_results.json` (`cjk_chars`, `cjk_share`). Имя модели не
  влияет на код: `qwen-7b-tokenizer` оценивается через
  `VLLM_MODEL`/`LORA_ADAPTER_PATH`. В `GPU_TRAINING_GUIDE.md` Kaggle-
  раздел переведён на `gold_dataset_v2.jsonl` и
  `--lr 1e-4 --epochs 1`; Kaggle-ноутбук обновлён (v2-датасет,
  `LR=1e-4`, `EPOCHS=1`).

- **CJK-детектор и защита от языкового дрейфа (KI-060, итерация 49):**
  в `app/commentary/semantic.py` появились `has_cjk`, `count_cjk`,
  `cjk_share`, `strip_cjk_sentences`, `sanitize_cjk` и `cjk_warning`
  (диапазон CJK Unified Ideographs U+4E00–U+9FFF, порог
  `CJK_MAX_SHARE = 0.10`). `app/commentary/service.py` при иероглифах
  переспрашивает модель **один раз** с подсказкой «Отвечай только на
  русском языке. Убери китайские иероглифы», затем вырезает предложения
  с иероглифами, а при их большой доле отклоняет ответ (502); в лог
  пишутся только счётчики (`commentary_cjk_detected` /
  `commentary_cjk_stripped` / `commentary_cjk_dropped`). Тесты:
  `tests/unit/test_cjk_detector.py`, `tests/unit/test_commentary_cjk.py`.
- **Генератор golden dataset v2 через DeepSeek (T-104b, итерация 49):**
  `scripts/build_gold_dataset_v2.py` умеет два режима. **PGN:** читает
  `data/gold_pgns/`, собирает `build_fact_package(..., max_tokens=2048)`,
  отправляет системный промпт уровня `club` + требование «только текст
  комментария» в `deepseek-chat` (ключ — `Settings.deepseek_api_key`).
  **Positions:** `--positions data-chess/positions.jsonl` читает FEN из
  JSONL (`fen`, `evaluation`, `best_move`; есть синонимы) и строит
  упрощённый пакет — только текущая позиция и оценка, без истории ходов.
  Оба режима валидируют SAN-ходы и CJK, повторяют запрос при ошибках и
  пишут `schema_version=2` JSONL в `data/gold_dataset_v2.jsonl`. CLI:
  `--limit`, `--output`, `--dry-run` (пайплайн без API), `--positions`,
  `--max-tokens`, `--max-retries`, `--model`, `--level`. Тесты с
  `httpx.MockTransport`: `tests/unit/test_build_gold_dataset_v2.py`.
- **Гиперпараметры v0.2.0 (итерация 49):** `scripts/train_lora.py`
  получил явные `DEFAULT_EPOCHS`/`DEFAULT_LR` и help-текст для
  `--epochs`/`--lr`; значения по-прежнему переопределяются флагами без
  правки кода (`--lr 1e-4 --epochs 1`). В
  `docs/development/GPU_TRAINING_GUIDE.md` добавлен §6.1 «Обучение
  v0.2.0 (DeepSeek Teacher)» с командой сборки v2-датасета и обучения.

### Fixed

- **Судья LLM-eval больше не обрезает JSON на 1024 токенах (итерация 61):**
  `_judge` в `scripts/evaluate_llm.py` собирает клиента DeepSeek через
  `_judge_client`, который подставляет `llm_max_tokens =
  LLM_JUDGE_MAX_TOKENS` (новая настройка `Settings`, по умолчанию **2048**).
  Раньше судья наследовал общий `LLM_MAX_TOKENS=1024`, ответ обрезался, и
  `_extract_json` падал с «в ответе судьи нет JSON-объекта» — в baseline v3
  так потерялись **2 из 5** партий (оценка v3 была 3.89/10 по 3 оценённым).
  Длина обычных комментариев не изменилась. Тесты:
  `tests/unit/test_evaluate_llm_judge_tokens.py` (6).
- **`LLM_LOCAL_TIMEOUT_S`: дефолт поднят с 2 до 120 с (итерация 60):**
  таймаут — это время HTTP-запроса локального `club`-клиента, а не пинг:
  с 2 с живой GPU считался недоступным, потому что генерация 7B занимает
  10–15 с, и каждый клубный комментарий уходил в DeepSeek. Новый дефолт
  покрывает генерацию; отказ соединения (ПК выключен, туннель упал)
  по-прежнему даёт мгновенный fallback через `ConnectError`, а таймаут
  ожидается только при «чёрной дыре», после чего circuit-breaker на
  `LLM_ROUTER_COOLDOWN_S=60` пропускает local. Обновлены `app/config.py`,
  `.env.example`, гайд `DEPLOYMENT_GPU_TUNNEL.md` и тест дефолтов.
- **`evaluate_llm.py`: метка версии модели в отчёте и JSON (итерация 60):**
  добавлен флаг `--model-name` (по умолчанию `qwen-7b-tokenizer_gguf-v4`),
  который пишет явную метку в заголовок `LLM_EVAL.md` и в каждую запись
  `llm_eval_results.json` (`model_name`) рядом с API-именем `model`. Нужно,
  чтобы прогоны v3 и v4 на одном замороженном наборе не путались. Тесты:
  `tests/unit/test_evaluate_llm_model_name.py`.
- **`evaluate_llm.py` не падает на сбое Stockfish по одной партии
  (итерация 57):** per-game `POST /v1/analyze` обёрнут в `try/except` —
  ошибка логируется, для каждого запрошенного уровня пишется error-строка,
  eval продолжается со следующей партией (раньше исключение прерывало весь
  прогон). Тесты: `tests/unit/test_evaluate_llm_analyze_errors.py`.
- **CI: Stockfish снова находится в backend-job (итерация 56):**
  Ubuntu-пакет `stockfish` ставит бинарник в `/usr/games/stockfish`, а
  приложение искало `/usr/bin/stockfish`, поэтому `POST /v1/analyze` в CI
  отдавал 500 `engine_not_found` — 11 интеграционных тестов были красными.
  В шаг `pytest` добавлен `STOCKFISH_PATH=/usr/games/stockfish`;
  `tests/integration/test_api_games.py` и `tests/integration/test_streaming.py`
  получили skip-guard по наличию движка (как в `test_engine.py`), чтобы
  окружения без Stockfish не падали на 500. Побочный эффект: 28
  engine-тестов, которые в CI молча скипались, теперь реально выполняются
  (полный набор в Linux-контейнере — **1311 passed, 9 skipped,
  1 xfailed**). T-123 (reverse SSH GPU tunnel) добавлена в backlog.
- **`max_tokens` увеличен до 2048 для предотвращения обрезки (итерация 55):**
  в `scripts/build_gold_dataset_v2.py` лимит ответа учителя DeepSeek был
  `LLM_MAX_TOKENS=1024`; русский комментарий 200–320 слов (≈1500 токенов)
  обрезался на полуслове, обрезанный SAN-ход (например, «N» вместо «Nf3»)
  не проходил валидатор — из 824 партий v4-датасета **729 записей** были
  отклонены как «учитель не прошёл валидатор». Добавлены
  `DEFAULT_RESPONSE_MAX_TOKENS = 2048`, поле `GoldV2Config.response_max_tokens`
  и флаг `--response-max-tokens`; значение пробрасывается в
  `OpenAICompatibleClient` при создании клиента (независимо от
  `LLM_MAX_TOKENS`). Тесты проверяют `max_tokens=2048` в payload,
  override до 4096, parser и валидацию.
- **Таймауты и инкрементальная запись датасета v2 (итерация 54):**
  `scripts/build_gold_dataset_v2.py` больше не теряет весь прогресс из-за
  одного таймаута DeepSeek. Добавлен `--timeout-s` (по умолчанию 300 с,
  вместо `LLM_TIMEOUT_S`/120 с), пробрасываемый в
  `OpenAICompatibleClient`; таймауты (`httpx.TimeoutException` /
  `LLMTimeoutError`) повторяются до **3 раз** с экспоненциальной паузой
  (2/4/8 с), после чего запись пропускается (`skipped_timeout`, warning в
  stderr) и цикл по партиям продолжается. Запись JSONL стала
  инкрементальной: `DatasetWriter` открывает файл до цикла, пишет каждую
  строку и сразу `flush()`-ит её — падение на N-й партии сохраняет первые
  N−1. Новый флаг `--append` дописывает в существующий файл и пропускает
  уже сгенерированные записи (`metadata.id`), т.е. продолжает с места
  падения без дублей и без перезаписи. Тесты:
  `tests/unit/test_build_gold_dataset_v2.py` (+7: retry→skip→continue,
  инкрементальность при сетевой ошибке, `--append`/resume, parser и
  `build_client`).
- **KI-061 (Format mismatch) закрыт (итерация 52):** v2-FEN обучался на
  упрощённом FEN-пакете (`build_gold_dataset_v2.py --positions`), а
  инференс подаёт полный PGN-пакет — модель училась «додумывать» историю,
  судья фиксировал выдуманные ходы (2.67/10). В v3 датасет собран
  PGN-режимом (50 PGN-партий DeepSeek, тот же формат, что на инференсе):
  **3.67/10** (+1.00 к v2-FEN), галлюцинации снизились. Запись
  отсутствовала в реестре и восстановлена сразу как Closed.
- **LLM_TEMPERATURE: проброс подтверждён и покрыт тестом (итерация 48):**
  `Settings.llm_temperature` (`LLM_TEMPERATURE`, по умолчанию `0.2`) уже
  передавался из `app/llm/factory.py` в `OpenAICompatibleClient` и уходил
  в JSON payload (`complete` и `stream_generate`) для LM Studio и
  DeepSeek — проброс не менялся. Добавлены тесты: env →
  `Settings.llm_temperature`, фабрика → `temperature` в payload
  (`club`/`gm`), стрим; `LLM_TEMPERATURE=0.2` добавлен в `.env.example`.
- **Few-shot contamination в промптах (KI-030, итерация 48):**
  из `app/prompts/club.txt` и `club_en.txt` убраны примеры с конкретными
  ходами и фигурами («отвести слона», «конь в центр»), которые локальная
  3B-модель копировала в комментарий. Примеры сведены к форме изложения
  без SAN, фигур, оценок и номеров хода. Добавлено правило: если в пакете
  фактов нет информации о ходе — не выдумывать ходы и не упоминать
  фигуры, писать только общие слова о позиции. В `build_fact_package`
  появилась константа `NO_MOVE_DATA_INSTRUCTION`, которая попадает и в
  пакет без анализа движка, и в обычный пакет. Тесты —
  `tests/unit/test_prompts.py` (56 passed).
- **Изоляция export-тестов от данных dev-БД (итерация 48):**
  `tests/integration/test_admin_exports.py` и `test_export_jobs.py`
  ограничили выборку датами сидируемых правок: в общей dev-БД лежит
  реальная `gm`-правка от 2026-10-28, из-за которой пять тестов,
  рассчитанных на пустую БД, видели две строки вместо одной. В CI на
  чистой БД поведение не меняется; локально `pytest` снова зелёный.
- **Пакет фактов не раздувает контекст (T-095, итерация 47):**
  у `build_fact_package` появился параметр `max_tokens` (по умолчанию
  2048): длинная партия сокращается до первых и последних 10 ходов плюс
  ходы ключевых моментов, в пакете появляется пометка о сокращении. Это
  снимает раздувание пакета на длинных партиях; на текущем датасете
  `skipped_too_long` может сохраниться, пока не поднят `--max-length`
  (бюджет считает system + пакет + ответ, см. `GPU_TRAINING_GUIDE.md`
  §11.3.1). Добавлены тесты на 1200-ходовую партию.
- **TensorFlow в Colab больше не уводит PyTorch на CPU (T-095, итерация 47):**
  `scripts/train_lora.py` выставляет `USE_TF=0` и `TRANSFORMERS_NO_TF=1`
  до импорта torch/transformers: встроенный TF падал на
  `libcudart.so.12` и переключал обучение на CPU.
- **bf16 на T4 (T-095, итерация 47):** новый флаг `--force-fp16`
  принудительно ставит `fp16=True`/`bf16=False`; Tesla T4 определяется по
  имени устройства, даже если `torch.cuda.is_bf16_supported()` возвращает
  `True` (на таких сборках обучение зависало).
- **Терминология «ход» в пакете фактов (T-094, итерация 46):**
  `app/llm/prompts.py` больше не пишет «полуход»: метаданные — «Ходов: N»,
  ключевой момент — «Ход N белых/чёрных» (номер хода, а не ply);
  `app/rag/context.py` выводит «Ход N белых/чёрных»;
  `scripts/build_gold_dataset.py` синхронизирован с новой формулировкой.
  Добавлены тесты на отсутствие слова «полуход» в пакете и системном
  промпте.
- **QLoRA на T4/P100 без bf16 (T-094, итерация 46):**
  `scripts/train_lora.py` выбирает `fp16`, если
  `torch.cuda.is_bf16_supported()` == `False`, и передаёт тот же dtype в
  `BitsAndBytesConfig` и `torch_dtype`; `TrainingArguments` получает
  `fp16=True`/`bf16=False` вместо молчаливого fp32. Это делает T4
  пригодным для ноутбука из §11.1.
- **Совместимость LoRA-обучения с transformers 4.44+ (T-090, итерация 44):**
  `collate_batch` теперь возвращает `torch.Tensor` для `input_ids`,
  `attention_mask` и `labels` (иначе `embedding()` падал с
  `argument 'indices' must be Tensor, not list`); из `TrainingArguments`
  убраны `logging_dir` и `warmup_ratio`, вызывавшие `TypeError` в новых
  версиях. Добавлен `--skip-too-long` (по умолчанию включён): записи,
  которые не помещаются в `--max-length`, пропускаются со счётчиком
  `skipped_too_long`, обучение продолжается; `--no-skip-too-long`
  возвращает прежнее падение. В `pyproject.toml` добавлен mypy-override
  для опциональных torch/transformers/peft/datasets, чтобы проверка
  скриптов работала и без них.

### Changed

- **KI-030 закрыт: LoRA 3B и 7B отклонены, возврат к 4B (итерация 48).**
  `chess-annotator-3b` (Qwen2.5-3B) — **2.87/10**, `qwen-7b-tokenizer`
  (Qwen2.5-7B) — **3.33/10** против **4.40/10** у zero-shot Qwen3-4B.
  7B лучше 3B (+0.46), но обе хуже baseline (−1.07 и −1.53); судья выявил
  переобучение, выдуманные лучшие ходы, путаницу сторон и смешанный
  китайский текст у 7B. Причина — шум и нехватка данных golden-датасета.
  Возвращаемся к 4B; для T-104b нужна пересборка датасета более сильной
  моделью (DeepSeek). Подробности — `docs/development/reports/LLM_EVAL.md`.
- **KI-060 — языковой дрейф LoRA 7B (итерация 48).** Новый KI: в
  комментариях `qwen-7b-tokenizer` появляется китайский текст; нужен
  детектор CJK/кириллицы в eval/валидаторе и переобучение на очищенном
  датасете. См. `docs/KNOWN_ISSUES.md`.
- **Colab/T4-профиль ноутбука (T-095, итерация 47):**
  `notebooks/kaggle_train_lora.ipynb` ставит `torchao>=0.16.0 --no-deps`,
  блокирует TensorFlow отдельной ячейкой до импорта torch и запускает
  обучение с `--no-4bit`, `--force-fp16`, `--max-length 2048`; модель по
  умолчанию — `Qwen/Qwen3-4B-Instruct-2507` (7B без 4-bit не помещается в
  T4 16 GB). В `GPU_TRAINING_GUIDE.md` §11.3 переписан под эти флаги и
  добавлен §11.3.1 «Решение проблем (Troubleshooting)».
- **Быстрый старт на Kaggle (T-094, итерация 46):**
  `GPU_TRAINING_GUIDE.md` §11.1 «Как запустить обучение на Kaggle за
  5 минут» со ссылкой на `notebooks/kaggle_train_lora.ipynb`; §11.2–§11.4
  перенумерованы, примеры переведены на `gold_dataset.jsonl`, добавлено
  предупреждение о 7B/2048 на T4 16 GB и fallback 4B/1024.
- **GPU-гайд для запуска из РФ (T-091, итерация 44):**
  `GPU_TRAINING_GUIDE.md` — `hf download` вместо устаревшего
  `huggingface-cli`, `HF_ENDPOINT=https://hf-mirror.com` и Python API
  `snapshot_download`; установка через `uv`; зеркало PyTorch SJTU
  `mirror.sjtu.edu.cn/pytorch-wheels/cu126/`; совместимые `torch` и
  `torchvision` одной сборкой `cu126`; §10 переименован под CUDA 12.6
  (RTX 4090/A5000) и дополнен траблшутингом.

### Added

- **Kaggle Notebook для LoRA (T-094, итерация 46):**
  `notebooks/kaggle_train_lora.ipynb` — установка зависимостей,
  клонирование репозитория, автопоиск `gold_dataset.jsonl`, `--dry-run`,
  QLoRA-обучение `Qwen/Qwen2.5-7B-Instruct` на T4 (`batch=1`,
  `grad_accum=16`, `max_length=2048`, `--load-in-4bit`, gradient
  checkpointing, TensorBoard), упаковка адаптера в zip и resume с
  `--resume_from_checkpoint`. Ноутбук проверяется
  `tests/unit/test_kaggle_notebook.py`.
- **Lichess golden dataset builder (T-092, итерация 45):**
  `scripts/fetch_lichess_games.py` скачивает PGN Lichess с
  `evals=true` (по нику или ID), фильтрует рейтинг 1800–2200,
  standard-вариант и наличие `[%eval]`, раскладывает по
  `data/gold_pgns/*.pgn` по 50–100 партий.
  `scripts/build_gold_dataset.py` парсит `[%eval ...]`, пересчитывает
  оценки в сторону хода, собирает `MoveAnalysis` с
  `best_move_known=False`, вызывает `build_fact_package`, генерирует
  русский ответ локальной LLM или mock-клиентом, валидирует ходы через
  `app/engine/validator.py` и пишет `data/gold_dataset.jsonl`
  (`schema_version=2`). В `MoveAnalysis` добавлен `best_move_known`;
  пакет фактов честно сообщает, что лучший ход неизвестен.
- **Прогресс золотого датасета (T-092, итерация 46):** на 2026-11-01
  собрано **130 записей** (`data/gold_dataset.jsonl`, `schema_version=2`);
  файл локальный и в git не входит.
- **Бесплатные GPU в гайде (T-093, итерация 45):**
  `GPU_TRAINING_GUIDE.md` §11 — Kaggle Notebooks (T4, 30 ч/нед, датасет,
  обучение 4B, TensorBoard, zip-адаптер) и Google Colab (Drive,
  установка, обучение, `--resume_from_checkpoint`), ограничения 16 GB.
- **Экспорт LoRA в GGUF (T-088, итерация 43):**
  `scripts/export_to_gguf.py` — merge LoRA в базовую модель через `peft`
  (`merge_and_unload`), сохранение HF-модели и токенизатора, опциональная
  конвертация в GGUF через `llama.cpp` (`convert_hf_to_gguf.py` /
  `convert.py`). CLI: `--base_model`, `--adapter_path`, `--output_dir`,
  `--llama_cpp_dir`, `--gguf_outtype`, `--device`. Без llama.cpp или при
  ошибке конвертации — warning, HF-формат сохранён, exit 0.
- **Возобновление обучения и логирование лосса (T-089, итерация 43):**
  `scripts/train_lora.py` — `--resume_from_checkpoint PATH` (передаётся в
  `Trainer.train`, проверка каталога `checkpoint-*`) и
  `--report_to auto|tensorboard|wandb|none` (auto выбирает установленную
  интеграцию; отсутствующая — warning, обучение продолжается). TensorBoard
  пишет в `output_dir/runs`; `--check-env` показывает tensorboard/wandb.
- **Конвертер внешних PGN-датасетов (T-087, итерация 42):**
  `scripts/convert_external_datasets.py` читает JSONL с PGN в поле `text`,
  извлекает комментарии `{ ... }`, очищает служебные теги
  `[%eval ...]`/`[%clk ...]`, склеивает текст и ограничивает `assistant`
  2000 символами; `user` собирается через
  `build_fact_package(..., level="club")` без анализа движка, запись —
  `schema_version=2` (совместима с `scripts/train_lora.py`). CLI:
  `--input`, `--output`, `--limit`, `--dry-run`; в сводке — прочитано,
  пропущено (битый JSON/PGN, нет/короткие комментарии), сконвертировано,
  средняя длина `assistant`. `data/external_datasets/` и
  `data/lora_gold_v1.jsonl` добавлены в `.gitignore`; в
  `GPU_TRAINING_GUIDE.md` — раздел §5.1.
- **Stripe Checkout и подписка Pro (T-083, итерация 40):** `stripe` SDK,
  настройки `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET`/`STRIPE_PRICE_ID`,
  миграция `f2b3c4d5e6a7` (`users.tier` free/pro + CHECK, backfill
  admin/auditor → pro; `users.stripe_customer_id` nullable UNIQUE).
  `POST /v1/billing/create-checkout-session` находит/создаёт Stripe
  Customer и возвращает URL подписки; `POST
  /v1/billing/create-portal-session` — Customer Portal; `POST
  /v1/billing/webhook` проверяет `Stripe-Signature` через
  `stripe.Webhook.construct_event` и идемпотентно переключает тариф:
  `checkout.session.completed` → `pro`, `customer.subscription.deleted` →
  `free`. Без ключей billing-эндпоинты отвечают 503, приложение работает.
- **Billing UI (T-084, итерация 40):** кнопка «Оформить подписку» в
  модалке дневного лимита (редирект на Stripe Checkout), страницы
  `/billing/success` и `/billing/cancel`, блок «Моя подписка» в
  `/profile` с текущим тарифом и кнопкой «Управлять подпиской» (Stripe
  Customer Portal); `tier` в профиле `/auth/me`; i18n RU/EN.
- **FinOps из БД (T-085, итерация 40):** `TokenBudgetMiddleware` и
  `GET /v1/users/me/usage` берут тир из `users.tier` (PK-lookup на
  защищённый запрос), `PRO_IPS` и роль JWT больше не дают Pro; при
  недоступной БД — безопасный `free`. Роль admin без `tier='pro'` теперь
  получает free-лимит.

## [1.2.0] - 2026-10-30

### Added

- **Подготовка к GPU-обучению (T-080, итерация 39):** `.gitignore`
  закрывает веса и датасеты (`models/`, `*.safetensors`, `*.pt`,
  `data/lora_*.jsonl`, `data/*.jsonl` при сохранённом `!data/.gitkeep`);
  новый `scripts/generate_lora_dataset_from_db.py` собирает датасет
  schema v2 из БД (`--limit 500`, `--level`, `--min-words`, `--seed`,
  `--dry-run`, `--output`); пошаговая инструкция переноса обучения на
  RTX 3060 — `docs/development/GPU_TRAINING_GUIDE.md` (CUDA-PyTorch,
  датасет, `train_lora.py`, перенос адаптера через архив/Git LFS).
- **PWA (T-081, итерация 39):** `vite-plugin-pwa` — манифест
  («ChessAnnotator», `display: standalone`, иконки 192/512/maskable,
  `theme-color`), service worker с precache статики (15 файлов) и
  `navigateFallback` для SPA; `/v1/*` и `/auth/*` исключены из кэша;
  iOS-мета-теги (`apple-mobile-web-app-capable`), `apple-touch-icon`.
- **Профиль пользователя (T-082, итерация 39):** `PATCH /auth/me`
  (отображаемое имя + предпочитаемый уровень), `POST /auth/change-password`
  (проверка текущего пароля); миграция `users.display_name`
  (`a9f1f28d4c45`); страница `/profile` в UI (имя, уровень, смена пароля с
  валидацией) и пункт меню «Профиль».
- **Реальный SMTP (T-082, итерация 39):** `SMTPEmailSender` на
  `aiosmtplib` (STARTTLS/SSL, таймаут, MIMEText UTF-8) включается
  `EMAIL_SENDER=smtp` при заданных `SMTP_HOST`/`SMTP_FROM`; в логах нет
  ссылки/токена; `mock` остаётся дефолтом, неизвестный backend и неполная
  конфигурация — `EmailDeliveryError`; `SMTP_*` добавлены в `.env.example`.

- **Контекст выбранного хода в чате (T-072, итерация 38):** фронтенд
  передаёт `selected_ply` в `POST /v1/chat` и `/v1/chat/stream`; бэкенд
  добавляет в пользовательский промпт строку «Пользователь сейчас смотрит
  на ход 13... Re8 (13-й ход чёрных, оценка +0.40)…» (RU/EN), поэтому
  вопросы «Почему это плохо?», «А лучше?» без номера хода относятся к
  выбранному полуходу. Над чатом — индикатор «Контекст: 13... Re8»;
  `selected_ply` попадает и в SSE `meta`.
- **Стрелки на доске (T-073, итерация 38):** `Board` принимает `arrows`
  (react-chessboard 5); рекомендованный ход выбранного полухода рисуется
  зелёной стрелкой, а ходы, упомянутые в тексте комментария, извлекаются
  SAN-паттерном, проверяются на текущей позиции через `chess.js` и
  рисуются стрелками (зелёная — хороший/рекомендованный ход, красная —
  `blunder`/`mistake`/`inaccuracy`). Текст комментария поднят в
  `analysisStore.commentaryText`, стрелки обновляются после генерации или
  правки и скрываются в режиме варианта.
- **Промпты объясняют причины (итерация 38):** во все уровневые промпты
  (`beginner`/`club`/`gm` + `_en`) и `chat_en.txt` добавлено жёсткое
  правило «ОБЪЯСНЯЙ ПРИЧИНЫ / ALWAYS EXPLAIN WHY» с примером
  «потому что…»; в системный промпт чата — «Всегда объясняй причины».
- **Метрика объяснений в LLM-evals (итерация 38):** `scripts/evaluate_llm.py`
  считает `has_explanation` (маркеры «потому что», «так как», «поэтому»,
  «because», «since», …), пишет флаг в JSON и выводит «Долю объяснений» в
  сводке отчёта и консольном итоге, плюс колонку в per-level таблице.

### Changed

- **Версия 1.2.0 (итерация 38):** `pyproject.toml`, `app/__init__.py`,
  `web/package.json`; CHANGELOG `[1.2.0] - 2026-10-30`.

## [1.1.0] - 2026-10-30

### Added

- **Интерактивная доска (T-070, итерация 37):** в разборе фигуры можно
  перетаскивать (`react-chessboard` 5, `onPieceDrop`): легальный ход
  создаёт локальный вариант и показывает строку «Вариант: 3. Ng5 Nd4»,
  нелегальный — откатывается; кнопка «Сбросить на партию» возвращает
  позицию из базы, вариант сбрасывается при навигации и новом анализе.
- **Загрузка PGN из файла (T-071, итерация 37):** кнопка «Загрузить файл»
  (📎) рядом с полем PGN: `FileReader`, лимит **1 МБ**, проверка
  расширения `.pgn`, ошибки («Файл больше 1 МБ…», «Нужен файл с
  расширением .pgn») в существующем баннере.
- **Семантическая валидация комментария (KI-023, итерация 37):** новый
  `app/commentary/semantic.py` извлекает утверждения о позиции
  («слон на e6», «связка пешки f7», EN-аналоги) и сверяет их с позициями
  партии и PV-линиями движка; неверные утверждения ведут к retry с
  подсказкой, а на последней попытке offending-предложения вырезаются
  вместо ответа 502. Флаг `SEMANTIC_VALIDATION_ENABLED` (по умолчанию
  включено).
- **Локализация мотивов (итерация 36):** типы мотивов
  (`passed_pawn`, `weak_square`, `open_file`, …) больше не показываются
  как системные теги — в `AnalysisWorkspace` они переводятся через
  `t("motifs.<type>")` (RU/EN, 9 типов); неизвестный тип остаётся как
  есть.
- **Подсказка к лимиту токенов (итерация 36):** `UsageBadge` показывает
  в `title` «Израсходовано токенов LLM: 9 300 из 50 000 (дневной лимит)»
  с разделителями тысяч по языку интерфейса.
- **Шкала оценки (итерация 36):** `EvalBar` рядом с доской — белая часть
  сверху, чёрная снизу (clamp ±1000 cp), для мата — подпись `M3`/`M-2`.
- **График оценки (итерация 36):** `EvalGraph` на recharts — линейный
  график `eval_cp` по полуходам с нулевой линией, маркером выбранного
  хода и переключением доски кликом по точке.
- **Навигация по партии (итерация 36):** кнопки «в начало / назад /
  вперёд / в конец» под доской; на границах партии кнопки `disabled`.
- **Скрытие оценок в списке ходов (итерация 36):** чекбокс
  «Показывать оценки» прячет числовые значения, оставляя классификацию и
  цветовую подсветку.
- **Экспорт PGN (итерация 36):** кнопка «Скачать PGN» — `chess.js`
  восстанавливает партию, добавляет `{оценка/мат, классификация}` к
  каждому ходу и скачивает файл через Blob.
- **Route-level code-split (итерация 36):** `HomePage`/`AnalysisPage`
  лениво загружаются (`React.lazy` + `Suspense`), основной чанк —
  112.10 kB gzip вместо 255.26 kB (RULES §3.2: ≤ 200 kB).

### Changed

- **Версия 1.1.0 (итерация 37):** `pyproject.toml`, `app/__init__.py`,
  `web/package.json`; CHANGELOG `[1.1.0] - 2026-10-30`.

### Fixed

- **Порядок импортов `app/main.py` (итерация 37):** `import sys` перенесён
  к остальным stdlib-импортам — `ruff` (I001/F811) снова зелёный; настройка
  `WindowsProactorEventLoopPolicy` для win32 сохранена без изменений
  поведения.

## [1.0.0] - 2026-11-01

### Added

- **Английский PDF-экспорт (KI-058, итерация 35):** `GET
  /v1/commentaries/{id}/export/pdf` принимает query-параметр `language`
  (`ru`/`en`; иначе `Accept-Language` → `DEFAULT_LANGUAGE`); при `en`
  переводятся заголовки и подписи («Game», «Moves», «Commentary»,
  «Positions on the board», «Event», «Date», «Result», «Level», «Model»,
  «Status», названия уровней, футер, страницы). PGN/SAN не зависят от
  языка; русский PDF не изменился.
- **Язык чата (KI-058, итерация 35):** `POST /v1/chat` и
  `/v1/chat/stream` принимают `language` (`ru`/`en`; иначе
  `Accept-Language` → `DEFAULT_LANGUAGE`), английский системный промпт —
  `app/prompts/chat_en.txt`; retry-подсказка и ответы сохраняют
  выбранный язык.
- **Локализация ошибок API (KI-058, итерация 35):** `app/i18n.py` —
  английский `detail` по машинному `code`, если клиент просит `en`
  через `Accept-Language` (`en-US,en;q=0.9` → `en`); для неизвестных
  кодов и `internal_error` остаётся исходное сообщение. `code` и
  HTTP-статус не меняются, формат `{"detail", "code"}` сохранён.
- **Плейсхолдер `/history` для неподтверждённых (итерация 35):**
  страница истории показывает «Подтвердите email, чтобы увидеть историю»
  вместо пустого списка при `is_verified=false` (admin/auditor
  bypass); i18n RU/EN.
- **Read-only `export_jobs` в админке (итерация 35):**
  `ExportJobAdmin` в `/admin` — таблица фоновых выгрузок (status, rows,
  size, sha256, timestamps) с фильтром по `status`; создание/правка
  через view запрещены (read-only, admin/auditor).
- **Генератор слепого теста (T-024, итерация 35):**
  `scripts/generate_blind_test.py` — берёт 3 партии с анализом,
  генерирует комментарии трёх уровней и раскладывает по слотам `A`/`B`/`C`
  (перемешивание засеянным `random.Random(seed)`, одинаковый `--seed` даёт
  ту же раскладку): `game_N_X.txt`, `answer_form.md` (уровни не раскрыты),
  `answers_key.json` — ключ организатора. `--dry-run` работает без LLM,
  `--game-ids`, `--seed`, `--language`, exit codes 0/2. Сам тест с людьми
  не проводился (T-024 — «скрипт готов, живой тест вручную»).
- **English commentary localization (T-106, итерация 34):** файлы
  `app/prompts/{beginner,club,gm}_en.txt`; настройка `DEFAULT_LANGUAGE`
  (`ru`/`en`); `POST /v1/commentary` и `/v1/commentary/stream` принимают
  `language` (`ru`/`en`), иначе берут `Accept-Language`; английский
  retry-prompt; frontend передаёт `i18n.language` и нормализует его в
  `en`/`ru`. Тесты: `test_prompts.py`, `test_api_commentary.py`,
  Vitest `api.auth.test.ts`.
- **Grafana alerting (T-113, итерация 34):**
  `deploy/grafana/provisioning/alerting/rules.yaml` — `HighErrorRate`
  (5xx > 5% / 5 мин), `HighLatency` (p95 `/v1/commentary` > 10 с /
  5 мин), `LLMBudgetExceeded` (`increase(llm_cost_usd_total[24h])` >
  5 USD). Проверено в локальной Grafana: 3 правила в папке
  `ChessAnnotator`.
- **GHCR publishing (T-027, итерация 34):** job `publish-docker` в CI
  запускается только на тег `v*`, логинится в `ghcr.io` через
  `GITHUB_TOKEN` и пушит `ghcr.io/iilmchat/ai-chess-annotator:vX.Y.Z`,
  `:X.Y.Z`, `:latest`; `Dockerfile` принимает build-arg `VITE_API_BASE`.
- **Frontend auth UX (KI-055, итерация 33):** публичные маршруты
  `/verify-email?token=…` (GET `/auth/verify-email` при монтировании,
  статусы проверки/успеха/ошибки, обновление `authStore` после успеха),
  `/forgot-password` (нейтральное сообщение после 202) и
  `/reset-password?token=…` (новый пароль + повтор, редирект на `/login`
  с сообщением). `AuthGate` показывает экран «Подтвердите email» при
  `is_verified=false` (роль `user`; admin/auditor — bypass) с повторной
  проверкой профиля; `HomePage` отдельно обрабатывает 403
  `email_not_verified`. В шапке — выпадающее меню профиля с «Выйти» и
  «Выйти со всех устройств» (`POST /auth/logout-all`, очистка сессии +
  редирект). Ссылки в письмах теперь ведут на SPA
  (`APP_BASE_URL`/`verify-email`, `/reset-password`; dev-дефолт
  `http://localhost:5173`). i18n RU/EN. Тесты: Vitest
  `api.authFlow.test.ts` (7), Playwright `auth-flow.spec.ts`
  (9 сценариев × 2 проекта).
- **Retention экспортов (KI-054, итерация 33):**
  `scripts/prune_export_jobs.py` — батчевое удаление строк `export_jobs`
  старше `EXPORT_RETENTION_DAYS` (7) и их файлов; удаляются только пути
  внутри `EXPORT_DIR` (подмена `file_path`/симлинк → warning), файл
  удаляется до строки; `--retention-days`, `--export-dir`, `--batch-size`,
  `--max-batches`, `--dry-run`, `--json`, exit codes 0/2. Инструкция по
  cron/systemd/Windows Task Scheduler — `GETTING_STARTED.md` §9.3; в
  `docker-compose.prod.yml` — `EXPORT_RETENTION_DAYS`/`APP_BASE_URL`.
  Тесты: `tests/unit/test_prune_export_jobs.py` (7),
  `tests/integration/test_prune_export_jobs.py` (6).
- **ARQ-воркер фоновых выгрузок (KI-053, итерация 32):** `app/worker.py`
  и корневой `worker.py` (`arq worker.WorkerSettings`), задача
  `run_export_job(ctx, job_id)`; API только ставит задачу в Redis
  (`enqueue_job`, 503 `export_queue_unavailable` при недоступной очереди);
  recovery `running > EXPORT_JOB_STALE_MINUTES (15) → failed` при старте
  API и воркера; сервис `worker` в `docker-compose.yml` и
  `docker-compose.prod.yml` с общим volume `data/`/`prod_export_data`.
  Тесты — burst-воркер на реальном Redis, recovery, 503.
- **Auth finalization (KI-041, итерация 32):** миграция
  `dcf1da1421cd` — `users.is_verified` (legacy — true, регистрация —
  false); `app/email/sender.py` (`EmailSender` Protocol +
  `MockEmailSender` с outbox/structlog); `GET /auth/verify-email`;
  `POST /auth/forgot-password` (202 без enumeration) и
  `POST /auth/reset-password` (Argon2id, одноразовый токен, глобальный
  отзыв refresh); `POST /auth/logout-all` (`revoked_before:<user_id>`,
  Unix-ms; `/auth/refresh` сверяет `iat_ms`); `/v1/analyze` и
  `/v1/commentary` → 403 `email_not_verified` для неподтверждённых
  (admin/auditor bypass). Новые настройки `APP_BASE_URL`,
  `EMAIL_SENDER`, `EMAIL_FROM`, TTL ссылок, `ARQ_QUEUE_NAME`,
  `EXPORT_JOB_STALE_MINUTES`.
- **Диаграмма доски в PDF (T-107b, итерация 31):** `app/export/board.py`
  — `chess.svg.board()` → PNG через `cairosvg`; финальная позиция и до
  двух критических моментов (`blunder`/`mistake` по `|delta_cp|`) с
  подсветкой последнего хода. PDF вставляет PNG (`fpdf2`, 82 мм, подпись),
  pypdf видит image-XObject. Graceful degradation: без
  `cairosvg`/`libcairo2` PDF генерируется без картинки, а не 500.
  В `Dockerfile`/CI добавлен `libcairo2`; dependency `cairosvg>=2.7`.
- **Observability-стек Grafana + Tempo + Prometheus (T-109b, итерация 31):**
  `docker-compose.observability.yml` — `otel-collector` (OTLP gRPC/HTTP,
  spanmetrics-коннектор), `tempo`, `prometheus`, `grafana` (порты только
  на loopback). `app/observability.py` при `OTEL_ENABLED=true`
  экспортирует спаны и OTLP-метрики `llm.calls/llm.tokens/llm.cost.usd/
  llm.latency`; HTTP-спан получает `http.route`/`http.status_code` для
  spanmetrics. Провижининг datasource'ов и дашборда
  `deploy/grafana/dashboards/chessannotator-observability.json` (p95/p99
  для `/v1/commentary` и `/v1/analyze`, 5xx error rate, tokens/cost)
  автоматический. Новые настройки `OTEL_METRICS_ENABLED`,
  `OTEL_METRICS_INTERVAL_MS`; в Dockerfile/CI — `libcairo2`.
- **Фоновая выгрузка датасета (KI-050, итерация 31):** миграция
  `b0c9d2f13425` — таблица `export_jobs` (`status`
  queued/running/done/failed, снимок `filters` JSONB, `actor`,
  `file_path`, `file_size`, `sha256`, `rows_exported`, `error`,
  таймстемпы); `POST /v1/admin/exports/jobs` (202) + `GET /jobs`,
  `GET /jobs/{id}`, `GET /jobs/{id}/download` (409 — не готово,
  410 — файл вне `EXPORT_DIR`). BackgroundTasks пишет NDJSON в
  `EXPORT_DIR` (`data/exports/`, вне web-root) батчами через
  `asyncio.to_thread`, атомарно переименовывает файл, считает sha256 и
  пишет аудит `dataset.export` с `job_id`/`background=true`. В админке —
  форма «Создать экспорт» с поллингом и кнопкой «Скачать»; создание
  только admin/legacy, auditor read-only. Общая выборка вынесена в
  `app/dataset/records.py`. Новые настройки `EXPORT_DIR`,
  `EXPORT_JOBS_PAGE_SIZE`.
- **PDF-экспорт комментария (T-107, итерация 30):**
  `GET /v1/commentaries/{id}/export/pdf` — серверный PDF (`fpdf2` + TTF-
  шрифт с кириллицей): шапка (игроки, турнир, дата, результат, уровень,
  модель, статус), PGN-ходы, текст комментария с абзацами, нумерация
  страниц; `Content-Disposition: attachment; filename="commentary-{id}.pdf"`.
  JWT + изоляция данных (чужая запись → 404), CPU-bound генерация в
  `asyncio.to_thread`, битый legacy-PGN не ломает экспорт. Кнопка
  «Скачать PDF» в панели комментария (RU/EN) и API-хелпер с single-flight
  refresh. Настройка `PDF_FONT_PATH` (auto: Arial/DejaVuSans); в
  Docker-образ добавлен `fonts-dejavu-core`. Тесты:
  `tests/unit/test_pdf_export.py`,
  `tests/integration/test_commentary_pdf.py`, `web/src/api.pdf.test.ts`.
- **Кэш результатов Stockfish (T-108, итерация 30):** миграция
  `19bf1fdc2a73` — таблица `engine_cache` (`fen_hash`, `engine_version`,
  `depth`, `multipv`, `lines` JSONB, `created_at`,
  UNIQUE(fen_hash, engine_version, depth, multipv), индекс `created_at`);
  `app/engine/cache.py` — чтение/запись/чистка по TTL (30 дней,
  `ENGINE_CACHE_TTL_DAYS`); `EngineService.analyze` проверяет кэш до
  поиска и пишет результат после; ключ включает версию движка (UCI
  `id name`) и параметры уровня (beginner 12/1 ≠ gm 28/5); выключается
  `ENGINE_CACHE_ENABLED=false`; в lifespan — очистка устаревших строк.
  Тесты: `tests/unit/test_engine_cache.py`,
  `tests/integration/test_engine_cache.py` (включая реальный Stockfish:
  повторный `/v1/analyze` быстрее).
- **Retention `llm_calls` (KI-048, итерация 29):**
  `scripts/prune_llm_calls.py` — батчевый `DELETE` вызовов LLM старше
  `LLM_CALLS_RETENTION_DAYS` (по умолчанию 90 дней): `DELETE ... WHERE id
  IN (SELECT id ... ORDER BY id LIMIT N)` в цикле, commit на батч, пауза
  между батчами. Флаги `--retention-days`, `--batch-size`,
  `--max-batches`, `--sleep-ms`, `--dry-run`, `--json`; exit code 0/2.
  Запуск по cron/планировщику описан в `GETTING_STARTED.md` §9.1;
  партиционирование вынесено в KI-052. Тесты:
  `tests/unit/test_prune_llm_calls.py`,
  `tests/integration/test_prune_llm_calls.py`.
- **LLMOps-дашборд в админке (T-109, итерация 29):** страница
  `/admin/llmops` (`LlmOpsDashboardView`, `app/llmops_dashboard.py`,
  Jinja2 + Bootstrap, read-only) — агрегаты за 24 часа, 7 и 30 дней:
  latency p50/p95/p99 и avg по реальным вызовам (`cache_hit=false`),
  error rate (`error`/`timeout`), cache-hit rate, суммы токенов и
  стоимости с разбивкой по `model`/`level`, виджет «Топ-5 пользователей
  по расходу токенов» из `usage_daily`. Доступна admin/auditor. Тесты:
  `tests/integration/test_admin_llmops_dashboard.py`,
  `tests/unit/test_llmops_dashboard_unit.py`.
- **FinOps durable-агрегаты `usage_daily` (T-031b, итерация 28):**
  миграция `e9f0a1b2c3d4` — таблица `usage_daily` (subject_type/
  subject_key/day, tokens_in/out, cost_usd, requests,
  UNIQUE(subject_type, subject_key, day), индексы `(day, subject_type)` и
  `(subject_type, subject_key, day)`, CHECK ≥ 0). `record_token_usage`
  после Redis-инкремента best-effort upsert-ит дельту в фоне
  (`ON CONFLICT DO UPDATE`, отдельная сессия, `wait_for_pending_usage_upserts`);
  `/v1/users/me/usage` — Redis + durable fallback (`used = max(redis, db)`);
  `scripts/aggregate_usage.py` — месячные суммы по дням/типам, `--json`,
  `--retention-days` (90 по умолчанию), `--no-cleanup`. Тесты:
  `tests/unit/test_finops_durable.py`, `tests/integration/test_usage_daily.py`.
- **JSONL-экспорт датасета в админке (T-034b, итерация 28):**
  `GET /v1/admin/exports/edits.jsonl` — streaming NDJSON с
  `Content-Disposition`, keyset-батчами (без роста памяти); фильтры
  `date_from/date_to`, `level`, `human_edited`, `schema_version` (1|2);
  v2 переиспользует `scripts/export_dataset` (system-промпт + пакет
  фактов, пропуск проблемных записей), v1 — legacy original→edited.
  Авторизация: JWT admin/auditor (user → 403) **и** cookie-сессия
  `admin_session` (кнопка из админки); страница `/admin/exports/dataset`
  (`DatasetExportView`, меню «Экспорт датасета») с формой фильтров.
  Тесты: `tests/integration/test_admin_exports.py`.
- **KI-046 закрыт (итерация 28):** warmup обёрнут в
  `llm_call_context(operation="warmup")`; `attach_commentary_id()` после
  commit комментария заполняет `commentary_id` в `llm_calls` (sync и SSE);
  cache-hit пути commentary/chat пишут строку `cache_hit=true`,
  `provider="cache"`, `tokens=0`, `cost=0`, latency кэша.
- **LLMOps: durable `llm_calls` и сквозной `trace_id` (T-030, итерация 27):**
  миграция `d8e9f0a1b2c3` — таблица `llm_calls` по DESIGN §4.4
  (trace_id/span_id, request_id, actor_id/game_id/commentary_id,
  operation/provider/model/level, prompt_slug/prompt_version,
  tokens_in/out, cost_usd, latency_ms, cache_hit, status, error_code,
  created_at; UNIQUE(trace_id, span_id), BRIN(created_at) и DESC-индексы).
  `TraceMiddleware` (`app/tracing.py`): W3C `traceparent` (валидация/
  генерация), `request.state.trace_id`/`span_id`, contextvars, structlog,
  заголовок **`X-Trace-Id`** в ответе, OTel-span `http_request` с
  корреляцией. Recorder `app/llm/calls.py` пишет вызовы `complete` и
  `stream_generate` **best-effort** (`asyncio.create_task` + отдельная
  сессия, не блокирует ответ; ошибки только в лог); prompt_slug=level,
  prompt_version из `prompts.version`; без PII/текстов. Контекст
  operation/actor_id/game_id в `/v1/commentary` и `/v1/chat` (sync+SSE).
  Read-only view `LlmCallAdmin` в `/admin` (фильтры по модели/операции/
  статусу).
- **OTel-спаны validator/RAG (T-030b, итерация 27):** `validator.validate`
  (moves/valid/illegal_count, Status OK/ERROR) и `rag.retrieve`
  (top_k/results, OK/ERROR) с `trace.id` из `app.tracing.current_trace_id()`
  (fallback — активный OTel-span); PII/тексты в атрибуты не пишутся.
- **FinOps по JWT-пользователю (KI-038, итерация 26):**
  `TokenBudgetMiddleware` и `/v1/users/me/usage` считают дневной бюджет по
  `user:{id}` и tier из claim `role` (admin/auditor → pro, user → free);
  анонимно/битый токен — прежний IP + `PRO_IPS`. Ключ Redis —
  `usage:<date>:user:<id>`.
- **Dataset schema v2 (KI-039, итерация 26):** `scripts/export_dataset.py`
  по умолчанию пишет `schema_version=2`: `system` — промпт уровня,
  `user` — `build_fact_package(...)` (пакет фактов из
  `games/moves/motifs`), `assistant` — `edited_text`; вложенный `metadata`
  (id, commentary_id, game_id, editor_id, level, model, created_at,
  fact_sheet_chars). Проблемные записи пропускаются со статистикой;
  `--schema-version 1` оставлен legacy. `scripts/train_lora.py`
  поддерживает `{1,2}`, печатает распределение и предупреждает о
  rewrite-семантике v1; `LORA_TRAINING.md` переписан под v2.
- **RAG-коллекция и eval-фильтр (T-042, итерация 25).** В
  `annotated_games` загружены новые PGN: `sample3.pgn` (500),
  `sample4.pgn` (13), `sample5.pgn` (9) — **522 партии** с
  `source=new_samples` (`--limit 1000`; итого в таблице **624** записи:
  102 `my-collection` + 522, 621 с аннотациями, 0 `NULL` embedding).
  `scripts/evaluate_llm.py` получил `--source SOURCE` — фильтр по
  `annotated_games.source` для воспроизводимого сравнения LoRA
  «до/после»; методика и команды описаны в
  [LLM_EVAL.md](development/reports/LLM_EVAL.md).
- **LoRA fine-tuning (T-104, итерация 25):** `scripts/train_lora.py` —
  реальный SFT на `peft`/`transformers` (QLoRA 4-bit, r=16,
  `lora_alpha=32`, `target_modules=["q_proj","v_proj"]`, 3 эпохи,
  lr=2e-4), маскирование лосса только на assistant-токенах,
  right-padding-коллатор, gradient checkpointing; `--dry-run`/`--check-env`
  работают без `torch`, реальный запуск без GPU/зависимостей падает с
  понятной инструкцией; `--system-prompt`, `--limit`, `--check-env`.
  Экспорт `scripts/export_dataset.py` — схема `system → user → assistant`
  (`system` из `app/prompts/<level>.txt`). `LORA_ADAPTER_PATH`
  учитывается в `Settings.lora_model`/`app/llm/factory.py` только для
  существующего адаптера (`adapter_config.json`), с логами
  `llm_lora_adapter_enabled` / `llm_lora_adapter_unavailable`.
  Инструкция по GPU/облаку, стоимости и vLLM —
  [LORA_TRAINING.md](development/reports/LORA_TRAINING.md). Реальное
  обучение не выполнялось (нет CUDA) — KI-030 Open, KI-039.
- **Playwright E2E (T-036b, итерация 25):** `@playwright/test` 1.64.1,
  `web/playwright.config.ts` (projects `chromium` + `mobile`, baseURL
  `http://127.0.0.1:5173`, webServer Vite, trace/screenshot/video при
  падении), 5 сценариев в `web/e2e/` (`auth`, `analyze`, `commentary`,
  `history`, `limits`) с page.route-моками API; скрипты
  `test:e2e`/`test:e2e:ui`; CI-job `e2e` с `upload-artifact` при
  падении; real-backend-режим `E2E_REAL_BACKEND=1` (локально 5 passed).
  Ограничение CI — KI-040.
- **Тесты (итерация 25):** `tests/unit/test_train_lora.py` (41),
  `test_dataset_export.py` (6), `test_lora_config.py` (13), фикстура
  `tests/fixtures/lora_sample.jsonl`; E2E 10 mocked-прогонов (5×2).
- **JWT-аутентификация и изоляция данных (T-041, KI-035 закрыт, итерация 24):**
  миграция `c7d8e9f0a1b2` — `users.role` (enum `admin_role`: user/admin/
  auditor), `is_active`, `password_hash`, `last_login_at`;
  `commentaries.user_id`, `edits.user_id`; индексы `ix_games_user_id_created`,
  `ix_users_role`, `ix_commentaries_user_id`, `ix_edits_user_id`.
  Пакет `app/auth/`: bcrypt-хеширование (passlib), access+refresh JWT
  (python-jose) — позже заменены на Argon2id (итерация 26) и PyJWT/
  HS256 (T-110, итерация 34); зависимости `get_current_user` /
  `get_current_user_optional` / `require_admin`, проверки владельца
  партии/комментария.
- **`/auth` API:** `POST /auth/register` (201 профиль, 409 `email_taken`,
  422 на слабый пароль), `POST /auth/login` (200 access+refresh+профиль,
  401/403), `POST /auth/refresh` (ротация пары), `GET /auth/me`.
- **Изоляция данных:** `POST /v1/analyze` пишет `games.user_id`; `GET
  /v1/games` — только свои партии (admin — все, legacy NULL-owner — только
  admin); `GET /v1/games/{id}` — чужой id → 404; `POST /v1/commentary(/stream)`
  и `POST /v1/chat(/stream)` проверяют доступ к партии до вызова LLM и
  пишут `commentaries.user_id` (включая SSE); `PATCH /v1/commentaries/{id}`
  проверяет владельца и пишет `edits.user_id`.
- **Usage по ролям:** `GET /v1/users/me/usage` для JWT-пользователя —
  admin/auditor → `pro`, user → `free`, subject `user:{id}`; анонимно —
  прежний IP-путь (KI-031).
- **Фронтенд auth-flow:** страница `/login` (вход/регистрация, RU/EN),
  Zustand-стор `chess.auth` в `localStorage`, Bearer-заголовок и
  single-flight refresh на 401, `AuthGate` с редиректом на `/login`,
  профиль email + «Выйти» в шапке; SSE-запросы тоже авторизуются.
- **Тесты:** `tests/unit/test_auth.py` (24), `tests/integration/test_auth_api.py`
  (15, real auth), `tests/integration/test_data_isolation.py` (11),
  `tests/integration/test_usage_roles.py` (4); фронтенд — `authStore.test.ts`
  (7), `api.auth.test.ts` (9).
- **История партий и лимиты (T-036/T-037, итерация 20):**
  `GET /v1/games`, `GET /v1/users/me/usage`, страницы `/history` и
  `/analysis/:id`, usage-badge с модалкой Pro, SPA-fallback.
- **История чата (T-037):** Zustand + `sessionStorage`, передача
  `history` в `/v1/chat` и `/v1/chat/stream`; user-реплики повторно
  проверяются guardrails.
- **Mobile, skeleton, Error Boundary (T-038, итерация 21):** адаптивность
  до 480 px, shimmer-лоадеры, fallback с кнопкой reload.
- **LLM Evaluations (T-039, итерация 22):** `llm_evaluations`, read-only
  view в админке, запись из `evaluate_llm.py`.
- **CI caching (T-040, итерация 23):** pip-кэш, pnpm store cache,
  `workflow_dispatch`, полный прогон.
- **Semantic Cache (T-032, итерация 19):** `semantic_cache` (vector
  768, HNSW), exact `fen_hash+level+prompt_hash` и semantic
  cosine < 0.05; `/v1/commentary`, `/chat` и SSE отдают кэш; TTL 30
  дней + чистка; `prompt_hash` включает sysprompt/модель/style/question.
- **LoRA-пайплайн (T-104/T-034, итерация 19):**
  `scripts/export_dataset.py` (JSONL из `edits`),
  `scripts/train_lora.py` (QLoRA-каркас, `--dry-run`),
  `LORA_ADAPTER_PATH` → basename adapter-модели для vLLM.
- **Prompt Management (T-029, итерация 17):** таблица `prompts`
  (`level`, `content`, `version`, `updated_at`), миграция
  `e3f4a5b6c7d8`, seed из `app/prompts/*.txt` при старте, async
  `load_system_prompt` с in-memory кэшем, `reset_prompt_cache`, горячая
  инвалидация после правки и редактируемый view `PromptAdmin` (версия
  инкрементируется, `content` меняется без рестарта).
- **FinOps / token budget (T-031, итерация 17):** дневные Redis-счётчики
  `usage:{date}:{ip|user}`, `TokenBudgetMiddleware` для
  analyze/commentary/chat (+stream), `429 token_budget_exceeded` с
  `X-RateLimit-Reset`/`Retry-After`, лимиты
  `DAILY_TOKEN_LIMIT_FREE/PRO`, `cost_usd` по ценам из конфига в
  `commentaries` и OTel-спанах.
- **Admin Panel (T-028, частично):** `app/admin.py` — `/admin` на
  `starlette-admin` 1.0 (тот же `AsyncEngine`), защита `X-Admin-Token`
  (тот же `ADMIN_API_KEY`) или cookie-сессия формы входа
  (`ADMIN_USERNAME`/`ADMIN_PASSWORD`), 401 без учётных данных и 403 на
  неверный токен. Read-only views `annotated_games`, `commentaries`,
  `edits`; `tzdata` для конвертации времени на Windows.
- **OTel-спаны LLM (T-030, частично):** `llm_generate` и `llm_stream` в
  `OpenAICompatibleClient` с атрибутами `llm.model`, `llm.level`,
  `llm.tokens_in`, `llm.tokens_out`, `llm.cost_usd`, `llm.status`;
  экспорт `console`/`otlp` при `OTEL_ENABLED=true`.
- **Стандарт отчётов (RULES §2.18):** отчёт о фазе — строго 7 разделов;
  файл `docs/development/reports/v0.1.0/ITERATION-16.md`.
- **Синхронизация UI (итерация 15):** `react-chessboard` 5.12.1
  (`<Chessboard options={{position}} />`), `chess.js`
  `history({verbose:true})` для FEN из PGN, `react-i18next` (RU/EN),
  Zustand-стор `analysisStore.ts` с persist в `sessionStorage`.
- **SSE-стриминг (T-032):** `OpenAICompatibleClient.stream_generate`,
  `POST /v1/commentary/stream` и `POST /v1/chat/stream`
  (`sse-starlette`), события `meta`/`delta`/`done`/`error`, финальная
  SAN-валидация; фронтенд читает `ReadableStream` и печатает текст
  постепенно.
- **RAG 768:** `LMStudioEmbedder` (`/v1/embeddings`,
  `text-embedding-nomic-embed-text-v1.5`, L2), миграция `vector(768)`,
  `scripts/reembed_annotated.py`; hash-эмбеддер — fallback тестов.
- **`GET /v1/games/{id}`:** сохранённый анализ (`moves`,
  `analysis_level`, `commentaries`) без повторного Stockfish.

### Changed

- **Релиз v1.0.0 (итерация 35):** версия `1.0.0` в `pyproject.toml`,
  `app/__init__.py` и `web/package.json`; финальные README (docker
  compose quick start, статус v1.0.0), CHANGELOG `[1.0.0]`, ARCHITECTURE
  (C4 Context/Container, локализация, фоновые выгрузки), GETTING_STARTED
  (тесты/E2E), docs/README, TODO, RELEASES; KI-058 закрыт в объёме
  локализации (остатки — русский факт-пакет, en-промпты файлами, нет
  вычитки носителем).
- **HTTP-ответы (итерация 27):** добавляется заголовок `X-Trace-Id`
  (W3C trace id из `traceparent` или сгенерированный); `X-Request-Id`
  сохраняется в `llm_calls.request_id`; структурированные логи содержат
  `trace_id`/`span_id` (structlog contextvars).
- **`app/observability.py`:** `get_tracer(name)` принимает имя трейсера
  (для валидатора/RAG/HTTP используются разные имена); `OTEL_ENABLED=false`
  — no-op, `llm_calls`/`X-Trace-Id` продолжают работать.
- **Admin (итерация 27):** импорт строк закрыт (`can_import=False`)
  для всех views — вставка в обход аудита запрещена; auditor не получает
  export (экспорт — POST-действие, блокируется guard); legacy
  `X-Admin-Token`/парольная сессия работают как admin (деприкейт —
  отдельный шаг).
- **Контракт `/auth` (итерация 26):** `/auth/login` и `/auth/refresh`
  возвращают `{access_token, token_type, expires_in, user}` без
  `refresh_token`; refresh читается из HttpOnly-cookie (fallback — JSON-тело
  для curl/CLI), при успехе cookie ротируется; `POST /auth/logout` — 204.
  Ограничение длины пароля — 8..256 символов (bcrypt-лимит 72 байта снят).
- **Зависимости (KI-036, T-110):** удалены `passlib`, `types-passlib`,
  `python-jose[cryptography]` и стабы `types-python-jose`; добавлены
  `argon2-cffi>=23.1` и `PyJWT>=2.8` (HS256); `bcrypt>=4.1` оставлен только
  для проверки legacy-хешей (без пина `<4.1`); `email-validator` остаётся
  в зависимостях; в `.env.example` — комментарии про JWT и test-seam.
- **Фронтенд auth (KI-037):** `authStore` — memory-only access/user,
  `bootstrapAuth()` вместо `hydrateAuth()`, `AuthGate` ждёт bootstrap,
  logout вызывает API; `web/e2e`-моки переведены на новый контракт.
- **FinOps (KI-038):** `TokenBudgetMiddleware` принимает `Settings` вместо
  `pro_ips`/`trust_forwarded_for`; `request_subject`/`request_tier` стали
  JWT-aware; `/v1/users/me/usage` не требует БД.
- **Документация (итерация 26):** README (auth), `SPEC.md` (Argon2id,
  cookie/denylist, per-user FinOps), `KNOWN_ISSUES` (KI-036/037/038/039 →
  Closed, +KI-041…045), `TODO` (T-028 финал, T-043/T-044), `RELEASES`,
  `ITERATION-26.md`.
- **LoRA-экспорт (итерация 25):** JSONL из `edits` теперь строго
  `system → user → assistant` (`system` — промпт уровня из
  `app/prompts/*.txt`); старые выгрузки без `system` требуют
  переэкспорта. `Settings.lora_model` возвращает basename адаптера только
  если путь существует (файл или каталог с `adapter_config.json`) —
  иначе базовая `VLLM_MODEL`, без «model not found» у vLLM.
- **CI (итерация 25):** новый job `e2e` (Playwright mocked, артефакты при
  падении); `vitest` исключает `web/e2e/**`, в `.gitignore` добавлены
  `web/test-results/`, `web/playwright-report/`, `web/blob-report/`,
  `models/`.
- **Защищённые эндпоинты:** `/v1/analyze`, `/v1/games*`, `/v1/commentary*`,
  `/v1/chat*` требуют `Authorization: Bearer <access>` (401 `unauthorized`
  без токена); `/v1/ingest` по-прежнему защищён `X-Admin-Token`.
- **Salvage из локальной ветки итераций 10–11:** `LMStudioEmbedder` —
  `EmbeddingError` (503 `embedding_unavailable`) для HTTP/таймаутов/сети/
  битого JSON/неверной размерности/вырожденных векторов, батчевый
  `embed_many` (32, порядок по `index`) и батчевый ingest;
  `scripts/ingest_pgn.py` — `--limit` по умолчанию **10** (защита от
  случайной массовой загрузки) и чистые exit-коды; дополнительные тесты
  `GET /v1/games/{id}` (глубокий parity POST↔GET, FEN-старт, 404,
  порядок комментариев) и детерминированный порядок мотивов.
- **Тестовая сюита:** тестовый seam `APP_ENV=test` + `TEST_AUTH_BYPASS=true`
  (dependency override `get_current_user` на тестового admin-пользователя;
  в dev/prod недостижим); `tests/conftest.py` включает его, реальная
  авторизация проверяется отдельными тестами.
- **Semantic cache выключен в eval/тестах по умолчанию:** иначе повторные
  прогоны не проверяют генерацию; включается `SEMANTIC_CACHE_ENABLED=true`.
- **Промпты из БД:** `load_system_prompt` стал async (читает `prompts`
  при промахе кэша и падает обратно на `.txt` при недоступной БД);
  вызовы в commentary service/streaming обновлены.
- **KI-030 / `club`:** промпт `club.txt` сокращён и получил few-shot
  примеры **без SAN**; пакет фактов для `club` — только топ-3 момента без
  PV (было до 10 с PV); заголовки моментов однозначно называют сторону и
  номер. LLM-as-judge (полный пакет): **4.40/10** против 3.83, 5/5 без
  `502`, prompt-токены ≈ 3.4k → 1.9k. Конкретные few-shot (V1) оказались
  вредны — 4B-модель копировала ходы из примеров (3.00/10). Валидатор
  больше не считает одиночную клетку (`e1`) ходом.
- `games` получила `analysis_level` и `analysis_time_ms`; `AnalyzeResponse`
  отдаёт `analysis_level`.

### Fixed

- **KI-047 (итерация 28):** необработанные исключения (500) теперь
  возвращают заголовок `X-Trace-Id`: app-level handler `Exception`
  читает `request.state.trace_id` (его выставляет `TraceMiddleware` до
  входа в приложение) и отдаёт
  `{"detail": "Internal Server Error", "code": "internal_error"}`;
  structlog пишет `unhandled_exception` (path/method/trace_id/тип без
  текста исключения). Доменные `AppError` продолжают обрабатываться
  отдельным handler'ом. Тест:
  `test_tracing.py::test_unhandled_500_has_trace_id_header`.
- **KI-046 (итерация 28):** неполный охват `llm_calls` — см. Added
  (warmup/commentary_id/cache_hit).
- **KI-051 (итерация 28):** production wheel/образ не содержал `scripts/`,
  но `app/api/v1/admin_exports.py` импортировал `scripts.export_dataset`
  → `uvicorn app.main:app` падал на `ModuleNotFoundError`. Builder'ы
  (`build_fact_sheet`, `record_from_edit`, `ExportStats`, константы схемы,
  `load_system_prompt`) вынесены в `app/dataset/export.py`;
  `scripts/export_dataset.py` реэкспортирует их (публичный контракт
  сохранён), API-слой импортирует только `app`. В CI docker-job добавлен
  smoke `docker run --rm chessannotator:ci python -c "import app.main"`.
- **KI-033 закрыт (итерация 18):** аудит правки промпта пишется в
  `PromptAdmin.after_edit` в той же транзакции, что и `prompts`; сбой
  аудита откатывает правку.
- **KI-032 закрыт (Pub/Sub кэша промптов):** канал `prompts_updates`,
  watcher в lifespan, публикация после commit в админке — кэш промптов
  инвалидируется между воркерами; локальный `reset_prompt_cache` остаётся
  fallback'ом.
- **KI-021 закрыт (итерация 17):** `LLMUsage.cost_usd`,
  `commentaries.cost_usd` и OTel-атрибут `llm.cost_usd` считаются по
  `LLM_PRICE_*`/`LLM_LOCAL_PRICE_*`.

### Security

- **Security review (T-110, итерация 34):** `pip-audit` — 3 уязвимости
  до, 0 после; `python-jose`/`ecdsa` заменены на **PyJWT** (HS256,
  формат токенов совместим); `Settings` падает в `APP_ENV=prod/
  production` на dev/коротком `JWT_SECRET` и dev-`ADMIN_API_KEY`, а
  также запрещает `CORS_ORIGINS=*` (при `allow_credentials=True`).
  Проверены raw SQL/ORM (`llmops_dashboard.py`, `export_jobs/jobs.py`) —
  пользовательский ввод параметризуется SQLAlchemy.
- **Аудит экспорта датасета (KI-050, итерация 29):** после завершения
  NDJSON-стрима `GET /v1/admin/exports/edits.jsonl` в `audit_logs`
  пишется `dataset.export` с актором (`jwt:`/`session:`), фильтрами,
  числом фактически выгруженных строк, `trace_id` и флагом `completed`
  (best-effort, сбой не ломает ответ). Тесты — в
  `tests/integration/test_admin_exports.py`.
- **Timing-safe login (KI-043, итерация 27):** `DUMMY_PASSWORD_HASH`
  (Argon2id с параметрами по умолчанию); `/auth/login` для несуществующего
  email выполняет Argon2-verify и возвращает тот же 401 — медианы времени
  ответа 55.4 / 54.8 мс (ratio 1.01).
- **RBAC в Admin Panel (KI-045, итерация 27):** `AdminAuthGuard` выставляет
  `request.state.admin_role`; auditor строго read-only — любые
  POST/PUT/PATCH/DELETE в `/admin/**` → 403 `admin_forbidden`; role-aware
  hooks (`PromptAdmin.can_edit`, `can_import`, `can_export`); admin и
  legacy-пути сохраняют прежние права; read-only `LlmCallAdmin`.
- **Auth tech debt (KI-036/KI-037, итерация 26):** пароли — **Argon2id**
  (`argon2-cffi`, `$argon2id$`), legacy bcrypt-хеши перехешируются при
  успешном входе (`verify_and_update`, лог `password_rehashed`). Refresh-токен
  больше не возвращается в JSON: `/auth/login` и `/auth/refresh` кладут его
  в HttpOnly-cookie (`SameSite=Lax`, `Path=/auth`, `Max-Age` = TTL, `Secure`
  при `AUTH_COOKIE_SECURE=true`), добавлены настройки `AUTH_COOKIE_*`.
  Новый `POST /auth/logout` отзывает `jti` в Redis
  (`refresh_denylist:<jti>`, TTL до `exp`) и очищает cookie; повторное
  использование отозванного токена → 401; ротация refresh инвалидирует
  прежний `jti` (защита от replay). Access-токен на фронтенде — только в
  памяти, `credentials: "include"`, single-flight refresh и bootstrap через
  `/auth/refresh`, `localStorage` для токенов не используется.
- **Admin JWT (T-028 финал, итерация 26):** `/admin` принимает access JWT
  (`Authorization: Bearer` или cookie `access_token`): `admin`/`auditor` →
  200, `user` → 403 `admin_forbidden`, битый/просроченный → 403, без
  кредов → 401. Legacy `X-Admin-Token` и парольная форма сохранены как
  fallback.
- **Prompt Injection Guardrails (T-033, итерация 18):**
  `app/security/guardrails.py` — NFKC-нормализация, удаление
  control/zero-width/bidi, лимит 2000, 7 правил (override/leak/role/
  jailbreak/role-tags, RU+EN); `inspect_user_input` → 422
  `prompt_injection` в `/v1/chat` и `/chat/stream`; запрет раскрывать
  системные инструкции в `CHAT_SYSTEM_PROMPT`.
- **Security Audit (T-035, итерация 18):** append-only `audit_logs`
  (`timestamp`, `actor`, `action`, `target_id`, `details`,
  `prev_hash`, `current_hash`), миграция `f4a5b6c7d8e9`, hash-chain,
  advisory-lock и триггер `UPDATE/DELETE/TRUNCATE`; аудит правки промпта,
  `PATCH /v1/commentaries/{id}` и заблокированных инъекций; read-only
  view и страница «Проверить целостность».
- **`X-Admin-Token`:** constant-time защита `POST /v1/ingest`
  (`ADMIN_API_KEY`).
- **Feature-rate-limit на `/auth/login|register`** (10/мин на IP) —
  базовая защита от перебора паролей.
- **KI-031 закрыт:** тир `free/pro` больше не берётся из
  `X-User-Tier`/`X-User-Id`; `pro` — только IP из `PRO_IPS`,
  `X-Forwarded-For` — только при `TRUST_FORWARDED_FOR=true`.

## [0.1.0] — 2026-10-28

### Added

- **Web UI (T-019…T-022):** React 19 + Vite SPA: вставка PGN, выбор уровня,
  доска из FEN, список ходов с оценками и классами, карточка хода с
  мотивами/рекомендацией, панель комментария с inline-редактором и PATCH,
  чат по партии. 14 Vitest-тестов на FEN и форматирование.
- **Production-раздача SPA (T-025):** Vite-сборка `web/dist` отдаётся
  FastAPI `StaticFiles`; multi-stage Dockerfile (Node → Python), non-root
  пользователь, Stockfish в образе; `docker-compose.prod.yml` с loopback-
  портами БД/Redis и healthcheck.
- **LLM-as-judge (T-023):** `scripts/evaluate_llm.py` — 5 партий ×
  `club`/`gm`, оценки DeepSeek по трём шкалам, отчёт
  `docs/development/reports/LLM_EVAL.md`. Средняя **5.19/10** (9 из 10),
  ниже порога 7/10 — заведён KI-030 (план улучшения промптов); часть
  `club`-генераций не укладывается в production-таймаут (KI-024).
- **Документация MVP (T-026):** `USER_GUIDE.md`, `ASSISTANT_LEVELS.md`,
  `ARCHITECTURE.md`, `MANUAL_TESTING_GUIDE.md`, README и RELEASES под
  v0.1.0.
- **API для доски:** `AnalyzeResponse.initial_fen` и `fen` у каждого хода —
  чтобы клиент рисовал позицию без собственного шахматного движка.
- **Production hardening plan (T-028…T-035, документация):** в `TODO.md`
  добавлена Фаза 7 — admin control plane, prompt management, LLMOps,
  FinOps, semantic cache, prompt-injection guardrails, dataset export и
  security audit; расширен Backlog (T-111…T-119). Создан
  `docs/development/DESIGN_ADMIN_AND_OPS.md` (схема БД, админка,
  трейсинг, безопасность), `SPEC.md` §10 дополнен Admin Control Plane /
  Observability / FinOps. Код и миграции не менялись.
- **`PATCH /v1/commentaries/{id}` (T-021b):** ручная правка текста
  комментария — `commentaries.text` обновляется, `human_edited = true`,
  пара «оригинал → правка» пишется в `edits` (датасет для T-104);
  ответ содержит `edit_id`; 404 на неизвестный id, 422 на пустой текст.
- **`POST /v1/chat` (T-022):** диалог по `analysis_id` — метаданные,
  ключевые ходы, мотивы и оценки из БД → LLM по уровню → валидатор SAN с
  retry → `{"reply", "tokens_used"}`; rate limiting, 404/422/502/503/504.
- **`scripts/ingest_pgn.py`:** CLI для загрузки PGN в `annotated_games`
  (`--file`, `--limit`, `--source`), UTF-8/cp1251, общий с
  `POST /v1/ingest` пайплайн; `ingest_pgn_text(..., limit=...)`.
- **Тесты:** `tests/integration/test_api_commentaries_patch.py` (4),
  `tests/integration/test_api_chat.py` (9, включая живой диалог с LM Studio).
- **RAG-коллекция (итерация 12):** в `annotated_games` загружено 102
  партии из `data/` (`my-collection`: 10 + 42 + 50).
- **RAG (T-017):** `app/rag/` — хеш-эмбеддер (1024, по FEN), ingest
  аннотированных партий (`parse_pgn_games`, `ingest_pgn_text`), поиск
  похожих позиций (`find_similar_positions`, косинус через pgvector),
  RAG-контекст для уровня `gm` в `/v1/commentary`.
- **`POST /v1/ingest`:** загрузка PGN (multipart, до 5 МБ, UTF-8/cp1251),
  парсинг нескольких партий, сохранение аннотаций и эмбеддингов.
- **Тесты:** `tests/unit/test_rag.py` (6), `tests/integration/test_rag.py`
  (9: ingest, retrieval, `/v1/ingest`, RAG в промпте gm, не в club).
- **Комментарий (T-016):** `POST /v1/commentary` — по `analysis_id` берёт
  партию и анализ из БД, собирает пакет фактов, генерирует текст, проверяет
  ходы валидатором (до 2 retry), сохраняет в `commentaries` с токенами и
  `validated`; при нелегальных ходах после retry — `502 llm_invalid_output`.
- **Сервис генерации:** `app/commentary/service.py` — восстановление
  `MoveAnalysis` из `moves`/`motifs`/`multipv`, ретраи, стиль,
  реконструкция UCI/SAN.
- **Ошибки:** `NotFoundError` (404), `LLMValidationError` (502).
- **Системные промпты (T-014):** `app/prompts/{beginner,club,gm}.txt` —
  строгие правила (только факты из пакета, SAN без Markdown, фиксированная
  длина), загрузчик `load_system_prompt(level)` с кешем.
- **Пакет фактов:** `build_fact_package(game, analyses, level)` —
  метаданные, ходы партии, до 10 ключевых моментов с оценкой, классом,
  лучшим ходом, рекомендацией Policy, PV в SAN и мотивами.
- **Прогрев локальной LLM (KI-022 закрыт):** фоновая задача в lifespan
  отправляет короткий запрос в LM Studio при старте; ошибки не ломают
  старт, задача отменяется при остановке.
- **Валидатор ответов LLM (T-015):** `app/engine/validator.py` —
  `extract_moves_from_text` (SAN-регэксп), `validate_moves` (проигрывание
  на доске + проверка альтернатив по позициям партии),
  `validate_llm_response`.
- **Тесты:** `tests/unit/test_prompts.py` (11),
  `tests/unit/test_validator.py` (14), `tests/unit/test_warmup.py` (3),
  живой E2E `tests/integration/test_generation.py` (1).
- **``/v1/analyze``: разбор по ходам (T-012).** В ответ добавлен `moves`:
  `ply`, `san`, `uci`, `eval_cp`, `mate`, `best_move_san`, `delta_cp`,
  `classification`, `motifs` и `recommended_move` (san/uci/eval_cp/mate) —
  рекомендация Policy Layer под запрошенный уровень.
- **LLM-адаптер (T-013):** пакет `app/llm/` — `BaseLLMClient`
  (`generate`/`complete`/`aclose`), `OpenAICompatibleClient` для
  OpenAI-совместимых API (LM Studio, DeepSeek), фабрика
  `get_llm_client(level)`: `beginner`/`club` → LM Studio,
  `gm` → DeepSeek; таймауты, ретраи на 429/5xx с `Retry-After`, учёт
  токенов (`LLMUsage`).
- **Ошибки LLM:** `LLMError`, `LLMNotConfiguredError`, `LLMTimeoutError`
  (503/503/504).
- **Тесты:** `tests/unit/test_llm.py` (13, моки `httpx.MockTransport`),
  `tests/integration/test_llm_live.py` (2 живых, со skip), +4 теста policy
  (KI-020 и различие рекомендаций beginner/gm).
- **Motif Detector (T-010):** пакет `app/motifs/` — `detect_motifs(fen, multipv)`
  находит проходные пешки, слабые поля у короля, открытые линии и диагонали,
  проблемы безопасности короля, вилки/связки по доске, мат и выигрыш
  материала по лучшей линии движка (дедупликация, лимит 6 мотивов).
- **Мотивы в БД:** `MoveAnalysis.motifs` → таблица `motifs` (type,
  description, importance, move_id) в транзакции анализа.
- **Policy Layer (T-011):** `app/engine/policy.py` — `choose_recommended_move`
  для трёх уровней: `gm` (топ-1), `club` (альтернатива при разнице < 20 cp),
  `beginner` (отбрасывание раннего ферзя и «зевков», приоритет развитию,
  центру и рокировке).
- **PV-линии в БД (KI-018 закрыт):** `MoveAnalysis.lines` → `moves.multipv`
  (JSONB, до 3 линий на уровне `club`); отдельная миграция не потребовалась —
  колонка была создана в T-004.
- **Тесты:** `tests/unit/test_motifs.py` (13), `tests/unit/test_policy.py` (15).
- **``POST /v1/analyze`` (T-009):** PGN + `level` → структурированный анализ
  (moves, evals, классификация) с сохранением в `games`/`moves` и
  `analysis_id` в ответе; партии с FEN-стартом поддерживаются через
  PGN-заголовки `[SetUp]`/`[FEN]`.
- **`EnginePool` (T-009, KI-014 закрыт):** 4 параллельных процесса
  Stockfish (`ENGINE_POOL_SIZE`, до 8), очередь свободных, порядок
  результатов сохраняется; SLA на 53 полухода — 14.6 с (лимит 30 с).
- **Глобальный обработчик доменных ошибок:** `AppError` →
  `{"detail", "code"}` с кодами 400/422/500/504; ошибки движка отдают
  `Engine unavailable`.
- **Rate limiting:** Redis `INCR`+`EXPIRE`, 10 запросов/мин на IP для
  `/v1/analyze`, `Retry-After`/`X-RateLimit-*`, fail-open при недоступном
  Redis (KI-015).
- **Lifespan приложения:** пул движков и Redis-клиент создаются/закрываются
  с приложением; `create_app(settings)` для тестовой конфигурации.
- **Тесты:** `tests/unit/test_engine_pool.py` (7),
  `tests/unit/test_rate_limit.py` (7),
  `tests/integration/test_api_analyze.py` (4, включая SLA и проверку БД).

- **Engine Service (T-008):** пакет `app/engine/` — async-обёртка над
  Stockfish: `EngineService.analyze()` (MultiPV, параметры по уровню из
  SPEC §2.2), `classify_move()` (best/good/inaccuracy/mistake/blunder),
  `analyze_game_moves()` (оценка «до/после» для каждого полухода,
  `delta_cp`, SAN/UCI, мат как `±(10000 − 100·N)`); собственный
  асинхронный UCI-клиент с таймаутами и перезапуском процесса.
- **Ошибки движка:** `EngineError`, `EngineNotFoundError`,
  `EngineTimeoutError`, `InvalidLevelError` в `app/errors.py`.
- **`ParsedGame.initial_fen`:** стартовая позиция партии (для партий с
  FEN-стартом); используется движком при анализе истории ходов.
- **Тесты движка:** `tests/unit/test_engine.py` — 27 тестов, включая
  интеграцию с реальным Stockfish (MultiPV, бенчмарк «клуба», первые
  3 хода партии, легальность лучшего хода).
- **Ingestion (T-007):** пакет `app/ingestion/` — `parse_pgn()` (PGN →
  `ParsedGame`), `parse_fen()` (валидация FEN), `find_position_repeats()`
  (повторы позиций); ECO-код из заголовка `[ECO]` с fallback на
  курируемую таблицу дебютных префиксов; разметка фаз
  (дебют ≤ 30 полуходов, эндшпиль по материалу `< 2000 cp` или `< 10`
  фигур).
- **Доменные ошибки ingestion:** `AppError`, `InvalidPgnError`,
  `InvalidFenError` (`code`, `http_status` для будущего API-слоя).
- **Модели результата ingestion:** `ParsedMove`, `GameMetadata`,
  `PhaseBreakdown`, `PositionRepeat`, `ParsedGame` (Pydantic v2, `frozen`,
  `extra="forbid"`).
- **Тесты ingestion:** `tests/unit/test_ingestion.py` — 33 теста
  (10 входных PGN, FEN после каждого полухода, флаги взятия/шаха, фазы,
  ошибки, ECO, повторы), `xfail`-маркер KI-012.
- **Фикстура:** `tests/fixtures/test_game.pgn` (Test White / Test Black)
  и таблица инвариантов в `tests/fixtures/README.md`.
- **Bootstrap проекта (T-001…T-006):** FastAPI-скелет (`app/main.py`,
  `app/config.py`), healthcheck `/v1/health` с проверкой БД, Redis,
  Stockfish и LLM, Docker Compose (PostgreSQL 17 + pgvector, Redis,
  опциональные Stockfish и vLLM), CI (ruff, mypy, alembic, pytest,
  typecheck, build), фронтенд-скелет (React 19 + Vite).
- **Схема БД (T-004):** 8 таблиц по SPEC.md §4 — `users`, `games`,
  `moves`, `motifs`, `annotated_games`, `retrieval_cache`,
  `commentaries`, `edits`; нативный enum `level`, `VECTOR(1024)` с
  ivfflat-индексом, индексы `moves(game_id, ply)` и
  `retrieval_cache(fen_hash)`.
- **Первая миграция** `b802598c3778_init_schema` (upgrade / downgrade /
  повторный upgrade проверены), `alembic check` без дрейфа.
- **Тесты схемы (T-004/T-005):** `tests/test_health.py`,
  `tests/test_models.py` — 5 тестов, включая интеграционный тест схемы
  (defaults, каскады).
- **Документация:** `docs/development/RULES.md` (правила кода, тестов,
  коммитов, безопасности, окружения), `PROMPT_CURRENT.md`,
  `RELEASES.md`, `docs/CHANGELOG.md`, `docs/KNOWN_ISSUES.md`,
  `docs/README.md` (навигация), пошаговые отчёты в
  `docs/development/reports/`.
- **CI:** шаги `alembic check` и roundtrip-миграции
  (`downgrade base` → `upgrade head`) — DoD T-004.

### Changed

- `ruff`: `RUF001` добавлено в `ignore` вместе с `RUF002/RUF003` —
  сообщения и докстринги проекта на русском.
- Комментарий к `games.phase_breakdown` в `app/db/models.py` приведён к
  фактическому формату `{"opening_end_ply": …, "endgame_start_ply": …}`.
- `POSTGRES_HOST` / `REDIS_URL` по умолчанию — `127.0.0.1` вместо
  `localhost`: на Windows `localhost` резолвится в `::1`, а compose
  публикует порты только на IPv4-loopback (иначе healthcheck Redis
  падал по таймауту).
- HTTP-порт Redis в compose сделан переопределяемым: `REDIS_PORT`
  (по умолчанию 6380).
- `/v1/health` возвращает `degraded`, если хоть один компонент не `ok`
  (в том числе `skipped` для ненастроенной LLM) — как требует контракт.
- `pyproject.toml`: добавлен `pgvector`; убран `types-redis` (redis 5.x
  поставляет `py.typed`); отключены `RUF002/RUF003` (русские докстринги);
  добавлен `asyncio_default_fixture_loop_scope`.
- `web/pnpm-workspace.yaml`: `allowBuilds: esbuild` — pnpm 12 не выполняет
  postinstall-скрипты зависимостей без явного разрешения.

### Changed

- Локальный LLM-провайдер — **LM Studio** (`http://127.0.0.1:8034/v1`,
  модель `qwen/qwen3-4b-2507`, ключ-заглушка `lm-studio`); облачный —
  **DeepSeek** (`deepseek-chat`). Новые настройки: `vllm_api_key`,
  `deepseek_base_url`, `deepseek_model`, `llm_max_tokens`.
- Policy новичка: ход с `mate > 0` разрешён всегда, даже если это жертва (KI-020 закрыт).
- Ошибки движка в HTTP теперь отдают **500** (`EngineError`,
  `EngineNotFoundError`) и **504** (`EngineTimeoutError`) вместо 503 —
  как требует контракт T-009; текст для пользователя — `Engine unavailable`.
- `create_app()` принимает `Settings`, а создание пула движков и
  Redis-клиента перенесено в lifespan (корректное закрытие ресурсов).

### Fixed

- **KI-029:** `password authentication failed for user "chess"` — старый том
  PostgreSQL сохранял прежний пароль. Полный сброс `docker compose down -v`
  + `up -d postgres redis` + `alembic upgrade head`; после этого ingest
  загрузил 102 партии без ошибок авторизации.
- Валидатор учитывает позиции PV-линий (xtra_fens): цитаты вариантов из пакета фактов больше не считаются нелегальными ходами; /v1/commentary передаёт их автоматически.
- Температура LLM вынесена в настройку llm_temperature (в тестовой генерации — 0.0).
- Пул соединений asyncpg закрывается в lifespan (ngine.dispose()): без этого несколько TestClient в одном процессе переиспользовали соединения из закрытого event loop и получали 500.
- Alembic-миграция доведена руками: `import pgvector`,
  `CREATE EXTENSION vector`, явные `create/drop` нативного enum `level`
  (иначе `downgrade base` + `upgrade` падали на существующем типе).
- `mypy`: конфликт имени `text` (колонка `commentaries.text` против
  `sqlalchemy.text`), устаревшие стабы `types-redis`.
- `ruff`: неиспользуемый импорт `Field`, `AsyncIterator` из `typing`,
  бесполезные `noqa`.
