# Гайд: финальный eval модели v4 и сравнение с v3

> **Назначение:** пошагово прогнать модель v4 на том же замороженном наборе
> из 5 партий, что и v3, и получить Markdown-таблицу с дельтами.
> **Статус baseline v3:** **3.60/10** (Факты 2.60, Польза 3.40, Стиль 4.80,
> объяснения 60%, CJK 0.00%, 5/5 партий оценены).
> **Скрипты:** `scripts/evaluate_llm.py`, `scripts/compare_evals.py`.

## Оглавление

1. [Что понадобится](#1-что-понадобится)
2. [Шаг 1. Сохранить baseline v3](#2-шаг-1-сохранить-baseline-v3)
3. [Шаг 2. Переключить `.env` на v4](#3-шаг-2-переключить-env-на-v4)
4. [Шаг 3. Прогнать evaluate_llm.py](#4-шаг-3-прогнать-evaluate_llmpy)
5. [Шаг 4. Проверить, что оценивалась именно v4](#5-шаг-4-проверить-что-оценивалась-именно-v4)
6. [Шаг 5. Сравнить с v3](#6-шаг-5-сравнить-с-v3)
7. [Как читать результат](#7-как-читать-результат)
8. [Troubleshooting](#8-troubleshooting)
9. [Ссылки](#9-ссылки)

---

## 1. Что понадобится

| Компонент | Требование |
| :--- | :--- |
| Датасет | Замороженный `tests/fixtures/eval_games.json` — **не менять**; 5 PGN Moscow zt 1964, `game_id=-1..-5` |
| Baseline | `docs/development/reports/llm_eval_results.json` — прогон v3 от 2026-11-06, 5/5 партий |
| v4 | LoRA-адаптер в vLLM **или** смердженный GGUF в LM Studio; модель отвечает на `/v1/models` |
| Судья | `DEEPSEEK_API_KEY` — судья всегда DeepSeek, от модели генерации не зависит |
| Инфраструктура | PostgreSQL/Redis (оценки пишутся в `llm_evaluations`), Stockfish, `.venv` |

Проверка окружения:

```powershell
docker compose ps
.\.venv\Scripts\python.exe -c "from app.config import get_settings; s=get_settings(); print(s.local_llm_base_url, s.lora_model)"
```

Ожидается адрес LM Studio/vLLM (например, `http://127.0.0.1:8034/v1`) и имя
модели, которое сервер реально отдаёт.

---

## 2. Шаг 1. Сохранить baseline v3

`evaluate_llm.py` по умолчанию пишет JSON в
`docs/development/reports/llm_eval_results.json` и **перезаписывает** его.
Перед прогоном v4 сделайте копию baseline:

```powershell
Copy-Item docs/development/reports/llm_eval_results.json `
    docs/development/reports/llm_eval_results_v3.json
```

```bash
cp docs/development/reports/llm_eval_results.json \
   docs/development/reports/llm_eval_results_v3.json
```

Проверка, что скопирован именно baseline:

```powershell
.\.venv\Scripts\python.exe -c "import json; d=json.load(open('docs/development/reports/llm_eval_results_v3.json', encoding='utf-8')); print(len(d), {i['model_name'] for i in d})"
```

Ожидается `5 {'qwen-7b-tokenizer_gguf-v3'}`. Если файл уже существует и
содержит v4 — вы перезаписываете baseline; восстановите его из git
(`git checkout -- docs/development/reports/llm_eval_results.json`).

---

## 3. Шаг 2. Переключить `.env` на v4

Модель генерации задаётся **настройками**, а не кодом скрипта: локальный
клиент для `club` берёт `Settings.lora_model` — basename существующего
LoRA-адаптера, иначе `VLLM_MODEL` (`app/llm/factory.py`). Выберите один из
двух вариантов.

### Вариант A. LoRA-адаптер (vLLM)

```dotenv
# .env
VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct
LORA_ADAPTER_PATH=models/lora-club-v4
```

vLLM должен быть запущен с тем же basename, что и путь:

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
    --lora-modules lora-club-v4=models/lora-club-v4 \
    --max-model-len 8192 \
    --port 8034
```

Тогда API-имя модели в ответах — `lora-club-v4`, и именно его увидит
`scripts/evaluate_llm.py` в поле `model`.

### Вариант B. Смердженный GGUF (LM Studio)

```dotenv
# .env
LORA_ADAPTER_PATH=
VLLM_MODEL=qwen-7b-tokenizer_gguf-v4
```

`VLLM_MODEL` — **точный id** из списка моделей, а не желаемое имя:

```powershell
Invoke-RestMethod http://127.0.0.1:8034/v1/models | ConvertTo-Json -Depth 5
```

```bash
curl -s http://127.0.0.1:8034/v1/models | python -m json.tool
```

Скопируйте `data[].id` нужной модели в `VLLM_MODEL`. Не угадывайте: при
несовпадении LM Studio вернёт 404 `model not found`.

### Общие настройки

```dotenv
# Адрес локального бэкенда (на VPS — вход SSH-туннеля, см. DEPLOYMENT_GPU_TUNNEL.md)
LLM_LOCAL_BACKEND_URL=http://127.0.0.1:8034/v1
# Судья: отдельный лимит, иначе JSON обрезается (итерация 61)
LLM_JUDGE_MAX_TOKENS=2048
# Локальная генерация 7B может быть медленной
LLM_LOCAL_TIMEOUT_S=120
DEEPSEEK_API_KEY=<ключ судьи>
```

`evaluate_llm.py` читает `.env` при старте процесса — перезапускать backend
не нужно, достаточно запустить скрипт заново. Если модель поднимается в LM
Studio, убедитесь, что она загружена в UI (зелёный статус).

**Не меняйте** `LLM_LOCAL_BACKEND_URL`/`VLLM_BASE_URL` на `localhost`:
на Windows он резолвится в `::1`, а LM Studio слушает IPv4 (RULES §11.1).

---

## 4. Шаг 3. Прогнать evaluate_llm.py

Команда для Windows PowerShell (строки продолжения — обратный апостроф):

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_llm.py `
    --games 5 `
    --levels club `
    --model-name qwen-7b-tokenizer_gguf-v4 `
    --timeout-s 300 `
    --output docs/development/reports/LLM_EVAL_V4.md `
    --json-output docs/development/reports/llm_eval_results_v4.json
```

```bash
.venv/bin/python scripts/evaluate_llm.py \
    --games 5 \
    --levels club \
    --model-name qwen-7b-tokenizer_gguf-v4 \
    --timeout-s 300 \
    --output docs/development/reports/LLM_EVAL_V4.md \
    --json-output docs/development/reports/llm_eval_results_v4.json
```

Почему именно так:

| Флаг | Зачем |
| :--- | :--- |
| `--games 5` | Ровно столько партий в замороженном наборе; same-sample с v3 |
| `--levels club` | Baseline v3 снят только на `club`; другой набор уровней несопоставим |
| `--model-name qwen-7b-tokenizer_gguf-v4` | Метка версии в отчёте и JSON; API может вернуть базовое имя модели вместо имени адаптера |
| `--timeout-s 300` | Локальная 7B на длинном пакете фактов не всегда укладывается в production-таймаут 120 с |
| `--output …/LLM_EVAL_V4.md` | Не перезаписывать baseline-отчёт `LLM_EVAL.md` |
| `--json-output …v4.json` | Не перезаписывать baseline-JSON; вход для `compare_evals.py` |

Ожидаемый финал в консоли:

```text
Замороженный eval-набор: 5 партий из ...eval_games.json
Партий: 5, уровни: club
Модель (метка eval): qwen-7b-tokenizer_gguf-v4
Судья: deepseek, max_tokens=2048
...
--- ИТОГ ---
Модель: qwen-7b-tokenizer_gguf-v4
factual_accuracy: X.XX/10
usefulness: X.XX/10
style: X.XX/10
Средняя: X.XX/10
Объяснения: NN% (N/5)
CJK: 0.00% символов, комментариев с иероглифами: 0/5
```

Если партий оценено меньше 5 — прогон неполный; смотрите `error` в JSON и
раздел [Troubleshooting](#8-troubleshooting).

---

## 5. Шаг 4. Проверить, что оценивалась именно v4

Это обязательная проверка. Для `club` включён local-first роутер: если
локальная модель не ответила за `LLM_LOCAL_TIMEOUT_S`, запрос **молча**
уходит в DeepSeek, и eval измерит облако, а не v4.

Быстрая проверка JSON:

```powershell
.\.venv\Scripts\python.exe -c "import json; d=json.load(open('docs/development/reports/llm_eval_results_v4.json', encoding='utf-8')); print(len(d)); [print(i['game_id'], i['model'], i['model_name'], i['error'], i['scores']) for i in d]"
```

Требования:

- записей **5**, `error` у всех `null`;
- `model` — `lora-club-v4`, `qwen-7b-tokenizer_gguf-v4` или иной id вашей
  v4-модели, но **не** `deepseek-chat`;
- `model_name` — `qwen-7b-tokenizer_gguf-v4` (метка из `--model-name`);
- `scores` содержит все три шкалы.

Признаки fallback в DeepSeek:

- в JSON `model: deepseek-chat` хотя бы у одной партии;
- в логах backend `local_gpu_unavailable` / `llm_router_fallback`;
- в LM Studio нет запросов во время прогона.

Если fallback случился — увеличьте `LLM_LOCAL_TIMEOUT_S` (например, 300),
проверьте туннель/загрузку модели и **перезапустите прогон** с теми же
путями. Результаты с fallback для сравнения v3↔v4 недействительны.

---

## 6. Шаг 5. Сравнить с v3

```powershell
.\.venv\Scripts\python.exe scripts\compare_evals.py `
    docs/development/reports/llm_eval_results_v3.json `
    docs/development/reports/llm_eval_results_v4.json `
    --baseline-label v3 `
    --new-label v4 `
    --output docs/development/reports/LLM_EVAL_V3_VS_V4.md
```

```bash
.venv/bin/python scripts/compare_evals.py \
    docs/development/reports/llm_eval_results_v3.json \
    docs/development/reports/llm_eval_results_v4.json \
    --baseline-label v3 \
    --new-label v4 \
    --output docs/development/reports/LLM_EVAL_V3_VS_V4.md
```

Пример вывода:

```markdown
| Шкала | v3 (Baseline) | v4 (New) | Дельта |
| :--- | ---: | ---: | ---: |
| Факт. точность | 2.60 | 3.40 | +0.80 |
| Полезность | 3.40 | 4.00 | +0.60 |
| Стиль | 4.80 | 5.20 | +0.40 |
| **Средняя** | **3.60** | **4.20** | **+0.60** |
| Объяснения | 60.0% (3/5) | 80.0% (4/5) | +20.0 п.п. |
| CJK-символы | 0.00% (0/5) | 0.00% (0/5) | 0.00 п.п. |
```

Коды возврата: `0` — сравнение выведено; `1` — файл не найден/битый JSON/
запись не по схеме, либо `--strict-samples` при разных наборах партий.

Полезные опции:

- `--baseline-label` / `--new-label` — подписи колонок (по умолчанию версия
  берётся из `model_name`: `qwen-7b-tokenizer_gguf-v3` → `v3`);
- `--output PATH` — продублировать таблицу в Markdown-файл;
- `--strict-samples` — падать, если наборы `(game_id, level)` различаются
  (полезно в CI).

Скрипт сам предупреждает, если:

- наборы партий различаются — дельты межвыборочные, сравнивать нельзя;
- у обоих прогонов одинаковая метка модели — вероятно, забыли
  `--model-name` для v4.

---

## 7. Как читать результат

| Метрика | Что значит | Как считать «лучше» |
| :--- | :--- | :--- |
| Факт. точность, Полезность, Стиль | Среднее оценок судьи по шкале 1–10 | Выше — лучше |
| **Средняя** | Среднее трёх шкал (как в `LLM_EVAL.md`) | Выше — лучше |
| Объяснения | Доля комментариев с «потому что / так как / поэтому / because / since» | Выше — лучше |
| CJK-символы | Средняя по комментариям доля иероглифов | Ниже — лучше; цель 0% |

Важные оговорки:

- **Шум выборки.** 5 партий — мало: разница в 0.2–0.4 балла может быть
  шумом судьи. Решение о v4 принимайте вместе с прогоном на 30–50 партиях
  (T-024) и ручной проверкой комментариев.
- **CJK-метрика в сравнении** — средняя по комментариям доля `cjk_share`
  (в JSON нет длины текста). В `LLM_EVAL.md` печатается агрегатная доля
  «иероглифы / все символы»; на нуле обе совпадают, при разной длине
  комментариев числа могут слегка расходиться. Для обеих версий
  `compare_evals.py` считает одинаково, поэтому дельта корректна.
- **Объяснения** считаются по записям с текстом: сбой генерации не входит в
  знаменатель, сбой только судьи — входит.
- **Судья и промпт должны быть одинаковыми.** Baseline v3 снят с
  `LLM_JUDGE_MAX_TOKENS=2048`; не меняйте модель судьи и рубрику между
  прогонами.
- **Критерии приёмки** (из `GPU_TRAINING_GUIDE.md` §6.2): ориентир — не ниже
  baseline 4B (4.40), далее порог 7/10; KI-060 закрывается при 5/5
  комментариев без CJK. Для v3 официальный baseline — **3.60/10**.

---

## 8. Troubleshooting

| Симптом | Причина | Что делать |
| :--- | :--- | :--- |
| `Ошибка замороженного eval-набора: … не найден` | запуск не из корня репозитория / файл удалён | `cd` в корень; `git status tests/fixtures/eval_games.json` |
| `model "…" not found` (404) | `VLLM_MODEL`/basename адаптера не совпадает с сервером | взять точный id из `/v1/models`; для LoRA — `--lora-modules <basename>=<path>` |
| `llm_lora_adapter_unavailable` | `LORA_ADAPTER_PATH` не существует или нет `adapter_config.json` | проверить путь; каталог должен содержать `adapter_config.json` |
| В JSON `model: deepseek-chat` | local-first fallback: v4 не ответила за таймаут | поднять `LLM_LOCAL_TIMEOUT_S`, проверить LM Studio/туннель, перезапустить прогон |
| `judge: в ответе судьи нет JSON-объекта` | судья обрезан по токенам | `LLM_JUDGE_MAX_TOKENS=2048` (итерация 61), проверить `.env` |
| `Объяснения: —` / `CJK: —` в таблице | старый JSON без `has_explanation`/`cjk_*` | перегенерировать JSON текущим `evaluate_llm.py` |
| `наборы партий различаются` | v4 прогнан не на frozen set / другой `--games`/`--levels` | `--games 5 --levels club`, `--frozen-set` по умолчанию |
| `UnicodeEncodeError` в консоли | cp1251 в Windows-консоли | `compare_evals.py` сам переводит stdout/stderr в UTF-8; обновите скрипт |
| `Не удалось сохранить llm_evaluations` | нет PostgreSQL | `docker compose up -d postgres redis`; eval продолжит работу, но БД-оценки не запишутся |

---

## 9. Ссылки

- [`scripts/evaluate_llm.py`](../../scripts/evaluate_llm.py) — генерация и
  LLM-as-judge.
- [`scripts/compare_evals.py`](../../scripts/compare_evals.py) — таблица
  дельт.
- [`LLM_EVAL.md`](reports/LLM_EVAL.md) — baseline v3 (3.60/10).
- [`ITERATION-62.md`](reports/v1.5.0/ITERATION-62.md) — отчёт итерации, в
  которой добавлены скрипт и гайд.
- [`GPU_TRAINING_GUIDE.md`](GPU_TRAINING_GUIDE.md) — обучение v4 и критерии
  приёмки (≥4.40, затем 7/10).
- [`LORA_TRAINING.md`](reports/LORA_TRAINING.md) — подключение LoRA-адаптера.
- [`DEPLOYMENT_GPU_TUNNEL.md`](DEPLOYMENT_GPU_TUNNEL.md) — GPU через
  reverse SSH-туннель.
- [`RULES.md`](RULES.md) — правила проекта.
