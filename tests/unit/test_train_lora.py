"""LoRA-пайплайн: валидатор, маскирование labels, CLI (T-104)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import train_lora
from scripts.train_lora import (
    DEFAULT_OUTPUT,
    DEFAULT_TARGET_MODULES,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TooLongError,
    build_parser,
    collate_batch,
    dataset_stats,
    dataset_warnings,
    estimate_tokens,
    looks_like_fact_package,
    main,
    override_system_prompt,
    read_records,
    resolve_report_to,
    resolve_resume_checkpoint,
    schema_version_of,
    tokenize_example,
    train,
    validate_dataset,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_V1 = REPO_ROOT / "tests" / "fixtures" / "lora_sample.jsonl"
FIXTURE_V2 = REPO_ROOT / "tests" / "fixtures" / "lora_sample_v2.jsonl"

#: Текст, похожий на `build_fact_package` (эвристика v2).
FACT_PACKAGE_USER = (
    "# Партия\nБелые: A\nЧёрные: B\n\n"
    "# Ходы партии (SAN)\n1. e4 e5\n\n"
    "# Ключевые моменты (до 3, по убыванию потери)\n"
    "## 1. Ход 1 белых: e4 (полуход 1)\n"
)

MESSAGES: list[dict[str, str]] = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "u"},
    {"role": "assistant", "content": "a"},
]


def _write(path: Path, lines: list[object]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for line in lines:
            stream.write(json.dumps(line, ensure_ascii=False) + "\n")


def _record(
    roles: list[str] | None = None,
    *,
    level: str = "club",
    schema_version: int = SCHEMA_VERSION,
    user_content: str = "текст user",
    metadata: object = "auto",
) -> dict[str, Any]:
    roles = roles or ["system", "user", "assistant"]
    record: dict[str, Any] = {
        "messages": [
            {"role": role, "content": f"текст {role}"} for role in roles
        ],
        "schema_version": schema_version,
    }
    if roles == ["system", "user", "assistant"]:
        record["messages"][1]["content"] = user_content
    if schema_version == SCHEMA_VERSION:
        record["metadata"] = (
            {"level": level, "fact_sheet_chars": len(user_content)}
            if metadata == "auto"
            else metadata
        )
    else:
        record["level"] = level
    return record


class _FakeTokenizer:
    """Посимвольный tokenizer с префиксным chat-шаблоном (без torch)."""

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool = False,
        add_generation_prompt: bool = False,
    ) -> str:
        assert tokenize is False
        parts = [
            f"<{message['role']}>{message['content']}</{message['role']}>"
            for message in messages
        ]
        if add_generation_prompt:
            parts.append("<assistant>")
        return "".join(parts)

    def __call__(
        self, text: str, *, add_special_tokens: bool = False
    ) -> dict[str, list[int]]:
        assert add_special_tokens is False
        return {"input_ids": [ord(char) for char in text]}


class _NoTemplateTokenizer:
    """Tokenizer без chat-шаблона: проверяет текстовый фолбэк."""

    def __call__(
        self, text: str, *, add_special_tokens: bool = False
    ) -> dict[str, list[int]]:
        return {"input_ids": [ord(char) for char in text]}


# --- валидация --------------------------------------------------------------


def test_valid_dataset(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(
        path,
        [
            _record(user_content=FACT_PACKAGE_USER),
            _record(level="beginner", user_content=FACT_PACKAGE_USER),
        ],
    )

    count, errors = validate_dataset(path)

    assert count == 2
    assert errors == []


def test_legacy_v1_dataset_is_still_valid(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(
        path,
        [
            _record(schema_version=LEGACY_SCHEMA_VERSION, user_content="было"),
            _record(schema_version=LEGACY_SCHEMA_VERSION, user_content="было"),
        ],
    )

    count, errors = validate_dataset(path)

    assert count == 2
    assert errors == []


def test_mixed_schema_versions_are_valid(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(
        path,
        [
            _record(schema_version=LEGACY_SCHEMA_VERSION),
            _record(user_content=FACT_PACKAGE_USER),
        ],
    )

    count, errors = validate_dataset(path)

    assert count == 2
    assert errors == []


def test_legacy_dataset_without_system_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(path, [_record(["user", "assistant"])])

    count, errors = validate_dataset(path)

    assert count == 0
    assert len(errors) == 1
    assert "system→user→assistant" in errors[0]


@pytest.mark.parametrize(
    ("record", "fragment"),
    [
        ({"messages": []}, "messages"),
        ({"messages": "system"}, "messages"),
        (
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "assistant", "content": "a"},
                    {"role": "user", "content": "u"},
                ]
            },
            "system→user→assistant",
        ),
        (
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "  "},
                    {"role": "assistant", "content": "a"},
                ]
            },
            "пустой content",
        ),
        (
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ],
                "schema_version": 3,
            },
            "schema_version",
        ),
        (
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ],
                "schema_version": 2,
            },
            "metadata",
        ),
        (
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ],
                "schema_version": 2,
                "metadata": "not-an-object",
            },
            "metadata",
        ),
        (
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ],
                "schema_version": 2,
                "metadata": {"fact_sheet_chars": -1},
            },
            "fact_sheet_chars",
        ),
        (
            {
                "messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                    {"role": "critic", "content": "a"},
                ]
            },
            "роль",
        ),
    ],
)
def test_invalid_rows_are_reported(
    tmp_path: Path, record: object, fragment: str
) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(path, [record])

    count, errors = validate_dataset(path)

    assert count == 0
    assert len(errors) == 1
    assert fragment in errors[0]


def test_non_integer_schema_version_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    record = _record(user_content=FACT_PACKAGE_USER)
    record["schema_version"] = "2"
    _write(path, [record])

    count, errors = validate_dataset(path)

    assert count == 0
    assert "целым числом" in errors[0]


def test_broken_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    path.write_text('{"messages": [}\n', encoding="utf-8")

    count, errors = validate_dataset(path)

    assert count == 0
    assert "битый JSON" in errors[0]


def test_missing_file_is_error(tmp_path: Path) -> None:
    count, errors = validate_dataset(tmp_path / "nope.jsonl")

    assert count == 0
    assert errors


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    path.write_text(
        json.dumps(_record(), ensure_ascii=False) + "\n\n",
        encoding="utf-8",
    )

    count, errors = validate_dataset(path)

    assert count == 1
    assert errors == []


# --- fixture и read_records -------------------------------------------------


def test_sample_v1_fixture_matches_legacy_schema() -> None:
    count, errors = validate_dataset(FIXTURE_V1)

    assert errors == []
    assert count >= 3
    records = read_records(FIXTURE_V1)
    assert len(records) == count
    for record in records:
        assert [message["role"] for message in record["messages"]] == [
            "system",
            "user",
            "assistant",
        ]
        assert record["level"] == "club"
        assert record["schema_version"] == LEGACY_SCHEMA_VERSION


def test_sample_v2_fixture_matches_schema() -> None:
    count, errors = validate_dataset(FIXTURE_V2)

    assert errors == []
    assert count >= 3
    records = read_records(FIXTURE_V2)
    assert len(records) == count
    for record in records:
        assert [message["role"] for message in record["messages"]] == [
            "system",
            "user",
            "assistant",
        ]
        assert record["schema_version"] == SCHEMA_VERSION
        metadata = record["metadata"]
        assert metadata["level"] == "club"
        user = record["messages"][1]["content"]
        assert looks_like_fact_package(user)
        assert metadata["fact_sheet_chars"] == len(user)


def test_read_records_missing_file_explains_export() -> None:
    with pytest.raises(SystemExit) as exc_info:
        read_records(FIXTURE_V2.parent / "absent.jsonl")

    message = str(exc_info.value)
    assert "не найден" in message
    assert "export_dataset.py" in message
    assert "--schema-version 2" in message


def test_read_records_empty_file_is_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        read_records(path)

    assert "пуст" in str(exc_info.value)


def test_read_records_invalid_schema_is_error(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(path, [_record(["user", "assistant"])])

    with pytest.raises(SystemExit) as exc_info:
        read_records(path)

    message = str(exc_info.value)
    assert "невалиден" in message
    assert "system→user→assistant" in message


def test_read_system_prompt_requires_existing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="не найден"):
        train_lora.read_system_prompt(tmp_path / "absent.txt")


def test_read_system_prompt_rejects_empty_file(tmp_path: Path) -> None:
    prompt = tmp_path / "system.txt"
    prompt.write_text("   \n", encoding="utf-8")

    with pytest.raises(SystemExit, match="Пустой"):
        train_lora.read_system_prompt(prompt)


# --- маскирование labels ----------------------------------------------------


def test_tokenize_masks_system_and_user() -> None:
    tokenizer = _FakeTokenizer()

    encoded = tokenize_example(MESSAGES, tokenizer, max_length=512)

    prompt_text = tokenizer.apply_chat_template(
        MESSAGES[:-1], add_generation_prompt=True
    )
    full_text = tokenizer.apply_chat_template(
        MESSAGES, add_generation_prompt=False
    )
    assert encoded["input_ids"] == [ord(char) for char in full_text]
    assert len(encoded["labels"]) == len(encoded["input_ids"])
    prompt_length = len(prompt_text)
    assert encoded["labels"][:prompt_length] == [-100] * prompt_length
    assistant_labels = encoded["labels"][prompt_length:]
    assert assistant_labels == [ord(char) for char in "a</assistant>"]
    assert all(label != -100 for label in assistant_labels)


def test_tokenize_truncates_by_max_length() -> None:
    tokenizer = _FakeTokenizer()
    prompt_length = len(
        tokenizer.apply_chat_template(
            MESSAGES[:-1], add_generation_prompt=True
        )
    )

    encoded = tokenize_example(
        MESSAGES, tokenizer, max_length=prompt_length + 3
    )

    assert len(encoded["input_ids"]) == prompt_length + 3
    assert len(encoded["labels"]) == prompt_length + 3
    assert encoded["labels"][:prompt_length] == [-100] * prompt_length
    assert all(label != -100 for label in encoded["labels"][prompt_length:])


def test_tokenize_rejects_prompt_longer_than_max_length() -> None:
    tokenizer = _FakeTokenizer()
    prompt_length = len(
        tokenizer.apply_chat_template(
            MESSAGES[:-1], add_generation_prompt=True
        )
    )

    with pytest.raises(TooLongError, match="max_length"):
        tokenize_example(MESSAGES, tokenizer, max_length=prompt_length)


def test_tokenize_requires_assistant_answer() -> None:
    with pytest.raises(ValueError, match="assistant"):
        tokenize_example(MESSAGES[:-1], _FakeTokenizer())


def test_tokenize_works_without_chat_template() -> None:
    encoded = tokenize_example(MESSAGES, _NoTemplateTokenizer())

    full_text = "system: sys\nuser: u\nassistant: a"
    prompt_text = "system: sys\nuser: u\nassistant:"
    assert encoded["input_ids"] == [ord(char) for char in full_text]
    assert encoded["labels"][: len(prompt_text)] == [-100] * len(prompt_text)


class _FakeTensor:
    """Заглушка torch.Tensor для проверки collate_batch без torch."""

    def __init__(self, values: list[list[int]], dtype: object) -> None:
        self.values = values
        self.dtype = dtype


def test_collate_batch_returns_torch_tensors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_torch = SimpleNamespace(
        tensor=lambda values, dtype=None: _FakeTensor(values, dtype),
        long="torch.long",
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    batch = collate_batch(
        [
            {"input_ids": [1, 2], "labels": [-100, 5]},
            {"input_ids": [3], "labels": [7]},
        ],
        pad_token_id=0,
    )

    assert isinstance(batch["input_ids"], _FakeTensor)
    assert isinstance(batch["attention_mask"], _FakeTensor)
    assert isinstance(batch["labels"], _FakeTensor)
    assert batch["input_ids"].values == [[1, 2], [3, 0]]
    assert batch["attention_mask"].values == [[1, 1], [1, 0]]
    assert batch["labels"].values == [[-100, 5], [7, -100]]
    assert batch["input_ids"].dtype == "torch.long"


def test_collate_batch_rejects_empty_batch() -> None:
    with pytest.raises(ValueError, match="пустой батч"):
        collate_batch([], pad_token_id=0)


# --- статистика, версии схемы и system-prompt -------------------------------


def test_estimate_tokens_and_stats() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 40) == 10

    records = [
        _record(user_content=FACT_PACKAGE_USER),
        _record(level="beginner", user_content=FACT_PACKAGE_USER),
        _record(schema_version=LEGACY_SCHEMA_VERSION),
        {"messages": []},
    ]
    stats = dataset_stats(records[:2])

    assert stats.records == 2
    assert stats.levels == {"beginner": 1, "club": 1}
    assert stats.schema_versions == {"2": 2}
    assert stats.estimated_tokens > 0
    assert stats.max_record_tokens >= stats.estimated_tokens // 2


def test_dataset_stats_reads_level_from_v2_metadata() -> None:
    record = _record(level="gm", user_content=FACT_PACKAGE_USER)
    record.pop("level", None)
    assert "level" not in record

    stats = dataset_stats([record, record])

    assert stats.levels == {"gm": 2}
    assert stats.schema_versions == {"2": 2}


def test_dataset_stats_mixes_schema_versions() -> None:
    stats = dataset_stats(
        [
            _record(schema_version=LEGACY_SCHEMA_VERSION),
            _record(user_content=FACT_PACKAGE_USER),
        ]
    )

    assert stats.schema_versions == {"1": 1, "2": 1}


def test_supported_schema_versions_are_one_and_two() -> None:
    assert SUPPORTED_SCHEMA_VERSIONS == frozenset({1, 2})
    assert SCHEMA_VERSION == 2


def test_schema_version_of_defaults_to_legacy() -> None:
    assert schema_version_of({}) == LEGACY_SCHEMA_VERSION
    assert schema_version_of({"schema_version": None}) == LEGACY_SCHEMA_VERSION
    assert schema_version_of({"schema_version": 2}) == SCHEMA_VERSION


def test_schema_version_of_rejects_non_integer() -> None:
    with pytest.raises(ValueError, match="целым числом"):
        schema_version_of({"schema_version": "2"})


def test_looks_like_fact_package_heuristic() -> None:
    assert looks_like_fact_package(FACT_PACKAGE_USER)
    assert not looks_like_fact_package("исходный комментарий модели")
    assert not looks_like_fact_package("# Партия\nбез ключевых моментов")


def test_dataset_warnings_explain_v1_rewrite_semantics() -> None:
    warnings = dataset_warnings(
        [_record(schema_version=LEGACY_SCHEMA_VERSION)] * 2
    )

    assert len(warnings) == 1
    assert "schema_version=1" in warnings[0]
    assert "ПЕРЕПИСЫВАТЬ" in warnings[0]
    assert "--schema-version 2" in warnings[0]


def test_dataset_warnings_flag_v2_without_fact_package() -> None:
    warnings = dataset_warnings([_record(user_content="короткий текст")])

    assert len(warnings) == 1
    assert "не похож на пакет фактов" in warnings[0]


def test_dataset_warnings_are_empty_for_v2_fixture() -> None:
    assert dataset_warnings(read_records(FIXTURE_V2)) == []


def test_override_system_prompt_replaces_only_system() -> None:
    records = [read_records(FIXTURE_V2)[0]]
    original = records[0]["messages"]

    updated = override_system_prompt(records, "новый system")

    assert updated[0]["messages"][0] == {
        "role": "system",
        "content": "новый system",
    }
    assert updated[0]["messages"][1:] == original[1:]
    # v2: user — пакет фактов, переопределение system его не трогает.
    assert updated[0]["messages"][1]["content"] == original[1]["content"]
    # исходные записи не мутируются
    assert records[0]["messages"][0]["content"] != "новый system"


# --- гиперпараметры и model preparation -------------------------------------


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--dataset", "dataset.jsonl"])

    assert args.dataset == Path("dataset.jsonl")
    assert args.output_dir == DEFAULT_OUTPUT
    assert DEFAULT_OUTPUT.name == "lora-club-v1"
    assert args.epochs == 3.0
    assert args.lr == 2e-4
    assert args.lora_r == 16
    assert args.lora_alpha == 32
    assert args.target_modules == DEFAULT_TARGET_MODULES
    assert args.max_length == 2048
    assert args.load_in_4bit is True
    assert args.gradient_checkpointing is True
    assert args.dry_run is False
    assert args.report_to == "auto"
    assert args.resume_from_checkpoint is None
    assert args.skip_too_long is True


def test_parser_no_skip_too_long_disables_skipping() -> None:
    args = build_parser().parse_args(["--no-skip-too-long"])

    assert args.skip_too_long is False


def test_parser_no_4bit_turns_quantization_off() -> None:
    args = build_parser().parse_args(["--no-4bit"])

    assert args.load_in_4bit is False


def test_parser_rejects_conflicting_quantization_flags() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--load-in-4bit", "--no-4bit"])

    assert exc_info.value.code == 2


@pytest.mark.parametrize("value", ["0", "-1"])
def test_parser_rejects_non_positive_limit(value: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--limit", value])

    assert exc_info.value.code == 2


def test_parser_accepts_resume_checkpoint_and_report_to(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "checkpoint-500"
    checkpoint.mkdir()
    args = build_parser().parse_args(
        [
            "--resume_from_checkpoint",
            str(checkpoint),
            "--report_to",
            "tensorboard",
        ]
    )

    assert args.resume_from_checkpoint == checkpoint
    assert args.report_to == "tensorboard"


def test_parser_rejects_unknown_report_to() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--report_to", "mlflow"])

    assert exc_info.value.code == 2


def test_resolve_resume_checkpoint_none_and_directory(
    tmp_path: Path,
) -> None:
    assert resolve_resume_checkpoint(None) is None
    assert resolve_resume_checkpoint(tmp_path) == str(tmp_path)


def test_resolve_resume_checkpoint_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="Чекпоинт"):
        resolve_resume_checkpoint(tmp_path / "missing")


def _installed(*names: str) -> Any:
    def find_spec(name: str) -> object | None:
        return object() if name in names else None

    return find_spec


def test_resolve_report_to_none_disables_logging() -> None:
    assert resolve_report_to("none", find_spec=_installed()) == []


def test_resolve_report_to_auto_prefers_tensorboard() -> None:
    assert resolve_report_to(
        "auto", find_spec=_installed("tensorboard", "wandb")
    ) == ["tensorboard"]


def test_resolve_report_to_auto_falls_back_to_wandb() -> None:
    assert resolve_report_to(
        "auto", find_spec=_installed("wandb")
    ) == ["wandb"]


def test_resolve_report_to_auto_without_libraries() -> None:
    assert resolve_report_to("auto", find_spec=_installed()) == []


def test_resolve_report_to_explicit_installed() -> None:
    assert resolve_report_to(
        "tensorboard", find_spec=_installed("tensorboard")
    ) == ["tensorboard"]


def test_resolve_report_to_warns_when_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert resolve_report_to("wandb", find_spec=_installed()) == []
    assert "не установлен" in capsys.readouterr().err


class _FakeBitsAndBytesConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeLoraConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeBaseModel:
    def __init__(self) -> None:
        self.config = SimpleNamespace(use_cache=True)
        self.checkpointing_enabled = False
        self.require_grads_enabled = False

    def gradient_checkpointing_enable(self) -> None:
        self.checkpointing_enabled = True

    def enable_input_require_grads(self) -> None:
        self.require_grads_enabled = True


class _FakePeftModel:
    def __init__(self, base_model: _FakeBaseModel, lora: _FakeLoraConfig) -> None:
        self.base_model = base_model
        self.lora = lora

    def get_nb_trainable_parameters(self) -> tuple[int, int]:
        return 10, 100


def _fake_stack() -> Any:
    class _AutoModel:
        @staticmethod
        def from_pretrained(*args: Any, **kwargs: Any) -> _FakeBaseModel:
            return _FakeBaseModel()

    def _prepare(model: _FakeBaseModel) -> _FakeBaseModel:
        return model

    return SimpleNamespace(
        torch=SimpleNamespace(bfloat16="bfloat16"),
        AutoModelForCausalLM=_AutoModel,
        BitsAndBytesConfig=_FakeBitsAndBytesConfig,
        prepare_model_for_kbit_training=_prepare,
        LoraConfig=_FakeLoraConfig,
        get_peft_model=_FakePeftModel,
    )


def test_prepare_model_applies_lora_hyperparameters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = build_parser().parse_args(["--no-4bit"])

    model = train_lora._prepare_model(
        args, _fake_stack(), SimpleNamespace(bfloat16="bfloat16"), object()
    )

    assert isinstance(model, _FakePeftModel)
    assert model.lora.kwargs == {
        "r": 16,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "bias": "none",
        "task_type": "CAUSAL_LM",
        "target_modules": ["q_proj", "v_proj"],
    }
    assert model.base_model.checkpointing_enabled is True
    assert model.base_model.require_grads_enabled is True
    assert model.base_model.config.use_cache is False
    assert "trainable=10" in capsys.readouterr().out


def test_prepare_model_passes_4bit_quantization_config() -> None:
    captured: dict[str, Any] = {}

    def _from_pretrained(*args: Any, **kwargs: Any) -> _FakeBaseModel:
        captured.update(kwargs)
        return _FakeBaseModel()

    stack = _fake_stack()
    stack.AutoModelForCausalLM = SimpleNamespace(
        from_pretrained=_from_pretrained
    )
    args = build_parser().parse_args([])

    train_lora._prepare_model(
        args, stack, SimpleNamespace(bfloat16="bfloat16"), object()
    )

    quantization = captured["quantization_config"]
    assert isinstance(quantization, _FakeBitsAndBytesConfig)
    assert quantization.kwargs["load_in_4bit"] is True
    assert quantization.kwargs["bnb_4bit_quant_type"] == "nf4"


def test_oom_detection() -> None:
    assert train_lora._is_oom_error(RuntimeError("CUDA out of memory"))
    assert not train_lora._is_oom_error(RuntimeError("other failure"))
    assert not train_lora._is_oom_error(ValueError("CUDA out of memory"))


# --- ошибки окружения -------------------------------------------------------


def test_train_without_dependencies_exits_with_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail() -> Any:
        raise ImportError("No module named 'torch'")

    monkeypatch.setattr(train_lora, "_import_training_stack", _fail)
    args = build_parser().parse_args(["--dataset", "dataset.jsonl"])

    with pytest.raises(SystemExit) as exc_info:
        train(args, [])

    message = str(exc_info.value)
    assert "torch" in message
    assert "pip install" in message
    assert "LORA_TRAINING.md" in message


def test_train_without_cuda_exits_with_runpod_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_stack = SimpleNamespace(
        torch=SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False)
        )
    )
    monkeypatch.setattr(
        train_lora, "_import_training_stack", lambda: fake_stack
    )
    args = build_parser().parse_args(["--dataset", "dataset.jsonl"])

    with pytest.raises(SystemExit) as exc_info:
        train(args, [])

    message = str(exc_info.value)
    assert "CUDA" in message
    assert "RunPod" in message
    assert "--dry-run" in message


class _TrainTokenizer(_FakeTokenizer):
    """Токенизатор с pad/eos и сохранением (для полного ``train()``)."""

    pad_token: str | None = None
    eos_token = "</s>"
    pad_token_id = 0

    def __init__(self) -> None:
        self.saved_to: Path | None = None

    def save_pretrained(self, path: str) -> None:
        self.saved_to = Path(path)


class _FakeTrainModel:
    """Модель-заглушка для полного прогона ``train()``."""

    def __init__(self) -> None:
        self.saved_to: Path | None = None

    def save_pretrained(self, path: str) -> None:
        self.saved_to = Path(path)


class _FakeDataset:
    """Минимальный Dataset: from_list."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    @classmethod
    def from_list(cls, records: list[dict[str, Any]]) -> _FakeDataset:
        return cls(records)


def _fake_training_stack(captured: dict[str, Any]) -> Any:
    """Стек transformers/peft, записывающий аргументы Trainer."""

    class _TrainingArguments:
        def __init__(self, **kwargs: Any) -> None:
            captured["training_args"] = kwargs

    class _Trainer:
        def __init__(self, **kwargs: Any) -> None:
            captured["trainer"] = kwargs

        def train(self, **kwargs: Any) -> None:
            captured["train_kwargs"] = kwargs

    class _AutoTokenizer:
        @staticmethod
        def from_pretrained(*args: Any, **kwargs: Any) -> _TrainTokenizer:
            return _TrainTokenizer()

    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def get_device_name(index: int) -> str:
            return "Fake GPU"

        @staticmethod
        def is_bf16_supported() -> bool:
            return True

    return SimpleNamespace(
        torch=SimpleNamespace(
            cuda=_Cuda,
            version=SimpleNamespace(cuda="12.1"),
        ),
        AutoTokenizer=_AutoTokenizer,
        Dataset=_FakeDataset,
        TrainingArguments=_TrainingArguments,
        Trainer=_Trainer,
    )


def test_train_passes_resume_checkpoint_and_report_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = tmp_path / "checkpoint-500"
    checkpoint.mkdir()
    output = tmp_path / "lora-out"
    args = build_parser().parse_args(
        [
            "--dataset",
            "dataset.jsonl",
            "--output-dir",
            str(output),
            "--resume_from_checkpoint",
            str(checkpoint),
            "--report_to",
            "none",
        ]
    )
    captured: dict[str, Any] = {}
    model = _FakeTrainModel()
    monkeypatch.setattr(
        train_lora,
        "_import_training_stack",
        lambda: _fake_training_stack(captured),
    )
    monkeypatch.setattr(
        train_lora, "_prepare_model", lambda *args, **kwargs: model
    )

    train(args, [_record()])

    assert captured["train_kwargs"] == {
        "resume_from_checkpoint": str(checkpoint)
    }
    assert captured["training_args"]["report_to"] == []
    assert "logging_dir" not in captured["training_args"]
    assert "warmup_ratio" not in captured["training_args"]
    assert model.saved_to == output
    assert captured["trainer"]["train_dataset"] is not None
    assert len(captured["trainer"]["train_dataset"].records) == 1


def _too_long_record() -> dict[str, Any]:
    record = _record()
    record["messages"][0]["content"] = "system " * 200
    return record


def test_train_skips_too_long_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = build_parser().parse_args(
        [
            "--dataset",
            "dataset.jsonl",
            "--output-dir",
            str(tmp_path / "out"),
            "--max-length",
            "100",
            "--report_to",
            "none",
        ]
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        train_lora,
        "_import_training_stack",
        lambda: _fake_training_stack(captured),
    )
    monkeypatch.setattr(
        train_lora,
        "_prepare_model",
        lambda *args, **kwargs: _FakeTrainModel(),
    )

    train(args, [_too_long_record(), _record()])

    assert len(captured["trainer"]["train_dataset"].records) == 1
    out = capsys.readouterr().out
    assert "skipped_too_long" in out
    assert "Пропущено слишком длинных записей" in out


def test_train_without_skip_too_long_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = build_parser().parse_args(
        [
            "--dataset",
            "dataset.jsonl",
            "--output-dir",
            str(tmp_path / "out"),
            "--max-length",
            "100",
            "--no-skip-too-long",
            "--report_to",
            "none",
        ]
    )
    monkeypatch.setattr(
        train_lora,
        "_import_training_stack",
        lambda: _fake_training_stack({}),
    )

    with pytest.raises(SystemExit, match="max-length"):
        train(args, [_too_long_record()])


# --- CLI --------------------------------------------------------------------


def test_main_dry_run_prints_statistics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["--dry-run", "--dataset", str(FIXTURE_V2), "--limit", "2"]
    )

    assert code == 0
    captured = capsys.readouterr()
    assert "Записей: 2" in captured.out
    assert "club=2" in captured.out
    assert "Распределение по schema_version: 2=2" in captured.out
    assert "r=16" in captured.out
    assert "alpha=32" in captured.out
    assert "4bit=да" in captured.out
    assert "DRY-RUN OK" in captured.out
    # v2-фикстура корректна — предупреждений нет.
    assert "ВНИМАНИЕ" not in captured.out


def test_main_dry_run_warns_about_legacy_v1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["--dry-run", "--dataset", str(FIXTURE_V1), "--limit", "2"]
    )

    assert code == 0
    captured = capsys.readouterr()
    assert "Распределение по schema_version: 1=2" in captured.out
    assert "ВНИМАНИЕ: schema_version=1" in captured.out
    assert "ПЕРЕПИСЫВАТЬ" in captured.out


def test_main_dry_run_mixed_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "mixed.jsonl"
    _write(
        path,
        [
            _record(schema_version=LEGACY_SCHEMA_VERSION),
            _record(user_content=FACT_PACKAGE_USER),
        ],
    )

    code = main(["--dry-run", "--dataset", str(path)])

    assert code == 0
    captured = capsys.readouterr()
    assert "Распределение по schema_version: 1=1, 2=1" in captured.out
    assert "ВНИМАНИЕ: schema_version=1" in captured.out


def test_main_check_env_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--check-env"]) == 0

    captured = capsys.readouterr()
    assert "torch:" in captured.out
    assert "cuda:" in captured.out


def test_main_dry_run_with_system_prompt_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt = tmp_path / "system.txt"
    prompt.write_text("новый system", encoding="utf-8")

    code = main(
        [
            "--dry-run",
            "--dataset",
            str(FIXTURE_V2),
            "--limit",
            "1",
            "--system-prompt",
            str(prompt),
            "--no-4bit",
        ]
    )

    assert code == 0
    captured = capsys.readouterr()
    assert "System-промпт переопределён" in captured.out
    assert "4bit=нет" in captured.out


def test_main_dry_run_reports_invalid_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "dataset.jsonl"
    _write(path, [_record(["user", "assistant"])])

    with pytest.raises(SystemExit) as exc_info:
        main(["--dry-run", "--dataset", str(path)])

    assert "system→user→assistant" in str(exc_info.value)


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--help"])

    assert exc_info.value.code == 0
