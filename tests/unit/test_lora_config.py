"""LoRA-адаптер в конфиге и фабрике клиента (T-104)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.llm import get_llm_client


def _adapter_dir(tmp_path: Path) -> Path:
    adapter = tmp_path / "lora-club-v1"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(
        '{"r": 16, "lora_alpha": 32}', encoding="utf-8"
    )
    return adapter


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


# --- Settings ---------------------------------------------------------------


def test_lora_model_uses_adapter_basename(tmp_path: Path) -> None:
    cfg = Settings(lora_adapter_path=str(_adapter_dir(tmp_path)))

    assert cfg.lora_adapter_available is True
    assert cfg.lora_model == "lora-club-v1"


def test_lora_model_accepts_adapter_file(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter_model.safetensors"
    adapter.write_bytes(b"stub")

    cfg = Settings(lora_adapter_path=str(adapter))

    assert cfg.lora_adapter_available is True
    assert cfg.lora_model == "adapter_model.safetensors"


def test_lora_model_requires_existing_path(tmp_path: Path) -> None:
    cfg = Settings(lora_adapter_path=str(tmp_path / "missing-adapter"))

    assert cfg.lora_adapter_available is False
    assert cfg.lora_model == cfg.vllm_model


def test_lora_model_ignores_directory_without_adapter_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "not-an-adapter").mkdir()

    cfg = Settings(lora_adapter_path=str(tmp_path / "not-an-adapter"))

    assert cfg.lora_adapter_available is False
    assert cfg.lora_model == cfg.vllm_model


@pytest.mark.parametrize("value", [None, "", "   "])
def test_lora_model_defaults_to_base_model(value: str | None) -> None:
    cfg = Settings(lora_adapter_path=value)

    assert cfg.lora_adapter_dir is None
    assert cfg.lora_adapter_available is False
    assert cfg.lora_model == cfg.vllm_model


def test_lora_model_trailing_separator_is_normalized(tmp_path: Path) -> None:
    adapter = _adapter_dir(tmp_path)

    cfg = Settings(lora_adapter_path=str(adapter) + "\\")

    assert cfg.lora_model == "lora-club-v1"


# --- factory ----------------------------------------------------------------


async def test_local_client_uses_lora_model(tmp_path: Path) -> None:
    cfg = Settings(lora_adapter_path=str(_adapter_dir(tmp_path)))
    client = get_llm_client("club", cfg)
    try:
        assert client.model == "lora-club-v1"
        assert client.provider == "lmstudio"
    finally:
        await client.aclose()


async def test_local_client_without_adapter_uses_base_model() -> None:
    cfg = Settings()
    client = get_llm_client("beginner", cfg)
    try:
        assert client.model == cfg.vllm_model
    finally:
        await client.aclose()


async def test_gm_ignores_lora_adapter(tmp_path: Path) -> None:
    cfg = Settings(
        lora_adapter_path=str(_adapter_dir(tmp_path)),
        deepseek_api_key="test-key",
    )
    client = get_llm_client("gm", cfg)
    try:
        assert client.provider == "deepseek"
        assert client.model == cfg.deepseek_model
    finally:
        await client.aclose()


async def test_factory_logs_enabled_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter_dir(tmp_path)
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.factory.logger", recorder)
    cfg = Settings(lora_adapter_path=str(adapter))

    client = get_llm_client("club", cfg)
    await client.aclose()

    assert recorder.events == [
        (
            "llm_lora_adapter_enabled",
            {
                "level": "club",
                "adapter_path": str(adapter),
                "model": "lora-club-v1",
            },
        )
    ]


async def test_factory_warns_about_missing_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.factory.logger", recorder)
    missing = tmp_path / "missing-adapter"
    cfg = Settings(lora_adapter_path=str(missing))

    client = get_llm_client("club", cfg)
    await client.aclose()

    assert len(recorder.events) == 1
    event, fields = recorder.events[0]
    assert event == "llm_lora_adapter_unavailable"
    assert fields["adapter_path"] == str(missing)
    assert fields["model"] == cfg.vllm_model
    assert client.model == cfg.vllm_model
