"""Контракт Kaggle-ноутбука обучения LoRA (итерация 46)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NOTEBOOK = (
    Path(__file__).resolve().parents[2]
    / "notebooks"
    / "kaggle_train_lora.ipynb"
)


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return data


def _code_text(notebook: dict[str, Any]) -> str:
    return "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )


def _markdown_text(notebook: dict[str, Any]) -> str:
    return "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "markdown"
    )


def test_notebook_is_clean_nbformat4() -> None:
    notebook = _load()

    assert notebook["nbformat"] == 4
    assert len(notebook["cells"]) >= 10
    ids = [cell["id"] for cell in notebook["cells"]]
    assert len(ids) == len(set(ids))
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None
            assert cell["outputs"] == []


def test_notebook_installs_training_dependencies() -> None:
    code = _code_text(_load())

    assert "pip install" in code
    for package in (
        "torch",
        "transformers",
        "peft",
        "datasets",
        "accelerate",
        "bitsandbytes",
    ):
        assert package in code
    assert 'torchao>=0.16.0' in code
    assert "--no-deps" in code


def test_notebook_blocks_tensorflow_after_deps() -> None:
    """Итерация 47: TF блокируется до импорта torch (иначе CUDA/CPU-фолбэк)."""
    notebook = _load()
    code = _code_text(notebook)
    ids = [cell["id"] for cell in notebook["cells"]]
    tf_cell = "".join(
        next(cell for cell in notebook["cells"] if cell["id"] == "tf-block")[
            "source"
        ]
    )

    assert 'os.environ["USE_TF"] = "0"' in tf_cell
    assert 'os.environ["TRANSFORMERS_NO_TF"] = "1"' in tf_cell
    assert "tensorflow" in tf_cell
    assert ids.index("tf-block") < ids.index("gpu")
    assert "USE_TF" in code


def test_notebook_clones_repo_and_finds_gold_dataset() -> None:
    code = _code_text(_load())

    assert "git clone" in code
    assert "scripts/train_lora.py" in code
    assert "gold_dataset.jsonl" in code
    assert "DATASET_PATH" in code
    assert "--dry-run" in code


def test_notebook_uses_t4_safe_parameters_and_qwen_4b() -> None:
    code = _code_text(_load())

    assert 'MODEL_NAME = "Qwen/Qwen3-4B-Instruct-2507"' in code
    assert "BATCH_SIZE = 1" in code
    assert "GRAD_ACCUM = 16" in code
    assert "MAX_LENGTH = 2048" in code
    assert '"--batch-size"' in code
    assert '"--grad-accum"' in code
    assert '"--max-length"' in code
    assert '"--no-4bit"' in code
    assert '"--force-fp16"' in code
    assert '"--load-in-4bit"' not in code
    assert "hf-mirror.com" in code


def test_notebook_explains_fallbacks_and_resume() -> None:
    markdown = _markdown_text(_load())
    code = _code_text(_load())

    assert "OOM" in markdown
    assert "Qwen/Qwen2.5-7B-Instruct" in markdown
    assert "--resume_from_checkpoint" in code
    assert "полуход" not in markdown.lower()
    assert "полуход" not in code.lower()
