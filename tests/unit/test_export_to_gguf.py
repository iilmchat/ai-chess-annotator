"""Unit-тесты экспортёра LoRA → HF → GGUF (T-088).

Тяжёлые torch/transformers/peft не импортируются: ``export_to_gguf``
принимает тестовый стек, а llama.cpp и subprocess подменяются. Проверяем
merge/save, поиск конвертера, graceful fallback без llama.cpp и CLI.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import scripts.export_to_gguf as gguf_module
from scripts.export_to_gguf import (
    DEFAULT_GGUF_OUTTYPE,
    GGUF_FILENAME,
    ExportError,
    build_converter_command,
    build_parser,
    export_to_gguf,
    find_llama_cpp_converter,
    main,
    run_llama_cpp_conversion,
    validate_adapter_path,
)

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"


def _adapter_dir(tmp_path: Path) -> Path:
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    return adapter


def _args(
    tmp_path: Path,
    *,
    device: str = "cpu",
    llama_cpp_dir: Path | None = None,
) -> Any:
    argv = [
        "--base_model",
        BASE_MODEL,
        "--adapter_path",
        str(_adapter_dir(tmp_path)),
        "--output_dir",
        str(tmp_path / "merged"),
        "--device",
        device,
    ]
    if llama_cpp_dir is not None:
        argv.extend(["--llama_cpp_dir", str(llama_cpp_dir)])
    return build_parser().parse_args(argv)


class _FakeTokenizer:
    def __init__(self) -> None:
        self.saved_to: Path | None = None

    def save_pretrained(self, path: str) -> None:
        self.saved_to = Path(path)


class _FakeMergedModel:
    def __init__(self) -> None:
        self.saved_to: Path | None = None
        self.save_kwargs: dict[str, Any] = {}

    def save_pretrained(self, path: str, **kwargs: Any) -> None:
        self.saved_to = Path(path)
        self.save_kwargs = kwargs


class _FakePeftModel:
    def __init__(
        self, base_model: object, merged_model: _FakeMergedModel | None
    ) -> None:
        self.base_model = base_model
        self.merged_model = merged_model
        self.merge_called = False

    def merge_and_unload(self) -> _FakeMergedModel | None:
        self.merge_called = True
        return self.merged_model


def _fake_stack(
    captured: dict[str, Any],
    *,
    merged_model: _FakeMergedModel | None = None,
) -> SimpleNamespace:
    """Стек с AutoModel/AutoTokenizer/PeftModel и подменой merge."""
    merged = merged_model if merged_model is not None else _FakeMergedModel()
    tokenizer = _FakeTokenizer()
    peft_model = _FakePeftModel(object(), merged)

    class _AutoModel:
        @staticmethod
        def from_pretrained(model_name: str, **kwargs: Any) -> object:
            captured["base_model"] = model_name
            captured["base_kwargs"] = kwargs
            return object()

    class _AutoTokenizer:
        @staticmethod
        def from_pretrained(model_name: str, **kwargs: Any) -> _FakeTokenizer:
            captured["tokenizer_model"] = model_name
            captured["tokenizer_kwargs"] = kwargs
            return tokenizer

    class _PeftModel:
        @staticmethod
        def from_pretrained(
            base_model: object, adapter_path: str
        ) -> _FakePeftModel:
            captured["peft_base"] = base_model
            captured["adapter_path"] = adapter_path
            return peft_model

    captured["merged_model"] = merged
    captured["tokenizer"] = tokenizer
    captured["peft_model"] = peft_model
    return SimpleNamespace(
        torch=SimpleNamespace(float16="float16"),
        AutoModelForCausalLM=_AutoModel,
        AutoTokenizer=_AutoTokenizer,
        PeftModel=_PeftModel,
    )


# --- адаптер и конвертер ----------------------------------------------------


def test_validate_adapter_path_requires_config(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter"
    adapter.mkdir()

    with pytest.raises(ExportError, match=r"adapter_config\.json"):
        validate_adapter_path(adapter)


def test_validate_adapter_path_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="не найден"):
        validate_adapter_path(tmp_path / "missing")


def test_find_llama_cpp_converter_explicit_dir_new_script(
    tmp_path: Path,
) -> None:
    converter = tmp_path / "convert_hf_to_gguf.py"
    converter.write_text("# fake", encoding="utf-8")

    assert find_llama_cpp_converter(tmp_path) == converter


def test_find_llama_cpp_converter_explicit_dir_legacy_script(
    tmp_path: Path,
) -> None:
    converter = tmp_path / "convert.py"
    converter.write_text("# fake", encoding="utf-8")

    assert find_llama_cpp_converter(tmp_path) == converter


def test_find_llama_cpp_converter_explicit_empty_dir_returns_none(
    tmp_path: Path,
) -> None:
    assert find_llama_cpp_converter(tmp_path) is None


def test_build_converter_command() -> None:
    command = build_converter_command(
        Path("/opt/llama.cpp/convert_hf_to_gguf.py"),
        Path("/models/merged"),
        Path("/models/merged/model.gguf"),
        outtype="f16",
    )

    assert command[0].endswith("python") or "python" in command[0]
    assert command[1].endswith("convert_hf_to_gguf.py")
    assert command[2] == str(Path("/models/merged"))
    assert command[3:5] == [
        "--outfile",
        str(Path("/models/merged/model.gguf")),
    ]
    assert command[5:] == ["--outtype", "f16"]


def test_run_llama_cpp_conversion_success(tmp_path: Path) -> None:
    outfile = tmp_path / "model.gguf"
    captured: dict[str, Any] = {}

    def runner(command: list[str], **kwargs: Any) -> None:
        captured["command"] = command
        captured["kwargs"] = kwargs
        outfile.write_text("GGUF", encoding="utf-8")

    ok = run_llama_cpp_conversion(
        tmp_path / "convert_hf_to_gguf.py",
        tmp_path / "model",
        outfile,
        runner=runner,
    )

    assert ok is True
    assert captured["kwargs"] == {
        "check": True,
        "capture_output": True,
        "text": True,
    }
    assert captured["command"][-2:] == ["--outtype", DEFAULT_GGUF_OUTTYPE]


def test_run_llama_cpp_conversion_failure_is_graceful(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def runner(command: list[str], **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(
            1, command, stderr="boom"
        )

    with caplog.at_level(logging.WARNING, logger="export_to_gguf"):
        ok = run_llama_cpp_conversion(
            tmp_path / "convert_hf_to_gguf.py",
            tmp_path / "model",
            tmp_path / "model.gguf",
            runner=runner,
        )

    assert ok is False
    assert "llama.cpp" in caplog.text


def test_run_llama_cpp_conversion_without_output_is_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def runner(command: list[str], **kwargs: Any) -> None:
        return None

    with caplog.at_level(logging.WARNING, logger="export_to_gguf"):
        ok = run_llama_cpp_conversion(
            tmp_path / "convert_hf_to_gguf.py",
            tmp_path / "model",
            tmp_path / "model.gguf",
            runner=runner,
        )

    assert ok is False
    assert "не создан" in caplog.text


# --- merge и сохранение -----------------------------------------------------


def test_export_merges_and_saves_hf(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    stack = _fake_stack(captured)
    args = _args(tmp_path)

    result = export_to_gguf(
        args, stack=stack, converter_finder=lambda _: None
    )

    assert result.hf_dir == tmp_path / "merged"
    assert result.gguf_path is None
    assert result.converter is None
    assert captured["base_model"] == BASE_MODEL
    assert captured["base_kwargs"]["torch_dtype"] == "float16"
    assert captured["base_kwargs"]["device_map"] == "cpu"
    assert captured["base_kwargs"]["trust_remote_code"] is True
    assert captured["base_kwargs"]["low_cpu_mem_usage"] is True
    assert captured["adapter_path"].endswith("adapter")
    assert captured["peft_model"].merge_called is True
    merged = captured["merged_model"]
    assert merged.saved_to == tmp_path / "merged"
    assert merged.save_kwargs == {"safe_serialization": True}
    assert captured["tokenizer"].saved_to == tmp_path / "merged"


def test_export_passes_cuda_device(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    args = _args(tmp_path, device="cuda")

    export_to_gguf(
        args, stack=_fake_stack(captured), converter_finder=lambda _: None
    )

    assert captured["base_kwargs"]["device_map"] == "cuda"


def test_export_rejects_missing_adapter(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--base_model",
            BASE_MODEL,
            "--adapter_path",
            str(tmp_path / "missing"),
            "--output_dir",
            str(tmp_path / "merged"),
        ]
    )

    with pytest.raises(ExportError, match="не найден"):
        export_to_gguf(args, stack=SimpleNamespace())


def test_export_rejects_failed_merge(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    stack = _fake_stack(captured, merged_model=None)
    # _fake_stack always builds a model; emulate merge() -> None explicitly.
    stack.PeftModel.from_pretrained = lambda *args: _FakePeftModel(
        object(), None
    )
    args = _args(tmp_path)

    with pytest.raises(ExportError, match="merge_and_unload"):
        export_to_gguf(
            args, stack=stack, converter_finder=lambda _: None
        )


def test_export_runs_converter_and_creates_gguf(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    converter = tmp_path / "llama.cpp" / "convert_hf_to_gguf.py"
    converter.parent.mkdir()
    converter.write_text("# fake", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs: Any) -> None:
        commands.append(command)
        Path(command[command.index("--outfile") + 1]).write_text(
            "GGUF", encoding="utf-8"
        )

    args = _args(tmp_path, llama_cpp_dir=converter.parent)
    result = export_to_gguf(
        args,
        stack=_fake_stack(captured),
        converter_finder=find_llama_cpp_converter,
        runner=runner,
    )

    assert result.gguf_path == tmp_path / "merged" / GGUF_FILENAME
    assert result.converter == converter
    assert result.gguf_created is True
    assert commands
    assert commands[0][1] == str(converter)


# --- CLI --------------------------------------------------------------------


def test_cli_requires_base_adapter_and_output() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([])

    assert exc_info.value.code == 2


def test_cli_defaults(tmp_path: Path) -> None:
    args = _args(tmp_path)

    assert args.gguf_outtype == DEFAULT_GGUF_OUTTYPE
    assert args.device == "cpu"
    assert args.llama_cpp_dir is None


def test_main_succeeds_without_llama_cpp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        gguf_module,
        "_import_export_stack",
        lambda: _fake_stack(captured),
    )
    monkeypatch.setattr(
        gguf_module, "find_llama_cpp_converter", lambda _: None
    )
    args = _args(tmp_path)
    argv = [
        "--base_model",
        args.base_model,
        "--adapter_path",
        str(args.adapter_path),
        "--output_dir",
        str(args.output_dir),
    ]

    code = main(argv)

    assert code == 0
    out = capsys.readouterr().out
    assert "HF-модель" in out
    assert "GGUF не создан" in out
    assert captured["merged_model"].saved_to == tmp_path / "merged"


def test_main_returns_1_for_missing_adapter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="export_to_gguf"):
        code = main(
            [
                "--base_model",
                BASE_MODEL,
                "--adapter_path",
                str(tmp_path / "missing"),
                "--output_dir",
                str(tmp_path / "merged"),
            ]
        )

    assert code == 1
    assert "не найден" in caplog.text
