"""Каркас LoRA/QLoRA-дообучения Qwen-4B на `edits` (T-104).

`--dry-run` не импортирует torch/transformers/peft: проверяет JSONL и
печатает статистику. Реальный запуск требует GPU и зависимостей:

```bash
pip install torch transformers peft datasets accelerate bitsandbytes
python scripts/export_dataset.py --output data/lora_dataset.jsonl
python scripts/train_lora.py --dry-run
python scripts/train_lora.py \
    --dataset data/lora_dataset.jsonl \
    --model Qwen/Qwen3-4B-Instruct-2507 \
    --output-dir data/lora_adapter --load-in-4bit
```

После обучения укажите `LORA_ADAPTER_PATH=data/lora_adapter` и запустите
vLLM с `--lora-modules qwen-lora=data/lora_adapter`. Клиент использует
basename пути как имя adapter-модели (см. `Settings.lora_model`).
LM Studio динамические LoRA через OpenAI API не поддерживает.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

__all__ = ["DEFAULT_DATASET", "main", "validate_dataset"]

DEFAULT_DATASET = PROJECT_ROOT / "data" / "lora_dataset.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "lora_adapter"


def _validate_record(
    index: int, record: Any
) -> str | None:
    if not isinstance(record, Mapping):
        return f"строка {index}: не JSON-объект"
    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return f"строка {index}: нет messages (user+assistant)"
    roles = {
        item.get("role")
        for item in messages
        if isinstance(item, Mapping)
    }
    if not {"user", "assistant"} <= roles:
        return f"строка {index}: нужны роли user и assistant"
    for item in messages:
        if not isinstance(item, Mapping):
            return f"строка {index}: message не объект"
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            return f"строка {index}: пустой content"
    return None


def validate_dataset(path: Path) -> tuple[int, list[str]]:
    """Проверить JSONL; вернуть (число валидных строк, ошибки)."""
    count = 0
    errors: list[str] = []
    if not path.is_file():
        return 0, [f"файл не найден: {path}"]
    with path.open("r", encoding="utf-8") as stream:
        for index, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"строка {index}: битый JSON: {exc.msg}")
                continue
            error = _validate_record(index, record)
            if error:
                errors.append(error)
                continue
            count += 1
    return count, errors


def _format_example(record: Mapping[str, Any], tokenizer: Any) -> str:
    messages = record["messages"]
    if hasattr(tokenizer, "apply_chat_template"):
        return str(
            tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=False,
            )
        )
    return "\n".join(
        f"{item['role']}: {item['content']}" for item in messages
    )


def train(args: argparse.Namespace) -> None:
    """QLoRA-обучение; импорт тяжёлых библиотек только здесь."""
    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:  # pragma: no cover - окружение без GPU
        raise SystemExit(
            "Нет зависимостей LoRA: pip install torch transformers peft "
            "datasets accelerate bitsandbytes"
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(
        args.model, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    if args.load_in_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quantization_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    if args.load_in_4bit:
        model = prepare_model_for_kbit_training(model)

    lora = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    )
    model = get_peft_model(model, lora)

    dataset = load_dataset(
        "json", data_files=str(args.dataset), split="train"
    )
    if args.limit:
        dataset = dataset.select(range(min(args.limit, len(dataset))))

    def tokenize(example: Mapping[str, Any]) -> dict[str, Any]:
        text = _format_example(example, tokenizer)
        encoded = tokenizer(
            text,
            truncation=True,
            max_length=args.max_length,
            padding=False,
        )
        encoded["labels"] = list(encoded["input_ids"])
        return encoded

    tokenized = dataset.map(
        tokenize,
        remove_columns=dataset.column_names,
        desc="tokenize",
    )
    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        logging_steps=10,
        save_strategy="epoch",
        bf16=torch.cuda.is_bf16_supported(),
        report_to="none",
        remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
        data_collator=DataCollatorForLanguageModeling(
            tokenizer=tokenizer, mlm=False
        ),
    )
    trainer.train()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    print(f"LoRA-адаптер сохранён: {args.output_dir}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QLoRA-дообучение Qwen-4B на edits (T-104)."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--model", default="Qwen/Qwen3-4B-Instruct-2507"
    )
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только проверить датасет, без импорта torch/transformers",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    count, errors = validate_dataset(args.dataset)
    print(f"Записей: {count}, ошибок: {len(errors)}")
    for error in errors[:20]:
        print(f"  - {error}", file=sys.stderr)
    if errors:
        print("Датасет невалиден; обучение не запущено.", file=sys.stderr)
        return 1
    if count == 0:
        print("Датасет пуст; сначала экспортируйте edits.", file=sys.stderr)
        return 1
    if args.dry_run:
        print("DRY-RUN OK: формат JSONL корректен, обучение не запускалось.")
        return 0
    train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
