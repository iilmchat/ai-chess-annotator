#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Скрипт автоматической настройки и запуска обучения LoRA на GPU-сервере.
# ОС: Ubuntu 22.04 / 24.04 / 26.04 (с драйверами NVIDIA).
# Требует переменные окружения: S3_ACCESS_KEY, S3_SECRET_KEY.
# =============================================================================

REPO_URL="https://github.com/iilmchat/ai-chess-annotator.git"
PROJECT_DIR="$HOME/ai-chess-annotator"
S3_BUCKET="chess-annotator-data"
S3_ENDPOINT="https://s3.ru-7.storage.selcloud.ru"
S3_DATASET="gold_dataset.jsonl"
LOCAL_DATASET="$PROJECT_DIR/data/gold_dataset.jsonl"

log() { printf '\n\033[1;34m[setup]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Проверка переменных окружения для S3
if [[ -z "${S3_ACCESS_KEY:-}" || -z "${S3_SECRET_KEY:-}" ]]; then
    die "Задайте S3_ACCESS_KEY и S3_SECRET_KEY перед запуском скрипта!"
fi

# 2. Установка системных пакетов
log "1/6. Установка системных пакетов (git, curl, python3-venv, unzip)"
sudo apt-get update -qq
sudo apt-get install -y -qq git curl python3-venv python3-pip unzip

# 3. Клонирование репозитория
if [[ -d "$PROJECT_DIR" ]]; then
    log "2/6. Папка проекта уже существует. Обновляем код..."
    cd "$PROJECT_DIR"
    git pull origin main
else
    log "2/6. Клонирование репозитория..."
    git clone "$REPO_URL" "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

# 4. Создание окружения и установка зависимостей через uv
log "3/6. Установка uv (быстрый менеджер пакетов)"
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env
fi

log "4/6. Создание виртуального окружения и установка PyTorch (cu121)"
uv venv --python 3.12 .venv
source .venv/bin/activate

# Обход таймаутов: ставим torch с китайского зеркала, остальное с Tsinghua
uv pip install torch torchvision torchaudio --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cu121/
uv pip install peft transformers accelerate tensorboard bitsandbytes -i https://pypi.tuna.tsinghua.edu.cn/simple/
uv pip install -e ".[dev]"

# 5. Скачивание датасета из S3
log "5/6. Скачивание датасета из S3 (${S3_BUCKET}/${S3_DATASET})"
mkdir -p "$PROJECT_DIR/data"
python -c "
import boto3
s3 = boto3.client(
    's3',
    endpoint_url='${S3_ENDPOINT}',
    aws_access_key_id='${S3_ACCESS_KEY}',
    aws_secret_access_key='${S3_SECRET_KEY}',
    region_name='ru-7'
)
s3.download_file('${S3_BUCKET}', '${S3_DATASET}', '${LOCAL_DATASET}')
print('Датасет успешно скачан!')
" || die "Не удалось скачать датасет из S3. Проверьте ключи и имя бакета."

# 6. Запуск обучения
log "6/6. Запуск обучения LoRA (Qwen2.5-7B) на GPU"
export BNB_CUDA_VERSION=121
export HF_ENDPOINT=https://hf-mirror.com

# Запускаем в tmux, чтобы обучение не прервалось при закрытии SSH
tmux new-session -d -s train "source .venv/bin/activate && \
python scripts/train_lora.py \
  --dataset data/gold_dataset.jsonl \
  --model Qwen/Qwen2.5-7B-Instruct \
  --output-dir models/qwen_lora_v2 \
  --epochs 3 \
  --batch-size 4 \
  --grad-accum 4 \
  --report_to tensorboard \
  --max-length 2048"

log "Готово! Обучение запущено в фоне (tmux)."
log "Чтобы посмотреть прогресс, выполните: tmux attach -t train"