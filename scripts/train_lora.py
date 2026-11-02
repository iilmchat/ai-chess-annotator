"""LoRA/QLoRA-дообучение Qwen-4B на `edits` (T-104, KI-039).

Скрипт — часть пайплайна LoRA:

1. `scripts/export_dataset.py --level club --schema-version 2` — JSONL из
   `edits` (по умолчанию v2: `user` — пакет фактов, `assistant` — правка);
2. `scripts/train_lora.py --dry-run ...` — офлайн-проверка датасета и
   гиперпараметров (не импортирует torch/transformers/peft);
3. `scripts/train_lora.py ... --load-in-4bit` — реальный SFT на NVIDIA GPU
   (RunPod/Colab; локально GPU нет, см. `docs/development/reports/
   LORA_TRAINING.md`).

Поддерживаются две версии схемы:

* **v2** (актуальная, KI-039): `user` — сериализованный пакет фактов, модель
  учится писать комментарий из фактов;
* **v1** (legacy): `user` — исходный текст модели, модель учится rewrite.
  Принимается для сравнения, `--dry-run` печатает предупреждение.

Маскирование лосса: labels для `system`/`user` (и служебных токенов шаблона)
равны `-100`, обучаются только токены `assistant`-ответа. На GPU без bf16
(T4/P100) QLoRA автоматически идёт в fp16, на Ampere+ — в bf16; на T4 bf16
отключается принудительно (`--force-fp16` или определение «Tesla T4»).
TensorFlow блокируется через `USE_TF`/`TRANSFORMERS_NO_TF` до импорта
torch/transformers: в Colab он инициализирует CUDA 13.0, падает на
`libcudart.so.12` и уводит PyTorch на CPU.

```bash
python scripts/export_dataset.py --level club --schema-version 2 \
    --output data/lora_dataset.jsonl
python scripts/train_lora.py --dry-run --dataset data/lora_dataset.jsonl
python scripts/train_lora.py \
    --dataset data/lora_dataset.jsonl \
    --model Qwen/Qwen3-4B-Instruct-2507 \
    --output-dir models/lora-club-v1 --no-4bit --force-fp16
```

Логи лосса: `--report_to auto` (по умолчанию) включает TensorBoard,
если пакет установлен, иначе wandb; `--report_to none` оставляет только
консоль. Прерванное обучение продолжается с чекпоинта:
`--resume_from_checkpoint models/lora-club-v1/checkpoint-500`.
Записи, не помещающиеся в `--max-length`, по умолчанию пропускаются
(`--skip-too-long`); `--no-skip-too-long` возвращает падение.

После обучения укажите `LORA_ADAPTER_PATH=models/lora-club-v1` и запустите
vLLM с `--lora-modules lora-club-v1=models/lora-club-v1`: клиент использует
basename пути как имя adapter-модели (см. `Settings.lora_model`).
Для LM Studio адаптер сначала смердживается и конвертируется в GGUF:
`scripts/export_to_gguf.py` (T-088).
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import os
import platform
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Colab/Kaggle: встроенный TensorFlow инициализирует CUDA 13.0, падает на
# libcudart.so.12 и переключает PyTorch на CPU. Блокируем TF до импорта
# torch/transformers (итерация 47); сами библиотеки импортируются лениво.
os.environ["USE_TF"] = "0"
os.environ["TRANSFORMERS_NO_TF"] = "1"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

__all__ = [
    "DEFAULT_DATASET",
    "DEFAULT_MODEL",
    "DEFAULT_OUTPUT",
    "DEFAULT_TARGET_MODULES",
    "FACT_PACKAGE_MARKERS",
    "LEGACY_SCHEMA_VERSION",
    "REPORT_TO_CHOICES",
    "SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "DatasetStats",
    "TooLongError",
    "build_parser",
    "collate_batch",
    "dataset_stats",
    "dataset_warnings",
    "estimate_tokens",
    "looks_like_fact_package",
    "main",
    "read_records",
    "resolve_report_to",
    "resolve_resume_checkpoint",
    "tokenize_example",
    "validate_dataset",
]

#: Текущая версия схемы JSONL (см. `scripts/export_dataset.py`).
SCHEMA_VERSION = 2
#: Legacy-схема «original_text → edited_text» (rewrite-семантика).
LEGACY_SCHEMA_VERSION = 1
#: Версии схемы, которые принимает обучение (v1 — для сравнения).
SUPPORTED_SCHEMA_VERSIONS = frozenset({LEGACY_SCHEMA_VERSION, SCHEMA_VERSION})
#: Признаки пакета фактов в `user` (schema_version=2, `build_fact_package`).
FACT_PACKAGE_MARKERS = ("# Партия", "# Ключевые моменты")
#: Обязательные роли и их строгий порядок в каждом примере.
REQUIRED_ROLES = ("system", "user", "assistant")
#: Лимит на длину одного сообщения в символах (защита от битых выгрузок).
MAX_CONTENT_CHARS = 50_000
#: Лимит на число сообщений в одном примере.
MAX_MESSAGES = 16

DEFAULT_DATASET = PROJECT_ROOT / "data" / "lora_dataset.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "lora-club-v1"
DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507"
DEFAULT_TARGET_MODULES = ("q_proj", "v_proj")
#: Допустимые значения --report_to.
REPORT_TO_CHOICES = ("auto", "tensorboard", "wandb", "none")

INSTALL_HINT = (
    "Нет зависимостей для обучения LoRA: нужны torch, transformers, peft,\n"
    "datasets, accelerate (и bitsandbytes для --load-in-4bit).\n"
    "Локальный venv без NVIDIA GPU/CUDA обучение не запускает — это "
    "ожидаемо.\n"
    "Установите на GPU-машине (RunPod/Colab):\n"
    "  pip install torch transformers peft datasets accelerate "
    "bitsandbytes\n"
    "Инструкция: docs/development/reports/LORA_TRAINING.md"
)
NO_CUDA_HINT = (
    "CUDA/GPU не найдена: QLoRA-обучение требует NVIDIA GPU.\n"
    "Локально доступен только офлайн-режим:\n"
    "  python scripts/train_lora.py --dry-run --dataset <dataset.jsonl>\n"
    "Запуск на GPU (RunPod/Colab и т.п.) и стоимость — в\n"
    "docs/development/reports/LORA_TRAINING.md:\n"
    "  python scripts/train_lora.py --dataset data/lora_dataset.jsonl \\\n"
    "      --output-dir models/lora-club-v1 --load-in-4bit"
)
OOM_HINT = (
    "CUDA OOM: не хватило видеопамяти. Варианты:\n"
    "  - уменьшить --batch-size (например, до 1) и увеличить --grad-accum;\n"
    "  - уменьшить --max-length (например, 1024);\n"
    "  - включить --load-in-4bit (QLoRA; нужен bitsandbytes);\n"
    "  - взять GPU с большей памятью (A100 40/80 GB, L40S, RTX 4090).\n"
    "Инструкция: docs/development/reports/LORA_TRAINING.md"
)


@dataclass(frozen=True, slots=True)
class DatasetStats:
    """Сводка по валидному датасету."""

    records: int
    levels: dict[str, int]
    schema_versions: dict[str, int]
    estimated_tokens: int
    max_record_tokens: int


# --- версия схемы и метаданные ---------------------------------------------


def schema_version_of(record: Mapping[str, Any]) -> int:
    """Нормализовать `schema_version` записи (нет поля → legacy v1)."""
    raw = record.get("schema_version")
    if raw is None:
        return LEGACY_SCHEMA_VERSION
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    raise ValueError(f"schema_version должна быть целым числом, а не {raw!r}")


def metadata_of(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Вложенные `metadata` записи v2 (или `None`)."""
    metadata = record.get("metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        return None
    return metadata


def _field(record: Mapping[str, Any], key: str) -> Any:
    """Поле записи: сначала в `metadata` (v2), затем на верхнем уровне (v1)."""
    metadata = metadata_of(record)
    if metadata is not None and key in metadata:
        return metadata[key]
    return record.get(key)


def _message_content(
    record: Mapping[str, Any], role: str
) -> str | None:
    """Текст сообщения с ролью ``role`` (или `None`)."""
    messages = record.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if isinstance(message, Mapping) and message.get("role") == role:
            content = message.get("content")
            return content if isinstance(content, str) else None
    return None


def looks_like_fact_package(text: str) -> bool:
    """Похож ли `user` на сериализованный пакет фактов (`build_fact_package`).

    Эвристика намеренно лёгкая: заголовки `# Партия` и `# Ключевые
    моменты` — стабильный контракт `app/llm/prompts.py`. Она не заменяет
    валидатор, а ловит перепутанные v1/v2-выгрузки.
    """
    return all(marker in text for marker in FACT_PACKAGE_MARKERS)


# --- валидация JSONL --------------------------------------------------------


def _validate_record(index: int, record: Any) -> str | None:
    """Проверить одну запись; вернуть текст ошибки или None."""
    if not isinstance(record, Mapping):
        return f"строка {index}: не JSON-объект"
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        return f"строка {index}: нет непустого messages"
    if len(messages) > MAX_MESSAGES:
        return (
            f"строка {index}: сообщений {len(messages)} > лимита "
            f"{MAX_MESSAGES}"
        )
    schema_version = record.get("schema_version")
    try:
        version = schema_version_of(record)
    except ValueError as exc:
        return f"строка {index}: {exc}"
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        expected = " или ".join(
            str(item) for item in sorted(SUPPORTED_SCHEMA_VERSIONS)
        )
        return (
            f"строка {index}: неподдерживаемая schema_version="
            f"{schema_version!r} (ожидается {expected})"
        )
    if version == SCHEMA_VERSION:
        metadata = record.get("metadata")
        if not isinstance(metadata, Mapping):
            return (
                f"строка {index}: для schema_version=2 нет объекта metadata "
                "(id, commentary_id, game_id, level, fact_sheet_chars, ...)"
            )
        fact_chars = metadata.get("fact_sheet_chars")
        if fact_chars is not None and (
            isinstance(fact_chars, bool)
            or not isinstance(fact_chars, int)
            or fact_chars < 0
        ):
            return (
                f"строка {index}: metadata.fact_sheet_chars должен быть "
                f"целым ≥ 0, получено {fact_chars!r}"
            )

    roles: list[str] = []
    for position, item in enumerate(messages, start=1):
        if not isinstance(item, Mapping):
            return f"строка {index}: сообщение {position} — не объект"
        role = item.get("role")
        if not isinstance(role, str) or role not in REQUIRED_ROLES:
            return (
                f"строка {index}: сообщение {position}: роль должна быть "
                "system/user/assistant"
            )
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            return (
                f"строка {index}: сообщение {position} ({role}): "
                "пустой content"
            )
        if len(content) > MAX_CONTENT_CHARS:
            return (
                f"строка {index}: сообщение {position} ({role}): "
                f"content длиннее {MAX_CONTENT_CHARS} символов"
            )
        roles.append(role)

    if tuple(roles) != REQUIRED_ROLES:
        actual = "→".join(roles)
        return (
            f"строка {index}: ожидается порядок system→user→assistant, "
            f"получено {actual}"
        )
    return None


def _parse_lines(path: Path) -> tuple[list[tuple[int, Any]], list[str], int]:
    """Прочитать непустые строки JSONL: (пары индекс/объект, ошибки, всего)."""
    parsed: list[tuple[int, Any]] = []
    errors: list[str] = []
    total = 0
    with path.open("r", encoding="utf-8") as stream:
        for index, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            total += 1
            try:
                parsed.append((index, json.loads(line)))
            except json.JSONDecodeError as exc:
                errors.append(f"строка {index}: битый JSON: {exc.msg}")
    return parsed, errors, total


def validate_dataset(path: Path) -> tuple[int, list[str]]:
    """Проверить JSONL; вернуть (число валидных строк, ошибки)."""
    if not path.is_file():
        return 0, [f"файл не найден: {path}"]
    parsed, errors, _ = _parse_lines(path)
    count = 0
    for index, record in parsed:
        error = _validate_record(index, record)
        if error:
            errors.append(error)
            continue
        count += 1
    return count, errors


def read_records(path: Path) -> list[dict[str, Any]]:
    """Прочитать и провалидировать датасет для обучения.

    Raises:
        SystemExit: файла нет, JSONL пуст или невалиден (код выхода 1).
    """
    if not path.is_file():
        raise SystemExit(
            f"Датасет не найден: {path}\n"
            "Экспортируйте его из edits:\n"
            "  python scripts/export_dataset.py --level club "
            "--schema-version 2 --output data/lora_dataset.jsonl\n"
            "Либо проверьте синтетический пример:\n"
            "  python scripts/train_lora.py --dry-run --dataset "
            "tests/fixtures/lora_sample_v2.jsonl"
        )
    parsed, errors, total = _parse_lines(path)
    records: list[dict[str, Any]] = []
    for index, record in parsed:
        error = _validate_record(index, record)
        if error:
            errors.append(error)
            continue
        records.append(dict(record))
    if errors:
        preview = "\n".join(f"  - {error}" for error in errors[:20])
        hidden = len(errors) - 20
        suffix = f"\n  ... и ещё {hidden} ошибок" if hidden > 0 else ""
        raise SystemExit(
            f"Датасет невалиден: {len(errors)} ошибок из {total} строк "
            f"({path})\n{preview}{suffix}\n"
            "Нужна строгая схема system→user→assistant; переэкспортируйте "
            "датасет:\n"
            "  python scripts/export_dataset.py --level club "
            "--schema-version 2 --output data/lora_dataset.jsonl"
        )
    if not records:
        raise SystemExit(
            f"Датасет пуст: {path}\n"
            "Сначала накопите правки редакторов (PATCH /v1/commentaries/{id}) "
            "и экспортируйте их:\n"
            "  python scripts/export_dataset.py --level club "
            "--schema-version 2 --output data/lora_dataset.jsonl"
        )
    return records


def read_system_prompt(path: Path) -> str:
    """Прочитать system-промпт из файла для `--system-prompt`."""
    if not path.is_file():
        raise SystemExit(f"Файл system-промпта не найден: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"Пустой system-промпт: {path}")
    return text


def override_system_prompt(
    records: Sequence[Mapping[str, Any]], system_prompt: str
) -> list[dict[str, Any]]:
    """Заменить system-сообщение во всех записях (новая версия промпта)."""
    updated: list[dict[str, Any]] = []
    for record in records:
        messages = [dict(message) for message in record["messages"]]
        messages[0]["content"] = system_prompt
        new_record = dict(record)
        new_record["messages"] = messages
        updated.append(new_record)
    return updated


# --- статистика -------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Грубая оценка числа токенов: ~4 символа на токен (+служебные)."""
    return max(1, (len(text) + 3) // 4)


def _record_tokens(record: Mapping[str, Any]) -> int:
    return sum(
        estimate_tokens(str(message["content"]))
        for message in record["messages"]
    )


def dataset_stats(records: Sequence[Mapping[str, Any]]) -> DatasetStats:
    """Посчитать записи, уровни, версии схемы и оценку токенов."""
    levels = Counter(
        str(_field(record, "level") or "unknown") for record in records
    )
    versions = Counter(
        str(schema_version_of(record)) for record in records
    )
    tokens = [_record_tokens(record) for record in records]
    return DatasetStats(
        records=len(records),
        levels=dict(sorted(levels.items())),
        schema_versions=dict(sorted(versions.items())),
        estimated_tokens=sum(tokens),
        max_record_tokens=max(tokens, default=0),
    )


def dataset_warnings(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Предупреждения по составу датасета (v1 legacy, v2 не похож на факты).

    Обучение v1 не блокируется: схема официально поддерживается для
    сравнения. Предупреждения печатаются в `--dry-run` и перед реальным
    запуском — они объясняют, чему именно учится модель.
    """
    warnings: list[str] = []
    versions = Counter(schema_version_of(record) for record in records)
    legacy = versions.get(LEGACY_SCHEMA_VERSION, 0)
    if legacy:
        warnings.append(
            f"schema_version=1: {legacy} записей — user содержит "
            "original_text, модель учится ПЕРЕПИСЫВАТЬ готовый комментарий "
            "(rewrite), а не писать его из пакета фактов. Для новой "
            "постановки переэкспортируйте: scripts/export_dataset.py "
            "--schema-version 2"
        )
    for position, record in enumerate(records, start=1):
        if schema_version_of(record) != SCHEMA_VERSION:
            continue
        user = _message_content(record, "user")
        if user is not None and not looks_like_fact_package(user):
            warnings.append(
                f"запись {position}: schema_version=2, но user не похож на "
                "пакет фактов (нет «# Партия»/«# Ключевые моменты»); "
                "проверьте, что датасет собран scripts/export_dataset.py "
                "--schema-version 2"
            )
    return warnings


def _format_hyperparams(args: argparse.Namespace) -> str:
    effective_batch = args.batch_size * args.grad_accum
    modules = ",".join(args.target_modules)
    return (
        "Гиперпараметры: "
        f"model={args.model}, r={args.lora_r}, alpha={args.lora_alpha}, "
        f"dropout={args.lora_dropout}, target_modules={modules}, "
        f"epochs={args.epochs:g}, batch={args.batch_size}, "
        f"grad_accum={args.grad_accum} (effective={effective_batch}), "
        f"lr={args.lr:g}, max_length={args.max_length}, "
        f"4bit={'да' if args.load_in_4bit else 'нет'}, "
        f"gradient_checkpointing="
        f"{'да' if args.gradient_checkpointing else 'нет'}, "
        f"report_to={args.report_to}, "
        f"resume={'да' if args.resume_from_checkpoint else 'нет'}, "
        f"skip_too_long={'да' if args.skip_too_long else 'нет'}"
    )


def _print_stats(stats: DatasetStats, args: argparse.Namespace) -> None:
    print(f"Датасет: {args.dataset}")
    print(f"Записей: {stats.records}")
    levels = ", ".join(
        f"{level}={count}" for level, count in stats.levels.items()
    )
    print(f"Распределение по уровням: {levels or 'нет данных'}")
    versions = ", ".join(
        f"{version}={count}"
        for version, count in stats.schema_versions.items()
    )
    print(f"Распределение по schema_version: {versions or 'нет данных'}")
    average = stats.estimated_tokens // max(stats.records, 1)
    print(
        "Оценка токенов: "
        f"всего ~{stats.estimated_tokens}, "
        f"в среднем ~{average} на запись, "
        f"максимум ~{stats.max_record_tokens}"
    )
    print(_format_hyperparams(args))


def _print_warnings(records: Sequence[Mapping[str, Any]]) -> None:
    """Напечатать предупреждения по составу датасета."""
    for warning in dataset_warnings(records):
        print(f"ВНИМАНИЕ: {warning}")


# --- токенизация и маскирование labels --------------------------------------


def _render_chat(
    messages: Sequence[Mapping[str, Any]],
    tokenizer: Any,
    *,
    add_generation_prompt: bool,
) -> str:
    """Отрендерить chat-шаблон (или простой текстовый фолбэк)."""
    if hasattr(tokenizer, "apply_chat_template"):
        rendered = tokenizer.apply_chat_template(
            list(messages),
            tokenize=False,
            add_generation_prompt=add_generation_prompt,
        )
        return str(rendered)
    lines = [
        f"{message['role']}: {message['content']}" for message in messages
    ]
    if add_generation_prompt:
        lines.append("assistant:")
    return "\n".join(lines)


def _encode_text(text: str, tokenizer: Any) -> list[int]:
    encoded = tokenizer(text, add_special_tokens=False)
    token_ids = encoded["input_ids"] if isinstance(encoded, Mapping) else encoded
    return [int(token_id) for token_id in token_ids]


class TooLongError(ValueError):
    """Запись не помещается в ``max_length``: prompt не оставил места ответу."""


def tokenize_example(
    messages: Sequence[Mapping[str, Any]],
    tokenizer: Any,
    *,
    max_length: int = 2048,
) -> dict[str, list[int]]:
    """Токенизировать диалог и замаскировать prompt в labels.

    Prompt (system+user и служебные токены шаблона до ответа) получает
    label `-100`; обучаются только токены `assistant`-ответа. Граница
    определяется по общему префиксу токенов «prompt с generation prompt» и
    «полный диалог», поэтому не зависит от конкретного chat-шаблона.

    Raises:
        ValueError: нет assistant-ответа или нет общего префикса prompt.
        TooLongError: ответ целиком не влез в `max_length`.
    """
    if max_length < 2:
        raise ValueError("max_length должен быть ≥ 2")
    if len(messages) < 2 or messages[-1].get("role") != "assistant":
        raise ValueError("в записи нет assistant-ответа (последнее сообщение)")

    prompt_text = _render_chat(
        messages[:-1], tokenizer, add_generation_prompt=True
    )
    full_text = _render_chat(
        messages, tokenizer, add_generation_prompt=False
    )
    prompt_ids = _encode_text(prompt_text, tokenizer)
    full_ids = _encode_text(full_text, tokenizer)

    prompt_length = 0
    for prompt_token, full_token in zip(prompt_ids, full_ids, strict=False):
        if prompt_token != full_token:
            break
        prompt_length += 1
    if prompt_length == 0 and prompt_ids:
        raise ValueError(
            "токенизатор не даёт общего префикса prompt и полного диалога: "
            "маскирование labels невозможно"
        )

    input_ids = full_ids[:max_length]
    labels = ([-100] * prompt_length + full_ids[prompt_length:])[:max_length]
    if not any(label != -100 for label in labels):
        raise TooLongError(
            f"prompt занимает {prompt_length} токенов и не оставляет места "
            f"assistant при max_length={max_length}; увеличьте --max-length"
        )
    return {"input_ids": input_ids, "labels": labels}


def _torch_tensor(values: Sequence[Sequence[int]]) -> Any:
    """Создать ``torch.LongTensor`` (импорт torch — лениво, для dry-run)."""
    try:
        import torch
    except ImportError as exc:
        raise SystemExit(INSTALL_HINT) from exc
    return torch.tensor(values, dtype=torch.long)


def collate_batch(
    features: Sequence[Mapping[str, Any]],
    *,
    pad_token_id: int,
    label_pad_token_id: int = -100,
) -> dict[str, Any]:
    """Собрать батч с right-padding и вернуть тензоры PyTorch.

    ``Trainer`` передаёт батч напрямую в модель, поэтому списки Python
    недопустимы: ``embedding()`` ждёт ``Tensor``. Паддинг labels — ``-100``.
    """
    if not features:
        raise ValueError("пустой батч")
    batch_length = max(len(feature["input_ids"]) for feature in features)
    input_ids: list[list[int]] = []
    attention_mask: list[list[int]] = []
    labels: list[list[int]] = []
    for feature in features:
        tokens = [int(token) for token in feature["input_ids"]]
        feature_labels = [int(label) for label in feature["labels"]]
        padding = batch_length - len(tokens)
        input_ids.append(tokens + [pad_token_id] * padding)
        attention_mask.append([1] * len(tokens) + [0] * padding)
        labels.append(feature_labels + [label_pad_token_id] * padding)
    return {
        "input_ids": _torch_tensor(input_ids),
        "attention_mask": _torch_tensor(attention_mask),
        "labels": _torch_tensor(labels),
    }


def _is_oom_error(exc: BaseException) -> bool:
    """Похоже ли исключение на CUDA OOM (OutOfMemoryError — подкласс RuntimeError)."""
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def resolve_report_to(
    requested: str,
    *,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> list[str]:
    """Выбрать интеграцию логирования для ``TrainingArguments.report_to``.

    ``auto`` включает TensorBoard, если он установлен, иначе wandb, иначе
    ничего (остаётся консольный вывод Trainer). Явно запрошенная, но не
    установленная интеграция не роняет обучение: печатается предупреждение,
    логирование отключается.

    Args:
        requested: ``auto`` / ``tensorboard`` / ``wandb`` / ``none``.
        find_spec: точка подмены для unit-тестов (по умолчанию
            ``importlib.util.find_spec``).

    Returns:
        Список интеграций для ``report_to`` (может быть пустым).
    """
    if requested == "none":
        return []
    if requested == "auto":
        for candidate in ("tensorboard", "wandb"):
            if find_spec(candidate) is not None:
                return [candidate]
        return []
    if find_spec(requested) is None:
        print(
            f"ВНИМАНИЕ: {requested} не установлен — логирование лосса "
            "отключено (останется консольный вывод Trainer).",
            file=sys.stderr,
        )
        return []
    return [requested]


def resolve_resume_checkpoint(path: Path | None) -> str | None:
    """Проверить путь ``--resume_from_checkpoint`` и вернуть его строкой.

    ``None`` означает «начать с нуля» и возвращается как ``None``.

    Raises:
        SystemExit: путь задан, но не является каталогом чекпоинта.
    """
    if path is None:
        return None
    if not path.is_dir():
        raise SystemExit(
            f"Чекпоинт не найден: {path}\n"
            "Укажите каталог checkpoint-* из --output-dir или уберите "
            "--resume_from_checkpoint, чтобы начать обучение заново."
        )
    return str(path)


# --- реальное обучение ------------------------------------------------------


def _import_training_stack() -> Any:
    """Импортировать тяжёлые библиотеки; вызывается только из `train()`."""
    import torch
    from datasets import Dataset
    from peft import (
        LoraConfig,
        get_peft_model,
        prepare_model_for_kbit_training,
    )
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
    )

    return SimpleNamespace(
        torch=torch,
        Dataset=Dataset,
        LoraConfig=LoraConfig,
        get_peft_model=get_peft_model,
        prepare_model_for_kbit_training=prepare_model_for_kbit_training,
        AutoModelForCausalLM=AutoModelForCausalLM,
        AutoTokenizer=AutoTokenizer,
        BitsAndBytesConfig=BitsAndBytesConfig,
        Trainer=Trainer,
        TrainingArguments=TrainingArguments,
    )


def _looks_like_t4(torch: Any) -> bool:
    """Похоже ли устройство на Tesla T4 (Turing без аппаратного bf16).

    На части сборок Colab ``is_bf16_supported()`` возвращает ``True``,
    хотя bf16 на T4 нестабилен и обучение зависает. Поэтому T4 определяем
    и по имени устройства, а не только по флагу.
    """
    cuda = getattr(torch, "cuda", None)
    get_device_name = getattr(cuda, "get_device_name", None)
    if get_device_name is None:
        return False
    try:
        name = str(get_device_name(0))
    except (AssertionError, IndexError, RuntimeError, TypeError):
        return False
    return "t4" in name.lower()


def _force_fp16(args: argparse.Namespace, torch: Any) -> bool:
    """``--force-fp16`` или T4: bf16 отключается принудительно."""
    return bool(getattr(args, "force_fp16", False)) or _looks_like_t4(torch)


def _training_dtype(torch: Any, *, force_fp16: bool = False) -> Any:
    """bf16 на Ampere+, fp16 на T4/P100 и при ``--force-fp16``."""
    if force_fp16:
        return getattr(torch, "float16", torch.bfloat16)
    cuda = getattr(torch, "cuda", None)
    is_bf16_supported = getattr(cuda, "is_bf16_supported", None)
    if is_bf16_supported is not None and is_bf16_supported():
        return torch.bfloat16
    return getattr(torch, "float16", torch.bfloat16)


def _prepare_model(
    args: argparse.Namespace, stack: Any, torch: Any, tokenizer: Any
) -> Any:
    """Загрузить базовую модель (опционально 4-bit) и навесить LoRA."""
    model_dtype = _training_dtype(
        torch, force_fp16=_force_fp16(args, torch)
    )
    quantization_config = None
    if args.load_in_4bit:
        quantization_config = stack.BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=model_dtype,
            bnb_4bit_use_double_quant=True,
        )
    model = stack.AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quantization_config,
        device_map="auto",
        torch_dtype=model_dtype,
        trust_remote_code=True,
    )
    if args.load_in_4bit:
        model = stack.prepare_model_for_kbit_training(model)
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        if hasattr(model, "enable_input_require_grads"):
            model.enable_input_require_grads()
    model.config.use_cache = False

    lora = stack.LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=list(args.target_modules),
    )
    model = stack.get_peft_model(model, lora)
    trainable, total = model.get_nb_trainable_parameters()
    print(
        f"LoRA: trainable={trainable:,} из {total:,} "
        f"({100 * trainable / max(total, 1):.2f}%)"
    )
    return model


def train(
    args: argparse.Namespace, records: Sequence[Mapping[str, Any]]
) -> None:
    """QLoRA-обучение; импорт тяжёлых библиотек — только здесь."""
    try:
        stack = _import_training_stack()
    except ImportError as exc:
        raise SystemExit(INSTALL_HINT) from exc

    resume_from_checkpoint = resolve_resume_checkpoint(
        args.resume_from_checkpoint
    )
    if resume_from_checkpoint is not None:
        print(f"Продолжение обучения с чекпоинта: {resume_from_checkpoint}")

    torch = stack.torch
    if not torch.cuda.is_available():
        raise SystemExit(NO_CUDA_HINT)
    supports_bf16 = bool(torch.cuda.is_bf16_supported())
    force_fp16 = _force_fp16(args, torch)
    if force_fp16:
        supports_bf16 = False
    print(
        f"GPU: {torch.cuda.get_device_name(0)}; "
        f"CUDA {torch.version.cuda}; "
        f"bf16={'да' if supports_bf16 else 'нет'}"
    )
    if force_fp16:
        reason = (
            "--force-fp16"
            if getattr(args, "force_fp16", False)
            else "Tesla T4"
        )
        print(f"Mixed precision: fp16 ({reason}); bf16 отключён")

    tokenizer = stack.AutoTokenizer.from_pretrained(
        args.model, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    tokenized_records: list[dict[str, list[int]]] = []
    skipped_too_long = 0
    for index, record in enumerate(records, start=1):
        try:
            tokenized_records.append(
                tokenize_example(
                    record["messages"],
                    tokenizer,
                    max_length=args.max_length,
                )
            )
        except TooLongError as exc:
            if not args.skip_too_long:
                raise SystemExit(
                    f"Запись {index} не помещается в --max-length: {exc}\n"
                    "Увеличьте --max-length или включите --skip-too-long."
                ) from exc
            skipped_too_long += 1
            if skipped_too_long <= 5:
                print(
                    f"ВНИМАНИЕ: пропущена слишком длинная запись {index} "
                    f"(skipped_too_long): {exc}"
                )
        except ValueError as exc:
            raise SystemExit(
                f"Не удалось подготовить датасет: {exc}\n"
                "Проверьте --max-length и содержимое JSONL."
            ) from exc
    if not tokenized_records:
        raise SystemExit(
            "Все записи пропущены: ни одна не помещается в --max-length "
            f"({args.max_length}). Увеличьте --max-length."
        )
    if skipped_too_long:
        print(
            "Пропущено слишком длинных записей "
            f"(skipped_too_long): {skipped_too_long}"
        )
    tokenized = stack.Dataset.from_list(tokenized_records)

    try:
        model = _prepare_model(args, stack, torch, tokenizer)
    except RuntimeError as exc:
        if _is_oom_error(exc):
            raise SystemExit(OOM_HINT) from exc
        raise

    report_to = resolve_report_to(args.report_to)
    print(
        "Логирование лосса: "
        f"{', '.join(report_to) if report_to else 'консоль'}"
    )
    training_args = stack.TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        logging_steps=args.logging_steps,
        save_strategy="epoch",
        bf16=supports_bf16,
        fp16=not supports_bf16,
        gradient_checkpointing=args.gradient_checkpointing,
        optim="paged_adamw_8bit" if args.load_in_4bit else "adamw_torch",
        lr_scheduler_type="cosine",
        seed=args.seed,
        report_to=report_to,
        remove_unused_columns=False,
    )
    collator = functools.partial(
        collate_batch, pad_token_id=tokenizer.pad_token_id
    )
    trainer = stack.Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
        data_collator=collator,
    )
    try:
        trainer.train(resume_from_checkpoint=resume_from_checkpoint)
    except RuntimeError as exc:
        if _is_oom_error(exc):
            raise SystemExit(OOM_HINT) from exc
        raise

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    print(f"LoRA-адаптер сохранён: {args.output_dir}")
    print(
        "Подключение: LORA_ADAPTER_PATH="
        f"{args.output_dir} и vLLM --lora-modules "
        f"{args.output_dir.name}={args.output_dir}"
    )


# --- CLI --------------------------------------------------------------------


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено {value!r}"
        ) from exc
    if number < 1:
        raise argparse.ArgumentTypeError("должно быть ≥ 1")
    return number


def _module_list(value: str) -> tuple[str, ...]:
    modules = tuple(part.strip() for part in value.split(",") if part.strip())
    if not modules:
        raise argparse.ArgumentTypeError("пустой список target modules")
    return modules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "LoRA/QLoRA-дообучение Qwen-4B на edits (T-104); "
            "--dry-run работает офлайн без torch."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--system-prompt",
        type=Path,
        default=None,
        help="txt-файл: заменить system-сообщение во всех записях",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="взять первые N записей",
    )
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--batch-size", type=_positive_int, default=1)
    parser.add_argument("--grad-accum", type=_positive_int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-length", type=_positive_int, default=2048)
    parser.add_argument(
        "--force-fp16",
        action="store_true",
        help=(
            "принудительно fp16 (bf16=False): нужно на T4, где bf16 "
            "нестабилен даже если torch сообщает о поддержке"
        ),
    )
    parser.add_argument(
        "--skip-too-long",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "пропускать записи, которые не помещаются в --max-length "
            "(по умолчанию включено; --no-skip-too-long — падать)"
        ),
    )
    parser.add_argument("--lora-r", type=_positive_int, default=16)
    parser.add_argument("--lora-alpha", type=_positive_int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument(
        "--target-modules",
        type=_module_list,
        default=DEFAULT_TARGET_MODULES,
        help="модули через запятую (по умолчанию q_proj,v_proj)",
    )
    parser.add_argument("--logging-steps", type=_positive_int, default=10)
    parser.add_argument(
        "--report_to",
        choices=REPORT_TO_CHOICES,
        default="auto",
        help=(
            "куда логировать лосс: auto (TensorBoard/wandb, если есть) | "
            "tensorboard | wandb | none (по умолчанию auto)"
        ),
    )
    parser.add_argument(
        "--resume_from_checkpoint",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "продолжить обучение с каталога checkpoint-* "
            "(например, models/lora-club-v1/checkpoint-500)"
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--gradient-checkpointing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="экономить VRAM за счёт скорости (по умолчанию включено)",
    )
    quantization = parser.add_mutually_exclusive_group()
    quantization.add_argument(
        "--load-in-4bit",
        dest="load_in_4bit",
        action="store_true",
        help="QLoRA: базовая модель в 4-bit (bitsandbytes)",
    )
    quantization.add_argument(
        "--no-4bit",
        dest="load_in_4bit",
        action="store_false",
        help="отключить 4-bit (нужен больший объём VRAM)",
    )
    parser.set_defaults(load_in_4bit=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только проверить датасет и напечатать статистику, без torch",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="показать диагностику окружения (python, torch, CUDA) и выйти",
    )
    return parser


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _print_env_check() -> None:
    print("Окружение LoRA:")
    print(f"  python: {platform.python_version()} ({sys.executable})")
    print(f"  platform: {platform.platform()}")
    packages = (
        "torch",
        "transformers",
        "peft",
        "datasets",
        "accelerate",
        "bitsandbytes",
        "trl",
        "tensorboard",
        "wandb",
    )
    for package in packages:
        installed = importlib.util.find_spec(package) is not None
        print(f"  {package}: {'установлен' if installed else 'НЕТ'}")
    if importlib.util.find_spec("torch") is None:
        print("  cuda: нет (torch не установлен)")
        print("  вывод: локально доступен только --dry-run")
        return
    try:
        import torch
    except Exception as exc:  # pragma: no cover - битая установка torch
        print(f"  cuda: ошибка импорта torch: {exc}")
        return
    if torch.cuda.is_available():
        print("  cuda: да")
        print(f"  device: {torch.cuda.get_device_name(0)}")
        print(f"  bf16: {'да' if torch.cuda.is_bf16_supported() else 'нет'}")
        if _looks_like_t4(torch):
            print("  T4: bf16 будет отключён, обучение пойдёт в fp16")
    else:
        print("  cuda: НЕТ (GPU не видна)")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_env:
        _print_env_check()
        return 0

    records = read_records(args.dataset)
    if args.limit is not None:
        if args.limit < len(records):
            print(
                f"Ограничение --limit: используется {args.limit} "
                f"из {len(records)} записей"
            )
        records = records[: args.limit]
    if args.system_prompt is not None:
        system_prompt = read_system_prompt(args.system_prompt)
        records = override_system_prompt(records, system_prompt)
        print(f"System-промпт переопределён из {args.system_prompt}")

    stats = dataset_stats(records)
    _print_stats(stats, args)
    _print_warnings(records)
    if args.dry_run:
        print(
            "DRY-RUN OK: схема JSONL корректна, статистика посчитана, "
            "обучение не запускалось."
        )
        return 0

    train(args, records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
