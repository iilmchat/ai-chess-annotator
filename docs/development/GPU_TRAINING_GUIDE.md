# GPU-машина: перенос LoRA-обучения (T-080)

Практическая инструкция: как подготовить отдельную машину с NVIDIA GPU
(целевая — **RTX 3060 12 GB**), собрать датасет из PostgreSQL, обучить
LoRA-адаптер и вернуть его на основную машину. Рассчитана на новичка;
команды даны для **Linux** и **Windows PowerShell**.

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

Для RTX 3060 (Ampere) и Python 3.12 ставим CUDA-сборку torch:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

Проверка **обязательна** — без неё легко обучаться на CPU и не понять,
почему «всё медленно»:

```bash
python -c "import torch; print('torch:', torch.__version__); print('cuda:', torch.version.cuda); print('available:', torch.cuda.is_available()); print('gpu:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'НЕТ GPU')"
```

Ожидаемый вывод на исправной машине:

```text
torch: 2.x.x+cu121
cuda: 12.1
available: True
gpu: NVIDIA GeForce RTX 3060
```

Если `available: False`:

- проверьте, что драйвер NVIDIA установлен и `nvidia-smi` показывает GPU;
- убедитесь, что не установилась CPU-сборка (`torch.__version__` без
  `+cu121`, например `+cpu`) — переустановите командой выше;
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
   нужен именно vLLM.

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
| `torch.cuda.is_available()` → `False` | CPU-сборка torch, нет драйвера, WSL без GPU | переустановить torch с `--index-url .../cu121`; проверить `nvidia-smi` в той же среде (в WSL — внутри WSL); перезагрузить |
| `Нет зависимостей для обучения LoRA` (exit 1) | не установлены transformers/peft/datasets/accelerate | `pip install transformers peft datasets accelerate bitsandbytes` |
| `CUDA/GPU не найдена` (exit 1) | torch не видит GPU | см. строки про `torch.cuda.is_available()` |
| `bitsandbytes` не ставится на Windows | нет нативной сборки | WSL2/Ubuntu (рекомендуется) или Linux-машина |
| `prompt занимает N токенов…` | `--max-length` меньше prompt | увеличить `--max-length` (до 4096) или сократить пакет фактов |
| PostgreSQL: `port already allocated` | 5434 занят | `docker compose ps`, `netstat -ano \| findstr :5434`; изменить mapping в `docker-compose.yml` и `POSTGRES_PORT` |
| `no games with commentaries found` / exit 2 | нет `edits` или не выполнен `/v1/analyze` | проверить `select count(*) from edits;`, вызвать `/v1/analyze` для партий, повторить `--dry-run` |
| `Пропущено записей: N` | нет анализа ходов/битый PGN | посмотреть причины в сводке; выполнить `/v1/analyze`; проверить PGN |
| `schema_version=2, но user не похож на пакет фактов` | файл собран не тем скриптом | пересобрать `scripts/generate_lora_dataset_from_db.py` (v2) |
| vLLM: `model "lora-club-v1" not found` | basename не совпал с `--lora-modules` | `--lora-modules lora-club-v1=<точный путь>`; проверить `ls models/lora-club-v1` |
| `llm_lora_adapter_unavailable` в логах | нет `adapter_config.json`/путь неверный | проверить `LORA_ADAPTER_PATH`; каталог должен содержать `adapter_config.json` |
| Скачивание Qwen зависает | нет доступа к Hugging Face | `HF_TOKEN`, предварительно скачать модель; проверить сеть |

---

## Чек-лист перед обучением

- [ ] `nvidia-smi` видит RTX 3060; `torch.cuda.is_available()` → `True`.
- [ ] `python scripts/train_lora.py --check-env` показывает `cuda: да`,
      `bf16: да` (или осознанно принято «нет»).
- [ ] `alembic upgrade head` выполнен; в БД есть `edits` и `moves`.
- [ ] `--dry-run` сборщика датасета: `Экспортировано записей` > 0,
      пропуски объяснены.
- [ ] `train_lora.py --dry-run`: `schema_version=2`, токены помещаются в
      `--max-length`.
- [ ] После обучения в `models/lora-club-v1/adapter_config.json` есть
      `r=16`, `lora_alpha=32`.
- [ ] Адаптер скопирован на основную машину, `/v1/models` содержит
      `lora-club-v1`, smoke `/v1/commentary` вернул 200.
