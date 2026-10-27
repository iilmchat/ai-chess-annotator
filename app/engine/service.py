"""Async-обёртка над Stockfish (T-008).

Почему свой UCI-клиент, а не библиотека `stockfish`:

* нужны **PV-линии и `multipv`-ранги** — библиотека отдаёт только ходы и
  оценки, без principal variation;
* нужны **жёсткие таймауты** поиска (`movetime` + запас на чтение) и
  перезапуск процесса при зависании — `RULES.md` §8.2 требует таймауты;
* нужен **неблокирующий** event loop: `asyncio.create_subprocess_exec`
  работает напрямую, без `to_thread` и без GIL-конкуренции за одним
  процессом.

Один процесс Stockfish на `EngineService`; запросы сериализуются
`asyncio.Lock`, потому что UCI-сессия — однопоточная. При таймауте процесс
убивается и поднимается заново на следующем запросе.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final

import chess
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.engine.models import (
    MATE_CP_BASE,
    EngineLine,
    MoveAnalysis,
    MoveClassification,
)
from app.errors import (
    EngineError,
    EngineNotFoundError,
    EngineTimeoutError,
    InvalidLevelError,
    InvalidPgnError,
)
from app.ingestion.models import STARTING_FEN, ParsedMove
from app.ingestion.parser import parse_fen
from app.motifs.detector import detect_motifs

__all__ = [
    "ENGINE_PROFILES",
    "EngineProfile",
    "EngineService",
    "build_move_analyses",
    "prepare_positions",
    "resolve_profile",
]

#: Запас времени на чтение ответа поверх `movetime` (IPC, MultiPV-строки).
_READ_SLACK_S: Final[float] = 4.0
#: Сколько ждать `uciok` при старте процесса.
_UCI_STARTUP_S: Final[float] = 15.0
#: Сколько ждать `readyok` после смены опций.
_READY_S: Final[float] = 5.0
#: Сколько ждать завершения процесса при закрытии.
_SHUTDOWN_S: Final[float] = 2.0


@dataclass(frozen=True, slots=True)
class EngineProfile:
    """Параметры поиска для уровня ассистента (SPEC.md §2.2)."""

    depth: int
    multipv: int
    movetime_ms: int


#: Параметры по уровням — из SPEC.md §2.2 / §3.
ENGINE_PROFILES: Final[dict[str, EngineProfile]] = {
    "beginner": EngineProfile(depth=12, multipv=1, movetime_ms=200),
    "club": EngineProfile(depth=18, multipv=3, movetime_ms=1000),
    "gm": EngineProfile(depth=28, multipv=5, movetime_ms=5000),
}


def resolve_profile(level: str) -> EngineProfile:
    """Параметры поиска по имени уровня.

    Raises:
        InvalidLevelError: уровень не из ``beginner`` / ``club`` / ``gm``.
    """
    profile = ENGINE_PROFILES.get(level)
    if profile is None:
        expected = ", ".join(sorted(ENGINE_PROFILES))
        raise InvalidLevelError(
            f"Неизвестный уровень {level!r}; ожидается один из: {expected}"
        )
    return profile


class EngineService:
    """Сервис анализа позиций и партий через Stockfish.

    Пример::

        async with EngineService() as engine:
            lines = await engine.analyze(fen, "club")
            analyses = await engine.analyze_game_moves(game.moves, "club",
                                                       initial_fen=game.initial_fen)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        executable: Path | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._executable_override = executable
        self._engine: _UciEngine | None = None
        self._lock = asyncio.Lock()

    # --- публичный API ----------------------------------------------------

    @property
    def executable_path(self) -> Path:
        """Путь к бинарнику Stockfish.

        Raises:
            EngineNotFoundError: файла нет и в ``PATH`` его тоже нет.
        """
        if self._executable_override is not None:
            return self._executable_override
        return _resolve_stockfish_path(self._settings)

    async def analyze(
        self,
        fen: str,
        level: str,
        *,
        depth: int | None = None,
        multipv: int | None = None,
        movetime_ms: int | None = None,
    ) -> list[EngineLine]:
        """Проанализировать позицию и вернуть линии MultiPV.

        Args:
            fen: FEN позиции (валидируется через `app.ingestion.parse_fen`).
            level: ``beginner`` / ``club`` / ``gm`` — определяет глубину,
                MultiPV и лимит времени.
            depth: переопределить глубину (тесты, эксперименты).
            multipv: переопределить число линий.
            movetime_ms: переопределить лимит времени поиска.

        Returns:
            Линии, отсортированные по рангу MultiPV (первая — лучшая).

        Raises:
            InvalidFenError: FEN некорректен.
            InvalidLevelError: неизвестный уровень.
            EngineNotFoundError: Stockfish не найден.
            EngineTimeoutError: движок не уложился в лимит и был перезапущен.
        """
        board = parse_fen(fen)
        profile = resolve_profile(level)
        resolved_depth = profile.depth if depth is None else depth
        resolved_multipv = profile.multipv if multipv is None else multipv
        resolved_movetime = (
            profile.movetime_ms if movetime_ms is None else movetime_ms
        )
        if resolved_depth < 1:
            raise ValueError("depth должен быть ≥ 1")
        if resolved_multipv < 1:
            raise ValueError("multipv должен быть ≥ 1")
        if resolved_movetime < 1:
            raise ValueError("movetime_ms должен быть ≥ 1")

        async with self._lock:
            engine = await self._ensure_engine()
            return await engine.go(
                fen=board.fen(),
                depth=resolved_depth,
                multipv=resolved_multipv,
                movetime_ms=resolved_movetime,
            )

    @staticmethod
    def classify_move(
        delta_cp: int, is_best: bool = False
    ) -> MoveClassification:
        """Классифицировать ход по потере в сантипешках (SPEC.md §2.2).

        ``delta_cp ≤ 0`` или лучший ход → ``best``; далее пороги
        ``< 50`` / ``< 100`` / ``≤ 200`` / ``> 200``.
        """
        if is_best or delta_cp <= 0:
            return "best"
        if delta_cp < 50:
            return "good"
        if delta_cp < 100:
            return "inaccuracy"
        if delta_cp <= 200:
            return "mistake"
        return "blunder"

    async def analyze_game_moves(
        self,
        moves: Sequence[ParsedMove],
        level: str,
        *,
        initial_fen: str | None = None,
    ) -> list[MoveAnalysis]:
        """Прогнать партию через движок и оценить каждый полуход.

        Каждая позиция (до первого хода, после каждого хода) анализируется
        один раз: ``N + 1`` вызовов движка на ``N`` полуходов. Здесь поиски
        идут **последовательно** (один процесс); для параллельного анализа
        партии используйте `app.engine.pool.EnginePool`.

        Args:
            moves: полуходы из `app.ingestion.parse_pgn`.
            level: уровень ассистента.
            initial_fen: стартовая позиция партии; по умолчанию — начальная
                расстановка. Для партий с FEN-стартом передайте
                ``ParsedGame.initial_fen``.

        Returns:
            Список `MoveAnalysis` в порядке ходов.

        Raises:
            InvalidPgnError: ход нелегален в позиции или история ходов
                расходится с FEN-ами из `ParsedMove`.
            EngineError: движок вернул недопустимый ход.
        """
        if not moves:
            return []

        boards, played_uci = prepare_positions(moves, initial_fen=initial_fen)
        lines_by_position = [
            await self.analyze(position.fen(), level) for position in boards
        ]
        return build_move_analyses(
            moves,
            boards,
            played_uci,
            lines_by_position,
            classify=self.classify_move,
        )

    async def aclose(self) -> None:
        """Остановить процесс движка (безопасно вызывать повторно)."""
        async with self._lock:
            engine = self._engine
            self._engine = None
            if engine is not None:
                await engine.close()

    async def __aenter__(self) -> EngineService:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # --- внутреннее -------------------------------------------------------

    async def _ensure_engine(self) -> _UciEngine:
        if self._engine is not None and self._engine.is_running:
            return self._engine

        engine = _UciEngine(self.executable_path)
        try:
            await engine.start()
        except BaseException:
            await engine.close()
            raise
        self._engine = engine
        return engine


class _UciEngine:
    """Минимальный UCI-клиент: один процесс, один поиск за раз."""

    def __init__(self, executable: Path) -> None:
        self._executable = executable
        self._process: asyncio.subprocess.Process | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                str(self._executable),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            self._process = None
            raise EngineNotFoundError(
                f"Не удалось запустить Stockfish ({self._executable}): {exc}"
            ) from exc
        await self._send("uci")
        await self._expect("uciok", timeout_s=_UCI_STARTUP_S)

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.write(b"quit\n")
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_S)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def go(
        self, *, fen: str, depth: int, multipv: int, movetime_ms: int
    ) -> list[EngineLine]:
        if not self.is_running:
            raise EngineError("Движок не запущен")

        await self._send("ucinewgame")
        await self._send(f"setoption name MultiPV value {multipv}")
        await self._send(f"position fen {fen}")
        await self._send("isready")
        await self._expect("readyok", timeout_s=_READY_S)
        await self._send(f"go depth {depth} movetime {movetime_ms}")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + movetime_ms / 1000 + _READ_SLACK_S
        lines: dict[int, EngineLine] = {}
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(self._readline(), timeout=remaining)
                if line.startswith("bestmove"):
                    break
                if not line.startswith("info "):
                    continue
                parsed = _parse_info_line(line)
                if parsed is not None:
                    lines[parsed.multipv] = parsed
        except TimeoutError as exc:
            # Зависший процесс нельзя «дожать» — убиваем; следующий запрос
            # поднимет новый (EngineService._ensure_engine).
            await self.close()
            raise EngineTimeoutError(
                f"Stockfish не уложился в {movetime_ms} мс (+{_READ_SLACK_S:.0f} с "
                "на чтение) и был перезапущен"
            ) from exc

        if not lines:
            raise EngineError(f"Движок не вернул оценок для позиции {fen!r}")
        return [lines[rank] for rank in sorted(lines)]

    async def _send(self, command: str) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise EngineError("stdin движка закрыт")
        try:
            process.stdin.write((command + "\n").encode("ascii"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise EngineError(f"Движок разорвал соединение: {exc}") from exc

    async def _readline(self) -> str:
        process = self._require_process()
        if process.stdout is None:
            raise EngineError("stdout движка закрыт")
        raw = await process.stdout.readline()
        if raw == b"":
            raise EngineError("Stockfish неожиданно завершился (EOF)")
        return raw.decode("utf-8", errors="replace").strip()

    async def _expect(self, token: str, *, timeout_s: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise EngineTimeoutError(
                    f"Движок не ответил {token!r} за {timeout_s:.1f} с"
                )
            line = await asyncio.wait_for(self._readline(), timeout=remaining)
            if line == token:
                return

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise EngineError("Движок не запущен")
        return self._process


def _resolve_stockfish_path(settings: Settings) -> Path:
    """Найти бинарник Stockfish: `STOCKFISH_PATH`, затем ``PATH``."""
    raw = settings.stockfish_path.strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
        found = shutil.which(raw)
        if found:
            return Path(found)

    hint = ""
    if settings.stockfish_host:
        hint = " Задан STOCKFISH_HOST, но TCP-режим движка пока не реализован (KI-013)."
    raise EngineNotFoundError(
        f"Stockfish не найден: {raw!r}. Укажите STOCKFISH_PATH (KI-006).{hint}"
    )


def _parse_info_line(line: str) -> EngineLine | None:
    """Разобрать строку ``info ... multipv K depth D score cp X pv ...``."""
    tokens = line.split()
    if not {"multipv", "depth", "score", "pv"} <= set(tokens):
        return None
    try:
        multipv = int(tokens[tokens.index("multipv") + 1])
        depth = int(tokens[tokens.index("depth") + 1])
        score_at = tokens.index("score")
        score_kind = tokens[score_at + 1]
        score_value = int(tokens[score_at + 2])
        pv = tokens[tokens.index("pv") + 1 :]
    except (IndexError, ValueError):
        return None
    if not pv or score_kind not in {"cp", "mate"}:
        return None

    try:
        return EngineLine(
            multipv=multipv,
            depth=depth,
            score_cp=score_value if score_kind == "cp" else None,
            mate=score_value if score_kind == "mate" else None,
            move_uci=pv[0],
            pv=pv,
        )
    except ValidationError:
        return None


def _white_cp(line: EngineLine, turn: chess.Color) -> int:
    """Оценка линии с точки зрения белых (UCI отдаёт её за сторону хода)."""
    if line.mate is not None:
        sign = 1 if line.mate > 0 else -1
        cp = sign * (MATE_CP_BASE - 100 * min(abs(line.mate), 99))
    else:
        cp = line.score_cp or 0
    return cp if turn == chess.WHITE else -cp


def prepare_positions(
    moves: Sequence[ParsedMove], *, initial_fen: str | None = None
) -> tuple[list[chess.Board], list[str]]:
    """Восстановить доски (до первого хода и после каждого) и UCI ходов.

    Сверяет результат с ``ParsedMove.fen``: расхождение означает, что
    история ходов и ``initial_fen`` несовместимы.

    Returns:
        ``(boards, played_uci)``: ``len(boards) == len(moves) + 1``.

    Raises:
        InvalidPgnError: ход нелегален или история расходится с FEN-ами.
    """
    board = chess.Board(parse_fen(initial_fen or STARTING_FEN).fen())
    boards: list[chess.Board] = [board.copy()]
    played_uci: list[str] = []

    for index, parsed in enumerate(moves, start=1):
        try:
            move = board.parse_san(parsed.san)
        except ValueError as exc:
            raise InvalidPgnError(
                f"Ход {parsed.san!r} нелегален в позиции "
                f"{board.fen()!r} (полуход {index})"
            ) from exc
        board.push(move)
        if board.fen() != parsed.fen:
            raise InvalidPgnError(
                f"История ходов расходится на полуходе {index}: ожидался "
                f"{parsed.fen!r}, получен {board.fen()!r}"
            )
        played_uci.append(move.uci())
        boards.append(board.copy())

    return boards, played_uci


def build_move_analyses(
    moves: Sequence[ParsedMove],
    boards: Sequence[chess.Board],
    played_uci: Sequence[str],
    lines_by_position: Sequence[Sequence[EngineLine]],
    *,
    classify: Callable[[int, bool], MoveClassification],
) -> list[MoveAnalysis]:
    """Собрать `MoveAnalysis` из готовых линий движка (по позициям).

    Отдельная функция — чтобы и последовательный `EngineService`
    (`analyze_game_moves`), и `EnginePool` использовали одну и ту же
    арифметику дельт/классификации.

    Raises:
        EngineError: движок вернул недопустимый ход.
    """
    white_scores = [
        _white_cp(lines[0], position.turn)
        for lines, position in zip(lines_by_position, boards, strict=True)
    ]

    analyses: list[MoveAnalysis] = []
    for index, parsed in enumerate(moves, start=1):
        before = boards[index - 1]
        mover_sign = 1 if before.turn == chess.WHITE else -1
        eval_before = white_scores[index - 1] * mover_sign
        eval_after = white_scores[index] * mover_sign
        delta_cp = max(0, eval_before - eval_after)

        best = lines_by_position[index - 1][0]
        try:
            best_move = before.parse_uci(best.move_uci)
            best_san = before.san(best_move)
        except ValueError as exc:
            raise EngineError(
                f"Движок вернул недопустимый ход {best.move_uci!r} "
                f"для позиции {before.fen()!r}"
            ) from exc

        after_line = lines_by_position[index][0]
        mate_after = None
        if after_line.mate is not None:
            mate_after = (
                after_line.mate
                if boards[index].turn == chess.WHITE
                else -after_line.mate
            )

        is_best = played_uci[index - 1] == best.move_uci
        lines_before = list(lines_by_position[index - 1])
        motifs = detect_motifs(
            boards[index].fen(), list(lines_by_position[index])
        )
        analyses.append(
            MoveAnalysis(
                ply=parsed.ply,
                played_move_san=parsed.san,
                played_move_uci=played_uci[index - 1],
                eval_before=eval_before,
                eval_after=eval_after,
                best_move_uci=best.move_uci,
                best_move_san=best_san,
                delta_cp=delta_cp,
                classification=classify(delta_cp, is_best),
                engine_depth=best.depth,
                mate_after=mate_after,
                lines=lines_before,
                motifs=motifs,
            )
        )
    return analyses
