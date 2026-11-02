# GPU-машина: перенос LoRA-обучения (T-080)

Практическая инструкция: как подготовить отдельную машину с NVIDIA GPU
(целевая — **RTX 3060 12 GB**), собрать датасет из PostgreSQL, обучить
LoRA-адаптер и вернуть его на основную машину. Рассчитана на новичка;
команды даны для **Linux** и **Windows PowerShell**. С итерации 43 —
экспорт адаптера в GGUF для LM Studio без vLLM (§10).

> Пайплайн и гиперпараметры подробно описаны в
> [`docs/development/reports/LORA_TRAINING.md`](reports/LORA_TRAINING.md).
> Здесь — именно «переезд» на GPU-машину и обратно. Схема датасета —
> `schema_version=2`; её пишет `scripts/generate_lora_dataset_from_db.py`
> (T-080) и читает `scripts/train_lora.py`.

## Оглавление

1. [Клонирование и venv](#1-клонирование-и-venv)
2. [PyTorch с CUDA](#2-pytorch-с-cuda)
3. [Опциональные пакеты обучения](#3-опциональные-пакеты-обучения)
4. [База данных](#4-база-данных)
5. [Датасет](#5-датасет)
6. [Обучение](#6-обучение)
7. [Перенос адаптера обратно](#7-перенос-адаптера-обратно)
8. [Проверка на основной машине](#8-проверка-на-основной-машине)
9. [Траблшутинг](#9-траблшутинг)
10. [Запуск на арендованном сервере (Ubuntu 22.04 + CUDA 12.6)](#10-запуск-на-арендованном-сервере-ubuntu-2204--cuda-126)
11. [Бесплатные GPU: Kaggle и Colab](#11-бесплатные-gpu-kaggle-и-colab)

---

## 1. Клонирование и venv

Нужны только Python 3.12, git и (для обучения) NVIDIA GPU с драйвером.
Node.js/pnpm для LoRA не требуются.

**Linux:**

```bash
git clone https://github.com/iilmchat/ai-chess-annotator.git
cd ai-chess-annotator
python3.12 -m venv .venv
source .venv/bin/activate
python -V                       # Python 3.12.x
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

**Windows PowerShell:**

```powershell
git clone https://github.com/iilmchat/ai-chess-annotator.git
cd ai-chess-annotator
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -V                       # Python 3.12.x
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

`pip install -e ".[dev]"` ставит зависимости приложения и тестов
(`pytest`, `ruff`, `mypy`), но **не** torch/transformers/peft — они
опциональны и ставятся на GPU-машине отдельно (см. §2–3).

Проверка, что репозиторий на месте и тесты не сломаны:

```bash
python -m pytest tests/unit/test_train_lora.py tests/unit/test_generate_lora_dataset.py -q
```

---

## 2. PyTorch с CUDA

Для Python 3.12 и CUDA 12.6 (RTX 3060/4090/A5000) ставьте **torch и
torchvision одной CUDA-сборкой**. Смешивать `cu121`/`cu126` нельзя: это
даёт `RuntimeError: Detected that PyTorch and torchvision were compiled
with different CUDA major versions`.

Официальный индекс:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

Если официальный индекс медленный/недоступен из РФ — зеркало SJTU:

```bash
pip install torch torchvision \
    --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cu126/
```

Через `uv` (быстрее и фиксирует окружение):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv pip install torch torchvision \
    --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cu126/
```

Если драйвер поддерживает только CUDA 12.1, замените `cu126` на `cu121`
**в обеих** библиотеках.

Проверка **обязательна** — без неё легко обучаться на CPU и не заметить
рассинхрон CUDA у torch/torchvision:

```bash
python -c "import torch, torchvision; print('torch:', torch.__version__); print('torchvision:', torchvision.__version__); print('cuda:', torch.version.cuda); print('available:', torch.cuda.is_available()); print('gpu:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'НЕТ GPU')"
```

Ожидаемый вывод на исправной машине:

```text
torch: 2.x.x+cu126
torchvision: 0.2x.x+cu126
cuda: 12.6
available: True
gpu: NVIDIA GeForce RTX 3060 (или RTX 4090 / A5000)
```

Если `available: False`:

- проверьте, что драйвер NVIDIA установлен и `nvidia-smi` показывает GPU;
- убедитесь, что не установилась CPU-сборка (`torch.__version__` без
  `+cu126`, например `+cpu`) — переустановите командой выше;
- проверьте, что `torch` и `torchvision` собраны с одной CUDA: обе строки
  должны оканчиваться на `+cu126` (или обе на `+cu121`);
- на Windows с WSL2 `nvidia-smi` должен работать **внутри** WSL; ставьте
  драйвер Windows (не Linux-драйвер внутри WSL);
- после обновления драйвера перезагрузите машину.

`python scripts/train_lora.py --check-env` позже покажет версию Python,
наличие пакетов и строку `cuda: да` / `device: NVIDIA GeForce RTX 3060`.

---

## 3. Опциональные пакеты обучения

`scripts/train_lora.py` импортирует (только при реальном обучении, не в
`--dry-run`):

- `torch` — уже установлен;
- `transformers` — `AutoModelForCausalLM`, `AutoTokenizer`,
  `BitsAndBytesConfig`, `Trainer`, `TrainingArguments`;
- `peft` — `LoraConfig`, `get_peft_model`, `prepare_model_for_kbit_training`;
- `datasets` — `Dataset`;
- `accelerate` — требование `Trainer`;
- `bitsandbytes` — только для `--load-in-4bit` (QLoRA).

```bash
pip install transformers peft datasets accelerate bitsandbytes
```

Проект **не фиксирует** версии этих пакетов в `pyproject.toml`: они
опциональны и быстро меняются. Обязательно сохраните окружение прогона,
иначе адаптер может не загрузиться другой версией vLLM:

```bash
pip freeze > requirements-gpu.txt
```

Проверка стека:

```bash
python scripts/train_lora.py --check-env
```

**Windows и bitsandbytes.** Нативный Windows-биндинг `bitsandbytes`
собирается не всегда. Для QLoRA надёжнее **WSL2 (Ubuntu)** или Linux:
внутри WSL повторите §1–2. Без 4-bit (`--no-4bit`) на 12 GB обучать
4B-модель не стоит — см. §6.

---

## 4. База данных

Датасет собирается из таблиц `games`/`commentaries`/`edits`/`moves`.
На GPU-машине нужна та же схема и данные. Два рабочих варианта:

- **A (проще):** перенести уже готовый JSONL с основной машины
  (`data/lora_club_v1.jsonl`) и пропустить сборку из БД — тогда §4 нужен
  только для `python scripts/train_lora.py --dry-run`;
- **B (полный, по T-080):** перенести дамп PostgreSQL и собрать датасет
  на GPU-машине скриптом из §5.

### 4.1. PostgreSQL и Redis в Docker

```bash
docker compose up -d postgres redis
docker compose ps
```

`docker-compose.yml` публикует PostgreSQL на
`127.0.0.1:5434` (жёстко) и Redis на `127.0.0.1:6380` (порт можно
переопределить `REDIS_PORT`). **`POSTGRES_PORT` в `.env`/окружении — это
порт, по которому к БД подключается приложение, а не проброс compose.**
Если 5434 занят, измените mapping в `docker-compose.yml` и поставьте тот же
`POSTGRES_PORT` в окружении.

PowerShell-пример окружения для текущей сессии (переменные читает
`pydantic-settings`, их же видят `alembic` и скрипты):

```powershell
$env:POSTGRES_HOST = "127.0.0.1"
$env:POSTGRES_PORT = "5434"
$env:POSTGRES_DB = "chessannotator"
$env:POSTGRES_USER = "chess"
$env:POSTGRES_PASSWORD = "chess_dev_password"
$env:REDIS_URL = "redis://127.0.0.1:6380/0"
```

Linux — то же через `export`, либо скопируйте `.env.example` в `.env`:

```bash
cp .env.example .env
# Windows: Copy-Item .env.example .env
```

`.env` в git не коммитится. Не меняйте `POSTGRES_HOST=127.0.0.1` на
`localhost`: на Windows `localhost` резолвится в `::1`, а compose слушает
IPv4-loopback.

### 4.2. Миграции и промпты

```bash
alembic upgrade head
```

Промпты уровней (`beginner`/`club`/`gm`) засеиваются автоматически при
старте API (`app.main` → `seed_prompts`, идемпотентно). Отдельная команда
не нужна, но один запуск API полезен и для проверки БД:

```bash
uvicorn app.main:app --port 8000
# дождитесь старта, затем Ctrl+C
```

Для сборки датасета промпты читаются из `app/prompts/*.txt`
(`load_system_prompt`), поэтому даже без запуска API §5 работает.

### 4.3. Перенос дампа с основной машины (вариант B)

Сначала остановите API/воркер, которые пишут в БД. На **основной** машине
из корня репозитория:

```bash
docker compose exec -T postgres pg_dump -U chess -d chessannotator -Fc -f /tmp/chess.dump
docker cp "$(docker compose ps -q postgres):/tmp/chess.dump" ./chess.dump
```

PowerShell:

```powershell
docker compose exec -T postgres pg_dump -U chess -d chessannotator -Fc -f /tmp/chess.dump
docker cp "$(docker compose ps -q postgres):/tmp/chess.dump" .\chess.dump
```

Скопируйте `chess.dump` на GPU-машину (scp/USB) и восстановите **после**
`docker compose up -d postgres` и `alembic upgrade head`:

```bash
docker cp ./chess.dump "$(docker compose ps -q postgres):/tmp/chess.dump"
docker compose exec -T postgres pg_restore -U chess -d chessannotator \
    --clean --if-exists --no-owner /tmp/chess.dump
```

Проверка, что правки на месте:

```bash
docker compose exec -T postgres psql -U chess -d chessannotator \
    -c "select count(*) as edits from edits;"
```

---

## 5. Датасет

Скрипт `scripts/generate_lora_dataset_from_db.py` собирает **ровно тот же**
v2-формат, что `scripts/export_dataset.py` (общие `build_fact_sheet` и
`record_from_edit`): `system` → `user` (пакет фактов) → `assistant` (правка
редактора). `train_lora.py` читает файл без конвертации.

Сначала «сухой» прогон — он ничего не пишет и показывает, сколько записей
получится и почему часть кандидатов пропущена:

```bash
python scripts/generate_lora_dataset_from_db.py \
    --dry-run --limit 500 --output data/lora_club_v1.jsonl
```

Затем реальная сборка (для club-адаптера полезно ограничить уровень):

```bash
python scripts/generate_lora_dataset_from_db.py \
    --limit 500 --level club --output data/lora_club_v1.jsonl
```

Без `--level` берутся все уровни (`all`). Другие полезные флаги:

| Флаг | Смысл |
| :--- | :--- |
| `--limit N` | целевое число записей (по умолчанию 500) |
| `--level beginner\|club\|gm\|all` | фильтр по `commentaries.level` |
| `--min-words N` | отбросить слишком короткие правки (по умолчанию 0) |
| `--seed N` | детерминированная выборка (без него — сначала свежие правки) |
| `--dry-run` | посчитать статистику, файл не писать |

Порядок стабильный: `edits.created_at DESC, edits.id DESC`. Из БД читается
окно последних `limit × 5` кандидатов (не более 20 000 строк), затем
`--seed` перемешивает выборку, а `--limit` ограничивает её размер.

**Что считается кандидатом.** Только комментарии, у которых есть правка
(`edits`), — иначе нет `assistant`-ответа. Записи без партии, с битым PGN,
без анализа ходов (`moves`) или с неизвестным уровнем **пропускаются** и
считаются в сводке: `no_game`, `invalid_pgn`, `no_analysis`,
`invalid_level`, `min_words`. Скрипт не падает на одной плохой записи.

Пример сводки:

```text
Датасет LoRA: schema_version=2
Кандидатов после фильтров: 780
Отобрано к сборке: 500 (limit=500)
Экспортировано записей: 468
Пропущено записей: 32
  нет анализа ходов (moves пусты): 30
  PGN не разобран (InvalidPgnError): 2
Распределение по уровням: club=468
Файл: data/lora_club_v1.jsonl
```

**Exit codes:** `0` — успех (есть хотя бы одна запись), `2` — неверные
аргументы, пустая БД/нет партий с комментариями или ошибка подключения.
Если записей `0`, проверьте, что в БД есть `edits` и для партий вызывался
`/v1/analyze` (без `moves` пакет фактов не собрать).

Проверка файла:

```bash
wc -l data/lora_club_v1.jsonl
python scripts/train_lora.py --dry-run --dataset data/lora_club_v1.jsonl
```

PowerShell:

```powershell
(Get-Content data/lora_club_v1.jsonl).Count
python scripts/train_lora.py --dry-run --dataset data/lora_club_v1.jsonl
```

`--dry-run` у `train_lora.py` печатает число записей, распределение по
уровням и `schema_version`, оценку токенов и предупреждения. В норме:
`Распределение по schema_version: 2=N` и **нет** предупреждения про
`schema_version=1`/«user не похож на пакет фактов».

### 5.1. Внешний датасет: bishop-chess-dataset (T-087)

Если правок редакторов мало, данные можно добрать из внешнего
аннотированного PGN-корпуса. Файл
`data/external_datasets/bishop_train.jsonl` (15 363 записи, ~60 МБ) в git
не коммитится — скачайте его отдельно (`pkloats/bishop-chess-dataset`).
В каждой строке JSONL поле `text` содержит PGN с комментариями в
`{ ... }`.

Конвертер `scripts/convert_external_datasets.py` собирает из него
`schema_version=2`: `system` — промпт уровня `club`, `user` — пакет
фактов `build_fact_package(..., level="club")`, `assistant` — склеенные
комментарии PGN. Служебные теги `[%eval ...]`/`[%clk ...]` вырезаются,
`assistant` ограничивается 2000 символами. Анализа движка у внешнего PGN
нет, поэтому в пакете фактов нет ключевых моментов и оценок — только ходы
и метаданные; пакет прямо сообщает, что оценок движка в нём нет.

Сначала сухой прогон (файл не пишется):

```bash
python scripts/convert_external_datasets.py --dry-run --limit 100
```

Реальная сборка (по умолчанию `--input data/external_datasets/bishop_train.jsonl`,
`--output data/lora_gold_v1.jsonl`, `--limit 5000`):

```bash
python scripts/convert_external_datasets.py --limit 5000
```

PowerShell — те же команды:

```powershell
python scripts/convert_external_datasets.py --dry-run --limit 100
python scripts/convert_external_datasets.py --limit 5000
```

`--limit` — максимум записей в **выходном** файле; чтение идёт, пока лимит
не набран. Пропуски (`битый PGN`, `нет комментариев`, `комментарии короче
20 символов`, `битый JSON`, пустой `text`) считаются в сводке и не
прерывают прогон. Пример:

```text
Конвертация внешнего датасета: schema_version=2
Прочитано записей: 12693
Сконвертировано записей: 5000
Пропущено записей: 7693
  битый PGN: 7321
  нет комментариев: 372
Средняя длина assistant: 1744 символов (максимум 2000)
Файл: data/lora_gold_v1.jsonl
```

Проверка перед обучением:

```bash
python scripts/train_lora.py --dry-run --dataset data/lora_gold_v1.jsonl
```

Ожидается `Распределение по schema_version: 2=5000`, `club=5000` и
отсутствие предупреждения «user не похож на пакет фактов». Файл можно
обучать отдельно (`--dataset data/lora_gold_v1.jsonl`) или объединить с
`data/lora_club_v1.jsonl` перед запуском. Внешние комментарии не
проверены движком и могут содержать опечатки — это данные для стиля, а не
эталон фактов; при смешивании ориентируйтесь на
`metadata.source="pkloats/bishop-chess-dataset"`.

---

## 6. Обучение

Сначала окружение и датасет:

```bash
python scripts/train_lora.py --check-env
python scripts/train_lora.py --dry-run --dataset data/lora_club_v1.jsonl
```

Команда обучения (все флаги реальные, из `scripts/train_lora.py`):

```bash
python scripts/train_lora.py \
    --dataset data/lora_club_v1.jsonl \
    --model Qwen/Qwen3-4B-Instruct-2507 \
    --output-dir models/lora-club-v1 \
    --epochs 3 \
    --batch-size 1 \
    --grad-accum 8 \
    --lr 2e-4 \
    --lora-r 16 \
    --lora-alpha 32 \
    --lora-dropout 0.05 \
    --max-length 2048 \
    --load-in-4bit \
    --gradient-checkpointing \
    --seed 42
```

Что это значит:

- `--load-in-4bit` — QLoRA: базовая 4B-модель в NF4, обучаются только
  LoRA-веса. Для 12 GB это основной режим; `--no-4bit` (bf16) на 12 GB
  не рекомендуется — по оценке из `LORA_TRAINING.md` нужен больший объём.
- `--batch-size 1 --grad-accum 8` — effective batch 8. На 12 GB начинайте
  именно с этого.
- `--gradient-checkpointing` включён по умолчанию; флаг указан явно, чтобы
  режим был виден в истории команд. Он экономит VRAM ценой ~20–30 %
  скорости.
- `--max-length 2048` — system + пакет фактов + ответ. Если `--dry-run`
  показывает среднюю оценку близко к 2048, поднимите до 4096 и столько же
  ставьте vLLM на инференсе; на 12 GB это может не поместиться.
- `--epochs 3`, `--lr 2e-4`, `--lora-r 16`, `--lora-alpha 32` — дефолты
  проекта для первого прогона.

### 6.1. Обучение v0.2.0 (DeepSeek Teacher)

Стратегия v0.2.0 меняет два параметра пайплайна:

* **данные** генерирует сильный учитель DeepSeek, а не локальная 4B-модель
  (иначе датасет дистиллирует её галлюцинации — KI-030);
* **обучение** идёт 1 эпоху с LR `1e-4`, чтобы не переобучиться на узком
  наборе и не усилить языковой дрейф 7B (KI-060).

Сначала соберите датасет v2 на машине, где задан
`DEEPSEEK_API_KEY` (ключ читается из `Settings.deepseek_api_key`):

```bash
# проверка пайплайна без вызова API
python scripts/build_gold_dataset_v2.py --dry-run --limit 20

# реальная генерация (по умолчанию data/gold_dataset_v2.jsonl)
python scripts/build_gold_dataset_v2.py --limit 1000

# режим 2: изолированные позиции (FEN, evaluation, best_move)
python scripts/build_gold_dataset_v2.py \
    --positions data-chess/positions.jsonl --dry-run --limit 20
python scripts/build_gold_dataset_v2.py \
    --positions data-chess/positions.jsonl --limit 1000
```

Скрипт `scripts/build_gold_dataset_v2.py` берёт PGN из `data/gold_pgns/`
(или FEN из `--positions`), собирает пакет фактов (`build_fact_package` с
`max_tokens=2048` для PGN; для позиций — только текущая позиция и оценка,
без истории ходов), отправляет системный промпт уровня `club` в
`deepseek-chat` и требует **только текст комментария**. Каждый ответ
проверяется валидатором: нелегальные SAN-ходы дают retry, китайские
иероглифы — один retry «отвечай только на русском», затем предложения с
иероглифами вырезаются. Выход — `schema_version=2`
(`system → user → assistant`) в обоих режимах.

Проверка датасета перед обучением:

```bash
python scripts/train_lora.py --dry-run --dataset data/gold_dataset_v2.jsonl
```

Обучение v0.2.0 (отличия от §6 — датасет, `--lr`, `--epochs` и
`--output-dir`):

```bash
python scripts/train_lora.py \
    --dataset data/gold_dataset_v2.jsonl \
    --model Qwen/Qwen2.5-7B-Instruct \
    --output-dir models/lora-club-v2 \
    --epochs 1 \
    --lr 1e-4 \
    --batch-size 1 \
    --grad-accum 8 \
    --max-length 2048 \
    --load-in-4bit \
    --gradient-checkpointing \
    --seed 42
```

`--lr` и `--epochs` — обычные CLI-флаги: их можно переопределить, не правя
код. Дефолты (`2e-4`, 3 эпохи) оставлены для воспроизводимости прогонов
v0.1.x. Для 4B-базы замените `--model` на `Qwen/Qwen3-4B-Instruct-2507`
и `--output-dir` на `models/lora-club-v2-4b`. После обучения прогоните тот
же 5-партийный eval (`scripts/evaluate_llm.py`); ориентир — не ниже
baseline 4B (4.40/10) и 5/5 комментариев без CJK.

**Логи лосса.** По умолчанию `--report_to auto`: если установлен
`tensorboard`, логи пишутся в `models/lora-club-v1/runs` (смотреть
`tensorboard --logdir models/lora-club-v1/runs`); иначе используется
`wandb`, если он есть; если нет ни одного — остаётся консольный вывод
Trainer. Явно: `--report_to tensorboard`, `--report_to wandb` или
`--report_to none`. Прерванное обучение продолжается флагом
`--resume_from_checkpoint models/lora-club-v1/checkpoint-N` (см. §10.4).

**VRAM на RTX 3060 12 GB.** QLoRA 4B + `batch=1` + `max_length=2048` +
gradient checkpointing обычно помещается. Следите за `nvidia-smi` в
отдельном терминале. Если CUDA OOM:

1. `--max-length 1024` (и `--grad-accum 16`, чтобы сохранить effective
   batch 16);
2. оставьте `--batch-size 1` и `--load-in-4bit`;
3. закройте игры/браузер/другие процессы, занимающие VRAM;
4. только потом рассматривайте GPU с большей памятью (4090/A100).

**bf16/fp16.** Скрипт включает `bf16` только если
`torch.cuda.is_bf16_supported()`; `fp16=True` он не выставляет никогда.
На Ampere (RTX 3060) bf16 обычно поддерживается — проверьте строку
`bf16` в `--check-env`. Если bf16 нет, обучение всё равно пойдёт, но
эффективность ниже: держите 4-bit и уменьшайте `--max-length`.

**Куда сохраняется адаптер.** По умолчанию `--output-dir
models/lora-club-v1`. После `save_pretrained` там будут:

```text
models/lora-club-v1/
├── adapter_config.json          # r=16, alpha=32, q_proj/v_proj, base model
├── adapter_model.safetensors    # веса LoRA (десятки МБ, не ~8 ГБ базы)
├── README.md
├── tokenizer_config.json
├── tokenizer.json
└── special_tokens_map.json
```

`adapter_config.json` — маркер готового адаптера для `Settings.lora_model`.
Каталог без него считается недокачанным.

Проверка:

```bash
ls -lh models/lora-club-v1
```

```powershell
Get-ChildItem models/lora-club-v1
```

---

## 7. Перенос адаптера обратно

### 7.1. Архив (самый простой путь)

Linux:

```bash
tar -czf lora-club-v1.tar.gz models/lora-club-v1
tar -tzf lora-club-v1.tar.gz | head
```

Windows PowerShell:

```powershell
Compress-Archive -Path models/lora-club-v1 -DestinationPath lora-club-v1.zip
Get-ChildItem lora-club-v1.zip
```

Скопируйте архив на основную машину (scp/USB) и распакуйте в
`models/lora-club-v1`. `models/` в `.gitignore` — адаптер не попадёт в git
случайно.

### 7.2. Git LFS (если адаптер нужно версионировать)

Адаптер маленький по сравнению с базовой моделью, но `models/` уже
игнорируется, поэтому LFS-трекинг сам по себе ничего не добавит в индекс.

```bash
git lfs install
git lfs track "models/**/*.safetensors"
git add .gitattributes
git add -f models/lora-club-v1/adapter_config.json
git add -f models/lora-club-v1/adapter_model.safetensors
git commit -m "chore(lora): club adapter v1"
```

`git add -f` обязателен: иначе `models/` из `.gitignore` не пустит файлы.
Не коммитьте чекпоинты эпох и `optimizer.pt` — только `adapter_config.json`
и `adapter_model.safetensors` (плюс токенизатор, если он нужен для
воспроизводимости). Для одноразового переноса архив из §7.1 проще и не
тратит квоту LFS.

---

## 8. Проверка на основной машине

1. Положите адаптер в `models/lora-club-v1` (в корне репозитория).
2. В `.env` укажите:

   ```dotenv
   LORA_ADAPTER_PATH=models/lora-club-v1
   ```

3. Запустите vLLM с тем же basename, что в пути:

   ```bash
   vllm serve Qwen/Qwen3-4B-Instruct-2507 \
       --lora-modules lora-club-v1=models/lora-club-v1 \
       --max-model-len 8192 \
       --port 8034
   ```

   LM Studio динамические LoRA через OpenAI-совместимый API не умеет —
   нужен именно vLLM. Альтернатива для LM Studio — смерджить адаптер и
   конвертировать в GGUF (§10.5–10.6), тогда vLLM не нужен.

4. Убедитесь, что адаптер виден:

   ```bash
   curl -s http://127.0.0.1:8034/v1/models | python -m json.tool
   # в списке должно быть "lora-club-v1"
   ```

5. Запустите backend (`uvicorn app.main:app --reload`) и сделайте
   smoke-генерацию через `/v1/commentary`. Нужен подтверждённый
   пользователь и партия с анализом ходов.

   PowerShell:

   ```powershell
   $login = Invoke-RestMethod -Method Post `
       -Uri http://127.0.0.1:8000/auth/login `
       -ContentType "application/json" `
       -Body '{"email":"user@example.com","password":"strong-password"}'
   $headers = @{ Authorization = "Bearer $($login.access_token)" }

   $analysis = Invoke-RestMethod -Method Post `
       -Uri http://127.0.0.1:8000/v1/analyze `
       -Headers $headers -ContentType "application/json" `
       -Body '{"pgn":"1. e4 e5 2. Nf3 Nc6","level":"club"}'

   $body = @{ analysis_id = [int]$analysis.analysis_id; level = "club" } |
       ConvertTo-Json
   Invoke-RestMethod -Method Post `
       -Uri http://127.0.0.1:8000/v1/commentary `
       -Headers $headers -ContentType "application/json" -Body $body
   ```

   `bash`/`curl`-эквивалент:

   ```bash
   TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
       -H "Content-Type: application/json" \
       -d '{"email":"user@example.com","password":"strong-password"}' \
       | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

   ANALYSIS_ID=$(curl -s -X POST http://127.0.0.1:8000/v1/analyze \
       -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
       -d '{"pgn":"1. e4 e5 2. Nf3 Nc6","level":"club"}' \
       | python -c "import sys,json; print(json.load(sys.stdin)['analysis_id'])")

   curl -s -X POST http://127.0.0.1:8000/v1/commentary \
       -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
       -d "{\"analysis_id\": $ANALYSIS_ID, \"level\": \"club\"}" \
       | python -m json.tool
   ```

   Признак успеха: HTTP 200, `validated: true`, непустой `text`; поле
   `model` должно быть `lora-club-v1` (имя из `--lora-modules`), если vLLM
   возвращает его в ответе. Если `model` — базовая модель, проверьте
   `LORA_ADAPTER_PATH` и лог `llm_lora_adapter_enabled`.

6. Откат: уберите `LORA_ADAPTER_PATH` из `.env` и перезапустите backend —
   приложение вернётся к `VLLM_MODEL` без изменения кода.

---

## 9. Траблшутинг

| Симптом | Причина | Решение |
| :--- | :--- | :--- |
| `CUDA OOM` / `out of memory` | не хватило VRAM | `--batch-size 1`, `--max-length 1024`, `--grad-accum 16`, `--load-in-4bit`, закрыть другие GPU-приложения; при необходимости GPU с большей памятью |
| `CUDA driver version is insufficient` | старый драйвер NVIDIA | обновить драйвер, перезагрузиться; проверить `nvidia-smi` |
| `torch.cuda.is_available()` → `False` | CPU-сборка torch, нет драйвера, WSL без GPU | переустановить `torch` и `torchvision` с `--index-url .../cu126` (или обе с `cu121`); проверить `nvidia-smi` в той же среде (в WSL — внутри WSL); перезагрузить |
| `Нет зависимостей для обучения LoRA` (exit 1) | не установлены transformers/peft/datasets/accelerate | `pip install transformers peft datasets accelerate bitsandbytes` |
| `CUDA/GPU не найдена` (exit 1) | torch не видит GPU | см. строки про `torch.cuda.is_available()` |
| `bitsandbytes` не ставится на Windows | нет нативной сборки | WSL2/Ubuntu (рекомендуется) или Linux-машина |
| `prompt занимает N токенов…` | `--max-length` меньше prompt | увеличить `--max-length` (до 4096) или сократить пакет фактов |
| PostgreSQL: `port already allocated` | 5434 занят | `docker compose ps`, `netstat -ano \| findstr :5434`; изменить mapping в `docker-compose.yml` и `POSTGRES_PORT` |
| `no games with commentaries found` / exit 2 | нет `edits` или не выполнен `/v1/analyze` | проверить `select count(*) from edits;`, вызвать `/v1/analyze` для партий, повторить `--dry-run` |
| `Пропущено записей: N` | нет анализа ходов/битый PGN | посмотреть причины в сводке; выполнить `/v1/analyze`; проверить PGN |
| `schema_version=2, но user не похож на пакет фактов` | файл собран не тем скриптом | пересобрать `scripts/generate_lora_dataset_from_db.py` (v2) |
| `Ни одной записи не сконвертировано` (exit 2) | нет входного JSONL, пустое поле `text` или все PGN битые | проверить путь к `bishop_train.jsonl`; `--dry-run` покажет причины пропусков |
| vLLM: `model "lora-club-v1" not found` | basename не совпал с `--lora-modules` | `--lora-modules lora-club-v1=<точный путь>`; проверить `ls models/lora-club-v1` |
| `llm_lora_adapter_unavailable` в логах | нет `adapter_config.json`/путь неверный | проверить `LORA_ADAPTER_PATH`; каталог должен содержать `adapter_config.json` |
| Скачивание Qwen зависает | нет доступа к Hugging Face | `HF_TOKEN`, предварительно скачать модель; проверить сеть |
| `llama.cpp не найден… GGUF не создан` | нет `convert_hf_to_gguf.py`/`convert.py` | установить llama.cpp (см. §10.5) и повторить `export_to_gguf.py --llama_cpp_dir ~/llama.cpp`; HF-модель уже сохранена |
| `В … нет adapter_config.json` | `--adapter_path` указывает не на LoRA | указать каталог из `--output-dir` обучения (там `adapter_config.json` и `adapter_model.safetensors`) |
| `tensorboard не установлен` / логов нет | `--report_to tensorboard`, но пакета нет | `pip install tensorboard` или `--report_to none` |
| `Чекпоинт не найден` | неверный `--resume_from_checkpoint` | `ls models/.../checkpoint-*` и указать существующий каталог |
| `embedding(): argument 'indices' must be Tensor, not list` | старый `collate_batch` возвращал списки | обновить репозиторий: с итерации 44 `collate_batch` возвращает `torch.tensor` |
| `Detected that PyTorch and torchvision were compiled with different CUDA major versions` | torch и torchvision из разных индексов | переустановить обе одной командой с `cu126` (или обе с `cu121`), см. §2 |
| `TypeError: __init__() got an unexpected keyword argument 'logging_dir'` / `'warmup_ratio'` | старый код с аргументами, удалёнными в transformers 4.44+ | обновить репозиторий (итерация 44 убрала эти аргументы) |
| `skipped_too_long: N` | записи не помещаются в `--max-length` | это не ошибка: увеличьте `--max-length` или оставьте пропуск; `--no-skip-too-long` вернёт падение |
| `hf: command not found` | старый `huggingface_hub` без CLI `hf` | `pip install -U "huggingface_hub[cli]"`; `huggingface-cli download` устарел |
| Скачивание Qwen зависает | нет доступа к Hugging Face | `export HF_ENDPOINT=https://hf-mirror.com`, `hf download ...`, `HF_TOKEN` для gated-моделей |

---

## 10. Запуск на арендованном сервере (Ubuntu 22.04 + CUDA 12.6)

Сценарий для арендованной машины с **RTX 4090 / RTX A5000 24 GB** (на
3060 12 GB осторожнее с batch size, см. §6). Все команды — в SSH-сессии
на сервере.

### 10.1. Зависимости

Проверьте драйвер и CUDA:

```bash
nvidia-smi
```

Если Python 3.12 нет в системе, поставьте его (Ubuntu 22.04):

```bash
sudo apt-get install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev git
```

Клонирование и venv (вариант `pip`):

```bash
git clone https://github.com/iilmchat/ai-chess-annotator.git
cd ai-chess-annotator
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Либо через `uv` (быстрее, меньше конфликтов версий):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/iilmchat/ai-chess-annotator.git
cd ai-chess-annotator
uv venv --python 3.12 .venv
source .venv/bin/activate
```

PyTorch CUDA 12.6 — **torch и torchvision одной сборкой**:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
# если официальный индекс недоступен из РФ:
pip install torch torchvision \
    --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cu126/
pip install peft transformers bitsandbytes accelerate datasets tensorboard
pip install -e ".[dev]"
```

С `uv` те же установки: `uv pip install ...` с тем же `--index-url`.
Не смешивайте `cu121`/`cu126`: `torch` и `torchvision` должны быть
собраны с одной CUDA.

Проверка **обязательна**:

```bash
python -c "import torch, torchvision; print(torch.__version__, torchvision.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
python scripts/train_lora.py --check-env
```

Ожидается `+cu126` у обеих библиотек, `True`, `NVIDIA GeForce RTX 4090`
(или `A5000`), `cuda: да`, `bf16: да` (Ampere/Ada bf16 поддерживают).

#### Скачивание базовой модели из РФ (HF-зеркало)

Официальный `huggingface.co` из РФ часто недоступен. Используйте зеркало
`hf-mirror.com` и новый CLI `hf` (`huggingface-cli download` устарел):

```bash
pip install -U "huggingface_hub[cli]"
export HF_ENDPOINT=https://hf-mirror.com
hf download Qwen/Qwen2.5-7B-Instruct \
    --local-dir models/base/Qwen2.5-7B-Instruct
```

PowerShell:

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
hf download Qwen/Qwen2.5-7B-Instruct `
    --local-dir models/base/Qwen2.5-7B-Instruct
```

Через Python API (если `hf` не установился):

```bash
export HF_ENDPOINT=https://hf-mirror.com
python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen2.5-7B-Instruct', local_dir='models/base/Qwen2.5-7B-Instruct')"
```

`HF_ENDPOINT` можно добавить в `~/.bashrc`. Для gated-моделей нужен
`HF_TOKEN` (получить на официальном сайте), зеркало токен не выдаёт.
Скачанную модель передавайте в обучение локальным путём
(`--model models/base/Qwen2.5-7B-Instruct`) — тогда сеть при старте не
нужна.

### 10.2. Датасет

Скопируйте готовый JSONL с основной машины (или соберите на сервере,
если перенесли БД):

```bash
# на основной машине
scp data/lora_gold_v1.jsonl root@<server-ip>:/workspace/ai-chess-annotator/data/
```

На сервере проверьте файл:

```bash
python scripts/train_lora.py --dry-run --dataset data/lora_gold_v1.jsonl
```

Должно быть `schema_version: 2=5000`, `club=5000`, без предупреждений о
v1/«user не похож на пакет фактов».

### 10.3. Обучение на RTX 4090

```bash
python scripts/train_lora.py \
    --dataset data/lora_gold_v1.jsonl \
    --model Qwen/Qwen2.5-7B-Instruct \
    --output-dir models/lora-club-v2 \
    --epochs 3 \
    --batch-size 4 \
    --grad-accum 4 \
    --lr 2e-4 \
    --lora-r 16 \
    --lora-alpha 32 \
    --lora-dropout 0.05 \
    --max-length 2048 \
    --load-in-4bit \
    --gradient-checkpointing \
    --report_to tensorboard \
    --seed 42
```

`--batch-size 4 --grad-accum 4` — effective batch 16; на 24 GB это
рабочая отправная точка для 7B в NF4 с gradient checkpointing. Если
`CUDA OOM`: `--batch-size 2 --grad-accum 8`, затем `--max-length 1024`.
Чекпоинты сохраняются по эпохам в `models/lora-club-v2/checkpoint-*`.

`--skip-too-long` включён по умолчанию: записи, которые не помещаются в
`--max-length`, пропускаются со счётчиком `skipped_too_long`, обучение
продолжается. Чтобы падать на первой такой записи, добавьте
`--no-skip-too-long`.

Если базовая модель скачана через зеркало, замените `--model
Qwen/Qwen2.5-7B-Instruct` на `--model models/base/Qwen2.5-7B-Instruct`.

TensorBoard (по умолчанию логи пишутся в `output_dir/runs`; запускать в
отдельном терминале или через SSH-туннель):

```bash
tensorboard --logdir models/lora-club-v2/runs --port 6006 --bind_all
```

С локальной машины:

```bash
ssh -L 6006:127.0.0.1:6006 root@<server-ip>
# открыть http://127.0.0.1:6006
```

`--report_to auto` (по умолчанию) сам выберет TensorBoard, если он
установлен; `--report_to wandb` — если нужен Weights & Biases (потребуется
`wandb login`); `--report_to none` — только консоль.

### 10.4. Продолжение после обрыва аренды

Найдите последний чекпоинт:

```bash
ls -d models/lora-club-v2/checkpoint-*
```

Повторите **ту же** команду обучения, добавив путь:

```bash
python scripts/train_lora.py \
    --dataset data/lora_gold_v1.jsonl \
    --model Qwen/Qwen2.5-7B-Instruct \
    --output-dir models/lora-club-v2 \
    --epochs 3 \
    --batch-size 4 \
    --grad-accum 4 \
    --max-length 2048 \
    --load-in-4bit \
    --gradient-checkpointing \
    --report_to tensorboard \
    --resume_from_checkpoint models/lora-club-v2/checkpoint-500
```

`--epochs` — общее число эпох, а не «добавить ещё»; Trainer восстановит
оптимизатор, scheduler и RNG. Не меняйте `--output-dir` и гиперпараметры
без необходимости: иначе продолжение будет несогласованным. Если путь
неверный, скрипт завершится с сообщением «Чекпоинт не найден», не начиная
обучение заново.

### 10.5. Экспорт в GGUF

LM Studio не умеет динамические LoRA, поэтому адаптер нужно смерджить и
конвертировать. Установите llama.cpp (нужен Python-конвертер из репозитория):

```bash
sudo apt-get install -y cmake build-essential
git clone https://github.com/ggerganov/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build
cmake --build build --config Release -j
pip install -r requirements.txt
cd ~/ai-chess-annotator
```

Экспорт (по умолчанию merge идёт на CPU — для 7B нужно ~16 ГБ RAM;
`--device cuda` быстрее, если хватает VRAM):

```bash
python scripts/export_to_gguf.py \
    --base_model Qwen/Qwen2.5-7B-Instruct \
    --adapter_path models/lora-club-v2 \
    --output_dir models/qwen2.5-7b-chess-gguf \
    --llama_cpp_dir ~/llama.cpp \
    --gguf_outtype f16
```

Результат:

```text
models/qwen2.5-7b-chess-gguf/
├── config.json
├── model.safetensors          # смердженная HF-модель
├── tokenizer.json
├── tokenizer_config.json
└── model.gguf                 # если llama.cpp найден и конвертация удалась
```

Если llama.cpp не найден или конвертер упал, скрипт печатает warning,
**не удаляет** HF-модель и завершается с кодом 0. Можно установить
llama.cpp и повторить только конвертацию (обучение не нужно). Для LM Studio
на слабой машине имеет смысл квантовать f16 в Q4_K_M:

```bash
~/llama.cpp/build/bin/llama-quantize \
    models/qwen2.5-7b-chess-gguf/model.gguf \
    models/qwen2.5-7b-chess-gguf/model-Q4_K_M.gguf Q4_K_M
```

### 10.6. Скачать GGUF и загрузить в LM Studio

С сервера на основную машину:

```bash
scp root@<server-ip>:/workspace/ai-chess-annotator/models/qwen2.5-7b-chess-gguf/model-Q4_K_M.gguf .
```

(или `rsync -avP` для докачки при обрыве).

В LM Studio:

1. **My Models → Import** (или скопируйте `.gguf` в каталог моделей
   LM Studio, например `~/.lmstudio/models/local/chess/`).
2. Выберите файл, при необходимости укажите context length `8192` и
   загрузите модель.
3. **Developer → Start Server** (OpenAI-совместимый сервер, по умолчанию
   `http://127.0.0.1:1234`).
4. Проверьте:

   ```bash
   curl -s http://127.0.0.1:1234/v1/models
   ```

5. В `.env` приложения укажите LM Studio вместо vLLM:

   ```dotenv
   VLLM_BASE_URL=http://127.0.0.1:1234/v1
   VLLM_MODEL=<model id из /v1/models>
   LORA_ADAPTER_PATH=
   ```

   Перезапустите backend. Теперь `/v1/commentary` использует смердженный
   GGUF через LM Studio, без vLLM и без отдельного LoRA-адаптера.

---

## 11. Бесплатные GPU: Kaggle и Colab

Бесплатные GPU подходят для первого прогона **4B-модели** (Qwen3-4B) на
T4 16 GB. 7B на T4 — верхняя граница: NF4 + `batch=1` может не влезть,
особенно с `max_length=2048`; для стабильного 7B берите арендованную
A5000/4090 (§10). Оба сервиса дают временную машину: рабочий каталог
может исчезнуть после сессии, поэтому чекпоинты и готовый адаптер
сохраняйте на Drive/Kaggle Dataset или скачивайте сразу.

### 11.1. Как запустить обучение на Kaggle за 5 минут

Готовый ноутбук — [`notebooks/kaggle_train_lora.ipynb`](../../notebooks/kaggle_train_lora.ipynb)
(на GitHub: [kaggle_train_lora.ipynb](https://github.com/iilmchat/ai-chess-annotator/blob/main/notebooks/kaggle_train_lora.ipynb)).
Он сам ставит зависимости, клонирует репозиторий, находит датасет,
проверяет его через `--dry-run`, запускает `train_lora.py` и пакует
адаптер в zip.

1. **Откройте ноутбук в Kaggle:** File → Import Notebook → GitHub → URL
   `https://github.com/iilmchat/ai-chess-annotator/blob/main/notebooks/kaggle_train_lora.ipynb`
   (или скачайте файл и загрузите его в Kaggle).
2. **Включите GPU:** Settings → Accelerator → **GPU T4 ×2**; Settings →
   Internet → **On**.
3. **Загрузите датасет:** Datasets → New Dataset → `gold_dataset.jsonl`
   (130 записей на 2026-11-01) → имя датасета `chess-gold`.
4. **Run All.** Ноутбук сам выполнит установку, dry-run, обучение и
   упаковку; готовый архив появится в **Output**.

Ноутбук по умолчанию повторяет параметры итерации 47:
`Qwen/Qwen3-4B-Instruct-2507`, `batch_size=1`, `grad_accum=16`,
`max_length=2048`, `--no-4bit`, `--force-fp16`. Режим без 4-bit требует
модели до 4B на T4 16 GB; 7B (`Qwen/Qwen2.5-7B-Instruct`) запускается
только с `--load-in-4bit`, а `MAX_LENGTH` — `1024` (консервативный ручной
сценарий ниже).

### 11.2. Kaggle Notebooks (ручной сценарий)

Бесплатный аккаунт: ~30 GPU-часов в неделю, T4 ×2 (16 GB каждая) или
P100. Для QLoRA выбирайте **T4**, а не P100: на P100 нет bf16, а
bitsandbytes-сборка капризнее.

1. Создайте Notebook → **Settings → Accelerator → GPU T4 ×2**.
2. Проверьте GPU и окружение:

   ```python
   !nvidia-smi
   !python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
   ```

3. Клонируйте репозиторий и поставьте пакеты (torch в Kaggle уже есть;
   не переустанавливайте его без необходимости):

   ```python
   !git clone https://github.com/iilmchat/ai-chess-annotator.git
   %cd ai-chess-annotator
   !pip install -q -U peft transformers datasets accelerate bitsandbytes tensorboard
   ```

4. Загрузите `data/gold_dataset.jsonl` как **Kaggle Dataset** (например,
   `chess-gold`). В ноутбуке он будет в
   `/kaggle/input/chess-gold/gold_dataset.jsonl`. Старое имя
   `lora_gold_v1.jsonl` тоже поддерживается — замените имя файла в
   командах ниже.
5. Запустите обучение (4B, T4, QLoRA; effective batch 16):

   ```python
   !python scripts/train_lora.py \
       --dataset /kaggle/input/chess-gold/gold_dataset.jsonl \
       --model Qwen/Qwen3-4B-Instruct-2507 \
       --output-dir /kaggle/working/models/lora-club-v1 \
       --epochs 3 \
       --batch-size 1 \
       --grad-accum 16 \
       --max-length 1024 \
       --load-in-4bit \
       --gradient-checkpointing \
       --report_to tensorboard \
       --seed 42
   ```

6. TensorBoard в Kaggle: запустите `%tensorboard --logdir
   /kaggle/working/models/lora-club-v1/runs` (если расширение доступно)
   либо скачайте `runs/` и откройте локально.
7. Упакуйте и скачайте адаптер из вкладки **Output**:

   ```python
   !cd /kaggle/working && zip -r lora-club-v1.zip models/lora-club-v1
   ```

8. Продолжение после обрыва: Kaggle-сессия ограничена (~12 ч). Если
   `checkpoint-*` успели сохраниться в `/kaggle/working`, перезапустите
   ячейку с `--resume_from_checkpoint
   /kaggle/working/models/lora-club-v1/checkpoint-N`. Если сессия
   уничтожила каталог — загрузите прошлый zip как Dataset, распакуйте в
   `/kaggle/working` и только потом продолжайте.

### 11.3. Google Colab

Бесплатный Colab: T4 16 GB, сессия до ~12 ч, но при простое среда
отключается раньше. Диск не сохраняется между сессиями — используйте
Google Drive.

1. Подключите Drive:

   ```python
   from google.colab import drive
   drive.mount('/content/drive')
   ```

2. Клонируйте репозиторий, поставьте зависимости и **заблокируйте
   TensorFlow** до импорта `torch` (иначе Colab переключает PyTorch на CPU,
   см. §11.3.1):

   ```python
   !git clone https://github.com/iilmchat/ai-chess-annotator.git
   %cd ai-chess-annotator
   !pip install -q -U peft transformers datasets accelerate bitsandbytes tensorboard
   !pip install -q --upgrade "torchao>=0.16.0" --no-deps

   import os
   os.environ["USE_TF"] = "0"
   os.environ["TRANSFORMERS_NO_TF"] = "1"
   ```

3. Положите `gold_dataset.jsonl` в
   `/content/drive/MyDrive/chess/gold_dataset.jsonl` (через веб-интерфейс
   Drive или `files.upload()`).
4. Обучение с сохранением прямо на Drive (4B, T4, без 4-bit, fp16):

   ```python
   !python scripts/train_lora.py \
       --dataset /content/drive/MyDrive/chess/gold_dataset.jsonl \
       --model Qwen/Qwen3-4B-Instruct-2507 \
       --output-dir /content/drive/MyDrive/chess/models/lora-club-v1 \
       --epochs 3 \
       --batch-size 1 \
       --grad-accum 16 \
       --max-length 2048 \
       --no-4bit \
       --force-fp16 \
       --gradient-checkpointing \
       --report_to tensorboard \
       --seed 42
   ```

5. После обучения скачайте `models/lora-club-v1` из Drive или упакуйте:

   ```python
   !cd /content/drive/MyDrive/chess/models && zip -r lora-club-v1.zip lora-club-v1
   ```

6. Продолжение: `--resume_from_checkpoint
   /content/drive/MyDrive/chess/models/lora-club-v1/checkpoint-N`. Drive
   переживает сессию, поэтому чекпоинты не теряются.

#### 11.3.1. Решение проблем (Troubleshooting)

| Симптом | Причина | Что делать |
| :--- | :--- | :--- |
| `OSError: libcudart.so.12`, `torch.cuda.is_available() == False` после импорта TensorFlow | Colab-овский TF инициализирует CUDA 13.0, падает и уводит PyTorch на CPU | выполнить `os.environ["USE_TF"] = "0"` и `os.environ["TRANSFORMERS_NO_TF"] = "1"` **до** импорта `torch`; в ноутбуке это ячейка 1.1, затем **Runtime → Restart and run all** |
| `ImportError: torchao` / `peft` падает при импорте | в образе Colab старая `torchao`, на которой не работает `peft` | `!pip install -q --upgrade "torchao>=0.16.0" --no-deps`, затем **Restart** |
| Обучение зависает на первом шаге, в логе `bf16` | T4 (Turing) не поддерживает bf16, но `torch.cuda.is_bf16_supported()` иногда возвращает `True` | запускать с `--force-fp16`: скрипт выставит `bf16=False`, `fp16=True` |
| `CUDA out of memory` сразу при загрузке модели | 7B без 4-bit не помещается в 16 GB | взять 4B (`Qwen/Qwen3-4B-Instruct-2507`) или вернуть `--load-in-4bit`, `--max-length 1024` |
| `bitsandbytes` падает с ошибкой CUDA-версии | wheel bitsandbytes не совпал с CUDA образа | режим `--no-4bit` (как в команде выше); не переустанавливать `torch` из Colab-образа |

**Почему `--no-4bit` и `--force-fp16` на T4.** В Colab сборка
`bitsandbytes` часто конфликтует с CUDA 13.0/драйвером: QLoRA падает уже
на загрузке модели, а не на обучении. Отказ от 4-bit убирает этот слой
(ценой ~4× памяти), поэтому на 16 GB T4 без квантования помещается только
модель до 4B. T4 — архитектура Turing: bf16 аппаратно не поддерживается,
а `is_bf16_supported()` на части сборок Colab врёт (`True`), из-за чего
`Trainer` зависает. `--force-fp16` принудительно ставит `fp16=True` и
`bf16=False`; `train_lora.py` делает то же автоматически, если видит
«Tesla T4» в имени устройства.

**Если `skipped_too_long` остался.** `--max-length` считает **всю** запись:
system-промпт + пакет фактов + ответ. На «золотом датасете» (1000 записей,
2026-11-02) медиана — ~5900 символов на запись, из них system ~2600 и
assistant ~1700; при ~3 символах на токен это ~2000 токенов, поэтому
лимит 2048 режет часть записей даже после сокращения пакета. Поднимите
`--max-length` до `3072` (T4/4B/fp16 это выдерживает с gradient
checkpointing) или сократите system-промпт/ответ.

### 11.4. Ограничения бесплатных GPU

- **VRAM 16 GB:** 4B в NF4 + `batch=1` + `max_length=1024` — рабочая
  точка; без квантования (`--no-4bit`) на T4 помещается только 4B при
  `max_length=2048`. При OOM уменьшите `--max-length` до 512.
- **Нет bf16 на P100/T4:** скрипт сам выставляет fp16, если
  `torch.cuda.is_bf16_supported()` == `False`, а при `--force-fp16` или
  имени устройства «Tesla T4» — принудительно, даже когда флаг сообщает
  `True`. Для T4 это штатный режим, а не ошибка.
- **Прерывания:** сессия может закончиться в любой момент. Сохраняйте
  `output_dir` на Drive/Kaggle Dataset и используйте
  `--resume_from_checkpoint`.
- **Сеть:** скачивание Qwen может занимать долго; заранее положите модель
  в Kaggle Dataset/Drive и передайте локальный путь `--model ...`.
- **GGUF-экспорт** лучше делать не на бесплатном T4: merge 7B требует
  ~16 ГБ RAM и много времени; используйте арендованный CPU/GPU или
  основную машину (§10.5).

---

## Чек-лист перед обучением

- [ ] `nvidia-smi` видит RTX 3060; `torch.cuda.is_available()` → `True`.
- [ ] `python scripts/train_lora.py --check-env` показывает `cuda: да`,
      `bf16: да` (или осознанно принято «нет»).
- [ ] `alembic upgrade head` выполнен; в БД есть `edits` и `moves`.
- [ ] `--dry-run` сборщика датасета: `Экспортировано записей` > 0,
      пропуски объяснены.
- [ ] Внешний gold-датасет (если используется): конвертер отработал без
      ошибок, `train_lora.py --dry-run` показывает `schema_version: 2`.
- [ ] `train_lora.py --dry-run`: `schema_version=2`, токены помещаются в
      `--max-length`.
- [ ] После обучения в `models/lora-club-v1/adapter_config.json` есть
      `r=16`, `lora_alpha=32`.
- [ ] Логи лосса пишутся (`models/.../runs` для TensorBoard или
      `--report_to none` осознанно).
- [ ] При обрыве обучение продолжено с `--resume_from_checkpoint
      models/.../checkpoint-N`, а не запущено заново.
- [ ] `export_to_gguf.py` отработал: есть `model.gguf` (или HF-модель при
      отсутствии llama.cpp); файл открывается в LM Studio.
- [ ] На бесплатном GPU (Kaggle/Colab): T4, открыт
      `notebooks/kaggle_train_lora.ipynb` (4B/2048, `--no-4bit`
      `--force-fp16`) или ручной сценарий §11.2 (4B/1024); output/checkpoint
      на Drive или Kaggle Dataset.
- [ ] Адаптер скопирован на основную машину, `/v1/models` содержит
      `lora-club-v1`, smoke `/v1/commentary` вернул 200.
