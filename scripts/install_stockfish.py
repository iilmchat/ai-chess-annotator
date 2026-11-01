"""Установка Stockfish 17.1 в ``./bin/stockfish`` (Windows/Linux/macOS x86_64).

Зачем: движок запускается локальным процессом по ``STOCKFISH_PATH``
(``app.engine.service._resolve_stockfish_path``). В Docker это
``/usr/bin/stockfish``, а на dev-машине бинарника может не быть — скрипт
скачивает официальный релиз с GitHub и раскладывает его рядом с проектом.

Алгоритм:

1. определить ОС и архитектуру (Windows, Linux, macOS x86_64; на Apple
   Silicon x86-64-сборка запускается через Rosetta 2);
2. скачать AVX2-архив релиза ``sf_17.1`` (zip для Windows, tar для
   Linux/macOS) во временный каталог рядом с ``bin/``;
3. безопасно распаковать его в ``bin/stockfish`` (защита от path traversal;
   NNUE-сеть встроена в бинарник начиная с Stockfish 16);
4. проверить бинарник UCI-рукопожатием (``uci`` → ``id name``);
5. прописать абсолютный путь в ``.env`` как ``STOCKFISH_PATH``.

Запуск::

    python scripts/install_stockfish.py              # установить + .env
    python scripts/install_stockfish.py --force      # переустановить
    python scripts/install_stockfish.py --no-env     # не трогать .env
    python scripts/install_stockfish.py --no-verify  # без запуска движка
    python scripts/install_stockfish.py --json       # машинный отчёт

В stdout печатается только абсолютный путь к бинарнику (удобно для
``$(...)``), прогресс и предупреждения — в stderr. ``--json`` печатает
объект с полями ``path``/``version``/``platform``/``arch``/``source``/
``env_updated``/``env_file``.

Exit codes: ``0`` — успех; ``1`` — ошибка установки/проверки.

Примечание: ассеты релиза собраны с AVX2; на CPU без AVX2 бинарник
завершится с ошибкой нелегальной инструкции — скрипт сообщит об этом.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_DEST",
    "DEFAULT_ENV_FILE",
    "MARKER_NAME",
    "VERSION",
    "InstallError",
    "Target",
    "UnsupportedPlatformError",
    "build_parser",
    "detect_target",
    "install_stockfish",
    "main",
    "update_env",
]

#: Корень проекта (``scripts/install_stockfish.py`` → ``..``).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Куда по умолчанию раскладывается бинарник (в ``.gitignore``).
DEFAULT_DEST = PROJECT_ROOT / "bin" / "stockfish"

#: Какой ``.env`` обновляется по умолчанию.
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

#: Тег релиза Stockfish на GitHub.
VERSION = "sf_17.1"

#: База ассетов релиза.
RELEASE_BASE = "https://github.com/official-stockfish/Stockfish/releases/download"

#: Файл-маркер установленной версии внутри ``bin/stockfish``.
MARKER_NAME = ".stockfish-version"

#: Коды возврата CLI.
EXIT_OK = 0
EXIT_ERROR = 1

_USER_AGENT = "ai-chess-annotator-stockfish-installer/1.0"

#: Нормализация имён архитектур из ``platform.machine()``/``uname -m``.
_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "x86_64": "x86_64",
    "x86-64": "x86_64",
    "arm64": "arm64",
    "aarch64": "arm64",
}

#: Допустимые расширения бинарника (Linux/macOS — без расширения).
_EXECUTABLE_SUFFIXES = {"", ".exe"}

#: Служебный мусор архивов (macOS/Windows).
_ARCHIVE_JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db"}

_ENV_KEY_RE = re.compile(r"^[ \t]*(?:export[ \t]+)?STOCKFISH_PATH[ \t]*=", re.IGNORECASE)


class InstallError(RuntimeError):
    """Установка невозможна: сеть, архив, права или несовместимая платформа."""


class UnsupportedPlatformError(InstallError):
    """ОС/архитектура не покрыта ассетами релиза Stockfish 17.1."""


@dataclass(frozen=True, slots=True)
class Asset:
    """Ассет релиза: имя файла и прямая ссылка."""

    archive_name: str
    url: str


#: Ассеты ``sf_17.1`` для поддерживаемых ОС (сборки AVX2, x86_64).
ASSETS: dict[str, Asset] = {
    "windows": Asset(
        "stockfish-windows-x86-64-avx2.zip",
        f"{RELEASE_BASE}/{VERSION}/stockfish-windows-x86-64-avx2.zip",
    ),
    "linux": Asset(
        "stockfish-ubuntu-x86-64-avx2.tar",
        f"{RELEASE_BASE}/{VERSION}/stockfish-ubuntu-x86-64-avx2.tar",
    ),
    "macos": Asset(
        "stockfish-macos-x86-64-avx2.tar",
        f"{RELEASE_BASE}/{VERSION}/stockfish-macos-x86-64-avx2.tar",
    ),
}


@dataclass(frozen=True, slots=True)
class Target:
    """Разрешённая платформа и соответствующий ассет."""

    os_name: str
    arch: str
    archive_name: str
    url: str
    rosetta: bool = False

    @property
    def executable_name(self) -> str:
        """Имя бинарника: архив без расширения (+ ``.exe`` на Windows)."""
        stem = self.archive_name.removesuffix(".zip").removesuffix(".tar")
        return f"{stem}.exe" if self.os_name == "windows" else stem

    @property
    def label(self) -> str:
        """Человекочитаемая платформа для логов."""
        if self.rosetta:
            return "macOS/arm64 (x86-64 через Rosetta 2)"
        return f"{self.os_name}/{self.arch}"


def _log(message: str) -> None:
    """Прогресс — в stderr, чтобы stdout оставался чистым (путь)."""
    print(message, file=sys.stderr, flush=True)


def _format_size(size: int) -> str:
    """Размер в удобных единицах (для прогресса загрузки)."""
    value = float(size)
    for unit in ("Б", "КиБ", "МиБ", "ГиБ"):
        if value < 1024 or unit == "ГиБ":
            return f"{int(value)} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГиБ"  # pragma: no cover - недостижимо


def detect_target(system: str | None = None, machine: str | None = None) -> Target:
    """Определить ОС/архитектуру и вернуть ассет для скачивания.

    Args:
        system: значение ``platform.system()`` (тесты подставляют своё).
        machine: значение ``platform.machine()``.

    Returns:
        ``Target`` с именем архива, URL и именем бинарника.

    Raises:
        UnsupportedPlatformError: ОС не Windows/Linux/macOS или архитектура
            не x86_64 (исключение — macOS arm64 через Rosetta 2).
    """
    raw_system = (system if system is not None else platform.system()).strip()
    raw_machine = (machine if machine is not None else platform.machine()).strip()
    name = raw_system.lower()

    if name.startswith("win"):
        os_name = "windows"
    elif name == "linux":
        os_name = "linux"
    elif name == "darwin":
        os_name = "macos"
    else:
        raise UnsupportedPlatformError(
            f"неподдерживаемая ОС: {raw_system!r}; нужны Windows, Linux или macOS"
        )

    arch = _ARCH_ALIASES.get(raw_machine.lower(), raw_machine.lower())
    rosetta = os_name == "macos" and arch == "arm64"
    if arch != "x86_64" and not rosetta:
        raise UnsupportedPlatformError(
            f"неподдерживаемая архитектура: {raw_machine!r} ({arch}); "
            f"для {raw_system} есть только сборка x86_64/AVX2"
        )

    asset = ASSETS[os_name]
    return Target(
        os_name=os_name,
        arch=arch,
        archive_name=asset.archive_name,
        url=asset.url,
        rosetta=rosetta,
    )


def _download_once(request: urllib.request.Request, dest: Path, *, timeout: float) -> None:
    """Одна попытка скачивания с прогрессом (10 шагов)."""
    with urllib.request.urlopen(request, timeout=timeout) as response, dest.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        step = max(total // 10, 1)
        next_mark = step
        done = 0
        while chunk := response.read(1 << 20):
            handle.write(chunk)
            done += len(chunk)
            if total and (done >= next_mark or done >= total):
                percent = min(done * 100 // total, 100)
                _log(f"  загрузка: {percent}% ({_format_size(done)} из {_format_size(total)})")
                next_mark += step
        if not total:
            _log(f"  загрузка: {_format_size(done)}")


def download(
    url: str,
    dest: Path,
    *,
    timeout: float = 60.0,
    attempts: int = 3,
) -> None:
    """Скачать ``url`` в ``dest`` с повторами на временных ошибках.

    Args:
        url: прямая ссылка на ассет GitHub.
        dest: локальный файл (родитель создаётся).
        timeout: таймаут сокета, секунды.
        attempts: число попыток (≥ 1).

    Raises:
        InstallError: HTTP 4xx или исчерпаны попытки.
    """
    if attempts < 1:
        raise ValueError("attempts должен быть >= 1")
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            _download_once(request, dest, timeout=timeout)
            return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise InstallError(
                    f"HTTP {exc.code} при скачивании {url}; проверьте доступность релиза {VERSION}"
                ) from exc
            error = exc
        except (urllib.error.URLError, OSError) as exc:
            error = exc
        dest.unlink(missing_ok=True)
        if attempt < attempts:
            delay = min(2.0**attempt, 10.0)
            _log(f"  попытка {attempt}/{attempts}: {error}; повтор через {delay:.0f} с")
            time.sleep(delay)

    raise InstallError(f"не удалось скачать {url}: {error}")


def _ensure_within(path: Path, root: Path) -> None:
    """Не дать элементу архива выйти за пределы каталога распаковки."""
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise InstallError(f"небезопасный путь в архиве: {path}") from exc


def _extract_zip(archive: Path, dest: Path) -> None:
    """Распаковать zip без path traversal и без симлинков."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("__MACOSX/") or Path(name).name in _ARCHIVE_JUNK:
                continue
            target = dest / name
            _ensure_within(target, dest)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            # Симлинк в zip (S_IFLNK) пропускаем: payload Stockfish их не содержит.
            if stat.S_ISLNK(info.external_attr >> 16):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


def _extract_tar(archive: Path, dest: Path) -> None:
    """Распаковать tar; ``filter='data'`` — защита от traversal/спецфайлов."""
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")


def _extract(archive: Path, dest: Path) -> None:
    """Определить формат по содержимому и распаковать."""
    if zipfile.is_zipfile(archive):
        _extract_zip(archive, dest)
    elif tarfile.is_tarfile(archive):
        _extract_tar(archive, dest)
    else:
        raise InstallError(f"неизвестный формат архива: {archive.name}")


def _payload_dir(root: Path) -> Path:
    """Каталог с файлами движка: единственная папка верхнего уровня или корень."""
    entries = [p for p in root.iterdir() if p.name not in _ARCHIVE_JUNK]
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if not p.is_dir()]
    if len(dirs) == 1 and not files:
        return dirs[0]
    return root


def _find_executable(root: Path, expected: str) -> Path:
    """Найти бинарник по ожидаемому имени, иначе — по префиксу ``stockfish``."""
    candidate = root / expected
    if candidate.is_file():
        return candidate

    candidates = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name.lower().startswith("stockfish")
        and path.suffix.lower() in _EXECUTABLE_SUFFIXES
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise InstallError(f"в архиве не найден бинарник {expected!r} (искали stockfish* в {root})")
    names = ", ".join(sorted(p.name for p in candidates))
    raise InstallError(f"в архиве несколько кандидатов на бинарник: {names}; ожидался {expected!r}")


def _uci_engine_name(output: str) -> str | None:
    """Извлечь ``id name`` из UCI-ответа движка."""
    for line in output.splitlines():
        if line.lower().startswith("id name "):
            return line[len("id name ") :].strip()
    return None


def verify_binary(path: Path, *, timeout: float = 30.0) -> str:
    """Проверить, что файл запускается и отвечает как UCI-движок Stockfish.

    Args:
        path: бинарник Stockfish.
        timeout: сколько ждать рукопожатия.

    Returns:
        ``id name`` движка (например, ``Stockfish 17.1``).

    Raises:
        InstallError: файл не запускается, упал или не ответил UCI.
    """
    try:
        proc = subprocess.run(
            [str(path)],
            input="uci\nquit\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallError(f"Stockfish не ответил на UCI за {timeout:.0f} с: {path}") from exc
    except OSError as exc:
        hint = ""
        if sys.platform == "darwin" and platform.machine().lower() in {
            "arm64",
            "aarch64",
        }:
            hint = " Установите Rosetta 2: softwareupdate --install-rosetta"
        raise InstallError(f"не удалось запустить {path}: {exc}.{hint}") from exc

    if proc.returncode != 0:
        hint = " Код 0xC000001D (незаконная инструкция) означает, что CPU не поддерживает AVX2."
        raise InstallError(f"Stockfish завершился с кодом {proc.returncode}: {path}.{hint}")

    name = _uci_engine_name(proc.stdout)
    if name is None:
        snippet = (proc.stdout or proc.stderr or "").strip()[:300]
        raise InstallError(f"файл {path} не отвечает как UCI-движок Stockfish: {snippet!r}")
    return name


def _read_marker(path: Path) -> str:
    """Прочитать маркер установленной версии (пустая строка, если нет)."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _looks_like_install(destination: Path, expected: str) -> bool:
    """Похож ли непустой каталог на прежнюю установку Stockfish."""
    if (destination / MARKER_NAME).is_file():
        return True
    if (destination / expected).is_file():
        return True
    return any(
        path.is_file() and path.name.lower().startswith("stockfish")
        for path in destination.iterdir()
    )


def install_stockfish(
    target: Target,
    *,
    dest: Path = DEFAULT_DEST,
    force: bool = False,
    verify: bool = True,
    timeout: float = 60.0,
    archive: Path | None = None,
) -> Path:
    """Скачать/распаковать Stockfish и вернуть абсолютный путь к бинарнику.

    Args:
        target: результат :func:`detect_target`.
        dest: каталог установки (по умолчанию ``bin/stockfish``).
        force: переустановить, даже если маркер версии совпадает.
        verify: запустить UCI-рукопожатие после распаковки.
        timeout: таймаут скачивания и проверки, секунды.
        archive: локальный архив вместо скачивания (offline/тесты).

    Returns:
        Абсолютный путь к исполняемому файлу.

    Raises:
        InstallError: скачивание/распаковка/проверка не удались или
            ``dest`` занят посторонними файлами.
    """
    destination = dest.expanduser().resolve()
    executable = destination / target.executable_name
    marker = destination / MARKER_NAME

    if not force and executable.is_file() and _read_marker(marker) == VERSION:
        _log(f"Stockfish {VERSION} уже установлен: {executable}")
        return executable

    if destination.exists() and not destination.is_dir():
        raise InstallError(f"путь установки занят файлом: {destination}")
    if (
        destination.is_dir()
        and any(destination.iterdir())
        and not _looks_like_install(destination, target.executable_name)
    ):
        raise InstallError(
            f"каталог {destination} не пуст и не похож на установку Stockfish; "
            "очистите его или укажите другой --dest"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".stockfish-install-", dir=destination.parent))
    try:
        if archive is not None:
            archive_path = Path(archive).expanduser().resolve()
            if not archive_path.is_file():
                raise InstallError(f"архив не найден: {archive_path}")
            _log(f"Использую локальный архив: {archive_path}")
        else:
            archive_path = staging / target.archive_name
            _log(f"Скачиваю Stockfish {VERSION} для {target.label}:")
            _log(f"  {target.url}")
            download(target.url, archive_path, timeout=timeout)
            _log(f"  скачано: {_format_size(archive_path.stat().st_size)}")

        extract_root = staging / "extract"
        extract_root.mkdir()
        _log(f"Распаковываю {archive_path.name} …")
        _extract(archive_path, extract_root)

        payload = _payload_dir(extract_root)
        binary = _find_executable(payload, target.executable_name)
        if target.os_name != "windows":
            binary.chmod(binary.stat().st_mode | 0o111)

        if verify:
            engine_name = verify_binary(binary, timeout=min(timeout, 30.0))
            _log(f"  UCI: {engine_name}")

        relative_binary = binary.relative_to(payload)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(payload, destination)

        marker.write_text(f"{VERSION}\n", encoding="utf-8")
        executable = destination / relative_binary
        _log(f"Установлено: {executable}")
        return executable
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _env_path_value(executable: Path) -> str:
    """Значение для ``.env``: POSIX-слэши, кавычки при спецсимволах."""
    value = executable.as_posix()
    if any(char in value for char in ' \t#$"'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def update_env(env_file: Path, executable: Path) -> bool:
    """Прописать абсолютный путь к Stockfish в ``.env`` как ``STOCKFISH_PATH``.

    Существующая строка заменяется (дубликаты удаляются), отсутствующая —
    добавляется в конец. Остальные строки и переводы строк сохраняются.

    Args:
        env_file: путь к ``.env`` (создаётся, если его нет).
        executable: путь к бинарнику Stockfish.

    Returns:
        ``True``, если файл изменён; ``False`` — значение уже актуально.
    """
    env_file = Path(env_file).expanduser().resolve()
    original = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
    newline = "\r\n" if "\r\n" in original else "\n"
    value = _env_path_value(executable)

    lines = original.splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if _ENV_KEY_RE.match(line):
            if not replaced:
                out.append(f"STOCKFISH_PATH={value}")
                replaced = True
            continue
        out.append(line)

    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append("# Stockfish 17.1 (scripts/install_stockfish.py)")
        out.append(f"STOCKFISH_PATH={value}")

    text = newline.join(out) + newline
    if text == original:
        return False

    env_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = env_file.with_name(f".{env_file.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, env_file)
    return True


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер установщика."""
    parser = argparse.ArgumentParser(
        prog="install_stockfish.py",
        description=(
            "Скачать Stockfish 17.1 с GitHub, распаковать в bin/stockfish "
            "и прописать STOCKFISH_PATH в .env."
        ),
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        metavar="DIR",
        help=f"каталог установки (по умолчанию {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        metavar="FILE",
        help=f".env для STOCKFISH_PATH (по умолчанию {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="не изменять .env (только установить бинарник)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="переустановить, даже если версия уже совпадает",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="не запускать бинарник для UCI-проверки",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        metavar="SEC",
        help="таймаут скачивания и проверки в секундах (по умолчанию 60)",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        metavar="FILE",
        help="локальный архив вместо скачивания (offline-установка)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="машинный вывод (JSON) вместо пути",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: установить и напечатать абсолютный путь."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    env_file = Path(args.env_file).expanduser().resolve()

    try:
        target = detect_target()
        _log(f"Платформа: {target.label}")
        if target.rosetta:
            _log("Предупреждение: Apple Silicon, сборка x86_64 — нужен Rosetta 2.")
        executable = install_stockfish(
            target,
            dest=Path(args.dest),
            force=bool(args.force),
            verify=not bool(args.no_verify),
            timeout=float(args.timeout),
            archive=args.archive,
        )
        env_updated = False
        if not args.no_env:
            env_updated = update_env(env_file, executable)
            if env_updated:
                _log(f"Обновлён {env_file}: STOCKFISH_PATH={_env_path_value(executable)}")
            else:
                _log(f"{env_file}: STOCKFISH_PATH уже актуален")
    except InstallError as exc:
        print(f"Ошибка установки Stockfish: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("Установка прервана пользователем.", file=sys.stderr)
        return 130

    if args.json:
        print(
            json.dumps(
                {
                    "path": str(executable),
                    "version": VERSION,
                    "platform": target.os_name,
                    "arch": target.arch,
                    "source": "local" if args.archive is not None else "github",
                    "env_updated": env_updated,
                    "env_file": str(env_file),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(executable)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
