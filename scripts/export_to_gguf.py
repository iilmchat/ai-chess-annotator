"""Экспорт LoRA-адаптера в GGUF для LM Studio (T-088, итерация 43).

Пайплайн:

1. загрузить базовую модель в fp16 (по умолчанию на CPU);
2. навесить LoRA-адаптер через ``peft.PeftModel.from_pretrained``;
3. смерджить веса ``merge_and_unload()``;
4. сохранить обычную HuggingFace-модель (``save_pretrained``);
5. если найден ``llama.cpp`` (``convert_hf_to_gguf.py``/``convert.py``) —
   сконвертировать её в GGUF; если нет — предупредить и оставить HF-формат.

Скрипт не импортирует тяжёлые библиотеки на уровне модуля: без
torch/transformers/peft он падает с понятным сообщением только при запуске
экспорта. Это позволяет тестировать CLI и логику поиска llama.cpp без
гигабайтных весов.

```bash
python scripts/export_to_gguf.py \\
    --base_model Qwen/Qwen2.5-7B-Instruct \\
    --adapter_path models/lora-club-v2 \\
    --output_dir models/qwen2.5-7b-chess
```

LM Studio не умеет динамические LoRA через OpenAI-совместимый API, поэтому
для локального инференса нужен именно смердженный GGUF (или HF-модель).
Инструкция по арендованному GPU — ``docs/development/GPU_TRAINING_GUIDE.md``
§10.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

__all__ = [
    "DEFAULT_GGUF_OUTTYPE",
    "GGUF_FILENAME",
    "INSTALL_HINT",
    "LLAMA_CPP_CONVERTERS",
    "ExportError",
    "ExportResult",
    "build_converter_command",
    "build_parser",
    "export_to_gguf",
    "find_llama_cpp_converter",
    "main",
    "run_llama_cpp_conversion",
    "validate_adapter_path",
]

#: Имя итогового GGUF-файла внутри ``--output_dir``.
GGUF_FILENAME = "model.gguf"
#: Тип точности для первого прогона конвертера (f16 — без потерь).
DEFAULT_GGUF_OUTTYPE = "f16"
#: Имена конвертеров llama.cpp в порядке приоритета (новый → старый).
LLAMA_CPP_CONVERTERS = ("convert_hf_to_gguf.py", "convert.py")

INSTALL_HINT = (
    "Нет зависимостей для экспорта GGUF: нужны torch, transformers, peft,\n"
    "accelerate.\n"
    "Установите на GPU-машине:\n"
    "  pip install torch transformers peft accelerate\n"
    "Инструкция: docs/development/GPU_TRAINING_GUIDE.md §10"
)

logger = logging.getLogger("export_to_gguf")


class ExportError(Exception):
    """Экспорт невозможен: нет адаптера, зависимостей или весов."""


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Итог экспорта: HF-каталог и (опционально) GGUF-файл."""

    hf_dir: Path
    gguf_path: Path | None = None
    converter: Path | None = None

    @property
    def gguf_created(self) -> bool:
        """Создан ли GGUF-файл."""
        return self.gguf_path is not None


def _import_export_stack() -> Any:
    """Импортировать torch/transformers/peft; только при реальном экспорте."""
    try:
        import torch
        from peft import PeftModel
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
        )
    except ImportError as exc:
        raise ExportError(INSTALL_HINT) from exc

    return SimpleNamespace(
        torch=torch,
        PeftModel=PeftModel,
        AutoModelForCausalLM=AutoModelForCausalLM,
        AutoTokenizer=AutoTokenizer,
    )


def validate_adapter_path(adapter_path: Path) -> None:
    """Проверить, что ``adapter_path`` — сохранённый LoRA-адаптер.

    Raises:
        ExportError: каталога нет или в нём нет ``adapter_config.json``.
    """
    if not adapter_path.is_dir():
        raise ExportError(
            f"LoRA-адаптер не найден: {adapter_path}\n"
            "Укажите каталог, который сохранил scripts/train_lora.py "
            "(--output-dir)."
        )
    if not (adapter_path / "adapter_config.json").is_file():
        raise ExportError(
            f"В {adapter_path} нет adapter_config.json — это не адаптер PEFT.\n"
            "Проверьте --adapter_path."
        )


def _converter_in(directory: Path) -> Path | None:
    """Найти конвертер llama.cpp в конкретном каталоге."""
    for name in LLAMA_CPP_CONVERTERS:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def find_llama_cpp_converter(llama_cpp_dir: Path | None = None) -> Path | None:
    """Найти ``convert_hf_to_gguf.py``/``convert.py`` или вернуть ``None``.

    Явный ``--llama_cpp_dir`` проверяется первым и не дополняется
    эвристиками: если пользователь указал путь, искать «где-то ещё»
    удивительно. Без аргумента проверяются типовые каталоги и ``PATH``.
    """
    if llama_cpp_dir is not None:
        return _converter_in(llama_cpp_dir)

    candidates: list[Path] = []
    for root in (
        PROJECT_ROOT / "llama.cpp",
        Path.home() / "llama.cpp",
        Path("/opt/llama.cpp"),
    ):
        candidates.extend(root / name for name in LLAMA_CPP_CONVERTERS)
    for name in ("convert_hf_to_gguf", "convert_hf_to_gguf.py"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    return next((path for path in candidates if path.is_file()), None)


def build_converter_command(
    converter: Path,
    model_dir: Path,
    outfile: Path,
    *,
    outtype: str = DEFAULT_GGUF_OUTTYPE,
) -> list[str]:
    """Команда llama.cpp: ``python convert_hf_to_gguf.py MODEL --outfile ...``."""
    return [
        sys.executable,
        str(converter),
        str(model_dir),
        "--outfile",
        str(outfile),
        "--outtype",
        outtype,
    ]


def run_llama_cpp_conversion(
    converter: Path,
    model_dir: Path,
    outfile: Path,
    *,
    outtype: str = DEFAULT_GGUF_OUTTYPE,
    runner: Callable[..., Any] = subprocess.run,
) -> bool:
    """Запустить конвертер llama.cpp; ``False`` — предупреждение, не падение.

    Args:
        converter: путь к ``convert_hf_to_gguf.py``/``convert.py``.
        model_dir: смердженная HF-модель.
        outfile: куда положить GGUF.
        outtype: ``f16``/``bf16``/``q8_0`` (передаётся как ``--outtype``).
        runner: точка подмены для unit-тестов (по умолчанию subprocess.run).

    Returns:
        ``True``, если файл создан; ``False`` — конвертация не удалась.
    """
    outfile.parent.mkdir(parents=True, exist_ok=True)
    command = build_converter_command(
        converter, model_dir, outfile, outtype=outtype
    )
    try:
        runner(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        logger.warning(
            "llama.cpp не смог сконвертировать модель: %s%s",
            exc,
            f"\n{stderr[-1000:]}" if stderr else "",
        )
        return False
    if not outfile.is_file():
        logger.warning(
            "llama.cpp завершился без ошибки, но %s не создан", outfile
        )
        return False
    return True


def export_to_gguf(
    args: argparse.Namespace,
    *,
    stack: Any | None = None,
    converter_finder: Callable[[Path | None], Path | None] | None = None,
    runner: Callable[..., Any] | None = None,
) -> ExportResult:
    """Смерджить LoRA в базовую модель, сохранить HF и (если можно) GGUF.

    Args:
        args: разобранные CLI-аргументы (``base_model``, ``adapter_path``,
            ``output_dir``, ``llama_cpp_dir``, ``gguf_outtype``, ``device``).
        stack: тестовая подмена импортов torch/transformers/peft.
        converter_finder: поиск конвертера llama.cpp (для тестов).
        runner: запуск конвертера (для тестов).

    Raises:
        ExportError: адаптер не найден, нет зависимостей или merge не удался.
    """
    adapter_path = Path(args.adapter_path)
    output_dir = Path(args.output_dir)
    validate_adapter_path(adapter_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    if stack is None:
        stack = _import_export_stack()
    torch = stack.torch

    tokenizer = stack.AutoTokenizer.from_pretrained(
        args.base_model, trust_remote_code=True
    )
    base_model = stack.AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.float16,
        device_map=getattr(args, "device", "cpu"),
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    peft_model = stack.PeftModel.from_pretrained(
        base_model, str(adapter_path)
    )
    merged = peft_model.merge_and_unload()
    if merged is None:
        raise ExportError(
            "merge_and_unload() вернул None: не удалось смерджить LoRA."
        )

    merged.save_pretrained(str(output_dir), safe_serialization=True)
    tokenizer.save_pretrained(str(output_dir))
    logger.info("HF-модель сохранена: %s", output_dir)

    converter = (
        converter_finder or find_llama_cpp_converter
    )(getattr(args, "llama_cpp_dir", None))
    if converter is None:
        logger.warning(
            "llama.cpp не найден: HF-модель сохранена в %s, GGUF не создан. "
            "Установите llama.cpp и повторите с --llama_cpp_dir.",
            output_dir,
        )
        return ExportResult(hf_dir=output_dir)

    gguf_path = output_dir / GGUF_FILENAME
    converted = run_llama_cpp_conversion(
        converter,
        output_dir,
        gguf_path,
        outtype=getattr(args, "gguf_outtype", DEFAULT_GGUF_OUTTYPE),
        runner=runner or subprocess.run,
    )
    if not converted:
        logger.warning(
            "GGUF не создан; HF-модель осталась в %s. "
            "Проверьте llama.cpp вручную.",
            output_dir,
        )
        return ExportResult(
            hf_dir=output_dir, converter=converter
        )
    logger.info("GGUF сохранён: %s", gguf_path)
    return ExportResult(
        hf_dir=output_dir, gguf_path=gguf_path, converter=converter
    )


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер экспортёра LoRA → HF → GGUF."""
    parser = argparse.ArgumentParser(
        prog="export_to_gguf.py",
        description=(
            "Смерджить LoRA-адаптер с базовой моделью, сохранить HF-формат "
            "и (опционально) конвертировать в GGUF через llama.cpp."
        ),
    )
    parser.add_argument(
        "--base_model",
        required=True,
        metavar="MODEL",
        help="HF-идентификатор или локальный путь базовой модели",
    )
    parser.add_argument(
        "--adapter_path",
        type=Path,
        required=True,
        metavar="PATH",
        help="каталог LoRA-адаптера (adapter_config.json)",
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        required=True,
        metavar="PATH",
        help="куда сохранить смердженную HF-модель и GGUF",
    )
    parser.add_argument(
        "--llama_cpp_dir",
        type=Path,
        default=None,
        metavar="PATH",
        help="каталог llama.cpp с convert_hf_to_gguf.py (иначе поиск)",
    )
    parser.add_argument(
        "--gguf_outtype",
        choices=("f16", "bf16", "q8_0"),
        default=DEFAULT_GGUF_OUTTYPE,
        help="точность GGUF (по умолчанию f16)",
    )
    parser.add_argument(
        "--device",
        choices=("cpu", "cuda", "auto"),
        default="cpu",
        help="устройство для merge (по умолчанию cpu: не нужна GPU)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: 0 — HF сохранена (даже без GGUF), 1 — ошибка."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = export_to_gguf(args)
    except ExportError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.error("Экспорт не удался: %s", exc)
        return 1

    print(f"HF-модель: {result.hf_dir}")
    if result.gguf_path is not None:
        print(f"GGUF: {result.gguf_path}")
    else:
        print(
            "GGUF не создан — HF-модель сохранена и готова к загрузке "
            "в transformers/локальный инференс."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
