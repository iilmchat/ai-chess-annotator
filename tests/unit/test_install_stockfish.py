"""Unit-тесты установщика Stockfish (``scripts/install_stockfish.py``).

Без сети: архивы собираются во временном каталоге, проверка UCI отключена.
Покрыты: определение платформы, zip/tar-распаковка, защита от path
traversal, идемпотентность установки и обновление ``.env``.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.install_stockfish import (
    MARKER_NAME,
    VERSION,
    InstallError,
    UnsupportedPlatformError,
    detect_target,
    install_stockfish,
    update_env,
)


def _make_zip(archive: Path, member: str, payload: bytes = b"fake-binary") -> Path:
    """Собрать zip с одним файлом по указанному пути внутри архива."""
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, payload)
    return archive


def _make_tar(archive: Path, member: str, payload: bytes = b"fake-binary") -> Path:
    """Собрать tar с одним исполняемым файлом."""
    info = tarfile.TarInfo(member)
    info.size = len(payload)
    info.mode = 0o755
    with tarfile.open(archive, "w") as tf:
        tf.addfile(info, io.BytesIO(payload))
    return archive


# --- определение платформы -------------------------------------------------


@pytest.mark.parametrize(
    ("system", "machine", "os_name", "archive_name"),
    [
        ("Windows", "AMD64", "windows", "stockfish-windows-x86-64-avx2.zip"),
        ("Linux", "x86_64", "linux", "stockfish-ubuntu-x86-64-avx2.tar"),
        ("Darwin", "x86_64", "macos", "stockfish-macos-x86-64-avx2.tar"),
    ],
)
def test_detect_target_maps_supported_platforms(
    system: str, machine: str, os_name: str, archive_name: str
) -> None:
    target = detect_target(system, machine)

    assert target.os_name == os_name
    assert target.arch == "x86_64"
    assert target.archive_name == archive_name
    assert target.url.endswith(f"/{VERSION}/{archive_name}")
    assert target.rosetta is False


def test_detect_target_windows_gets_exe_suffix() -> None:
    target = detect_target("Windows", "AMD64")

    assert target.executable_name == "stockfish-windows-x86-64-avx2.exe"


def test_detect_target_macos_arm64_allowed_via_rosetta() -> None:
    target = detect_target("Darwin", "arm64")

    assert target.arch == "arm64"
    assert target.rosetta is True
    assert "Rosetta" in target.label


@pytest.mark.parametrize(("system", "machine"), [("Linux", "aarch64"), ("Windows", "ARM64")])
def test_detect_target_rejects_non_x86_64(system: str, machine: str) -> None:
    with pytest.raises(UnsupportedPlatformError, match="архитектура"):
        detect_target(system, machine)


def test_detect_target_rejects_unknown_os() -> None:
    with pytest.raises(UnsupportedPlatformError, match="ОС"):
        detect_target("FreeBSD", "x86_64")


# --- установка -------------------------------------------------------------


def test_install_from_zip_is_idempotent(tmp_path: Path) -> None:
    target = detect_target("Windows", "AMD64")
    archive = _make_zip(tmp_path / target.archive_name, f"stockfish/{target.executable_name}")
    dest = tmp_path / "bin" / "stockfish"

    executable = install_stockfish(target, dest=dest, archive=archive, verify=False)

    assert executable == (dest / target.executable_name).resolve()
    assert executable.is_file()
    assert executable.read_bytes() == b"fake-binary"
    assert (dest / MARKER_NAME).read_text(encoding="utf-8").strip() == VERSION

    # Повторный запуск не требует архива и ничего не переустанавливает.
    archive.unlink()
    assert install_stockfish(target, dest=dest, verify=False) == executable


def test_install_from_tar_for_linux(tmp_path: Path) -> None:
    target = detect_target("Linux", "x86_64")
    archive = _make_tar(tmp_path / target.archive_name, f"stockfish/{target.executable_name}")
    dest = tmp_path / "bin" / "stockfish"

    executable = install_stockfish(target, dest=dest, archive=archive, verify=False)

    assert executable.is_file()
    assert executable.name == target.executable_name


def test_install_rejects_path_traversal(tmp_path: Path) -> None:
    target = detect_target("Windows", "AMD64")
    archive = _make_zip(tmp_path / target.archive_name, "stockfish/../../evil.exe")
    dest = tmp_path / "bin" / "stockfish"

    with pytest.raises(InstallError, match="небезопасный путь"):
        install_stockfish(target, dest=dest, archive=archive, verify=False)

    assert not (tmp_path / "evil.exe").exists()
    assert not (tmp_path / "bin" / "evil.exe").exists()


def test_install_refuses_unrelated_non_empty_dest(tmp_path: Path) -> None:
    target = detect_target("Windows", "AMD64")
    archive = _make_zip(tmp_path / target.archive_name, f"stockfish/{target.executable_name}")
    dest = tmp_path / "bin" / "stockfish"
    dest.mkdir(parents=True)
    important = dest / "important.txt"
    important.write_text("не удалять", encoding="utf-8")

    with pytest.raises(InstallError, match="не пуст"):
        install_stockfish(target, dest=dest, archive=archive, verify=False)

    assert important.read_text(encoding="utf-8") == "не удалять"


def test_install_missing_archive_reports_error(tmp_path: Path) -> None:
    target = detect_target("Windows", "AMD64")

    with pytest.raises(InstallError, match="архив не найден"):
        install_stockfish(
            target,
            dest=tmp_path / "bin" / "stockfish",
            archive=tmp_path / "missing.zip",
            verify=False,
        )


# --- .env ------------------------------------------------------------------


def _stockfish_line(text: str) -> str:
    """Первая строка ``STOCKFISH_PATH=...`` из текста .env."""
    return next(line for line in text.splitlines() if line.startswith("STOCKFISH_PATH="))


def test_update_env_replaces_existing_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=dev\nSTOCKFISH_PATH=/usr/bin/stockfish\nLOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    executable = tmp_path / "bin" / "stockfish" / "stockfish.exe"

    assert update_env(env_file, executable) is True

    text = env_file.read_text(encoding="utf-8")
    assert "APP_ENV=dev" in text
    assert "LOG_LEVEL=INFO" in text
    assert "/usr/bin/stockfish" not in text
    assert _stockfish_line(text) == f"STOCKFISH_PATH={executable.as_posix()}"
    # Идемпотентность: второй вызов ничего не меняет.
    assert update_env(env_file, executable) is False


def test_update_env_appends_missing_key(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=dev\n", encoding="utf-8")
    executable = tmp_path / "bin" / "stockfish"

    assert update_env(env_file, executable) is True

    text = env_file.read_text(encoding="utf-8")
    assert text.startswith("APP_ENV=dev\n")
    assert _stockfish_line(text) == f"STOCKFISH_PATH={executable.as_posix()}"


def test_update_env_quotes_path_with_spaces(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=dev\n", encoding="utf-8")
    executable = tmp_path / "my tools" / "stockfish"

    update_env(env_file, executable)

    line = _stockfish_line(env_file.read_text(encoding="utf-8"))
    assert line.startswith('STOCKFISH_PATH="')
    assert line.endswith('"')
    assert "my tools" in line


def test_update_env_removes_duplicates(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STOCKFISH_PATH=/old/one\nAPP_ENV=dev\nSTOCKFISH_PATH=/old/two\n",
        encoding="utf-8",
    )
    executable = tmp_path / "bin" / "stockfish"

    update_env(env_file, executable)

    lines = [
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("STOCKFISH_PATH=")
    ]
    assert lines == [f"STOCKFISH_PATH={executable.as_posix()}"]
