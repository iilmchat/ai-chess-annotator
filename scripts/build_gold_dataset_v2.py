"""Генерация «золотого» датасета v2 сильной моделью DeepSeek (T-104b).

Почему новый скрипт, а не ``--provider deepseek`` в
:mod:`scripts.build_gold_dataset`: у v1 два ограничения, которые для
v0.2.0-стратегии неприемлемы:

* учителем была та же локальная 4B-модель, что и целевая, — датасет нёс её
  галлюцинации и «дистиллировал слабую модель» (KI-030);
* не проверялся алфавит ответа — языковой дрейф 7B (KI-060) не отсекался.

Скрипт v2 поддерживает два режима:

**1. PGN (по умолчанию).** Читает ``data/gold_pgns/*.pgn`` (оценки
``[%eval]`` уже есть от Lichess), собирает
:func:`~app.llm.prompts.build_fact_package` с лимитом ``max_tokens=2048``
(T-095), отправляет системный промпт уровня ``club`` в DeepSeek
(``deepseek-chat``, ключ — ``Settings.deepseek_api_key``), требует
**только текст комментария**, валидирует SAN-ходы и CJK и пишет
``schema_version=2`` JSONL.

**2. Positions.** С ``--positions data-chess/positions.jsonl`` читает
FEN из JSONL (``fen``, ``evaluation``, ``best_move``; синонимы полей
поддерживаются, ``best_move`` — SAN или UCI, как в
``generate_positions.py``), строит **упрощённый пакет фактов** — только
текущая позиция и оценка, без истории ходов, — и генерирует комментарий
по той же схеме.

Общее для режимов: нелегальный SAN-ход — retry, китайские иероглифы
(KI-060) — ровно один retry «отвечай только по-русски», затем вырезание
предложений или пропуск записи.

Запуск::

    python scripts/build_gold_dataset_v2.py --dry-run --limit 10
    python scripts/build_gold_dataset_v2.py --limit 1000
    python scripts/build_gold_dataset_v2.py --positions data-chess/positions.jsonl --limit 1000

``--dry-run`` собирает пакеты фактов и печатает статистику, **не вызывая**
DeepSeek и не создавая файл.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import chess  # noqa: E402
import chess.pgn  # noqa: E402
import httpx  # noqa: E402

from app.commentary.semantic import (  # noqa: E402
    cjk_warning,
    has_cjk,
    sanitize_cjk,
)
from app.config import Settings, get_settings  # noqa: E402
from app.dataset.export import load_system_prompt  # noqa: E402
from app.engine.models import MATE_CP_BASE  # noqa: E402
from app.engine.validator import validate_llm_response  # noqa: E402
from app.errors import (  # noqa: E402
    InvalidFenError,
    InvalidPgnError,
    LLMError,
    LLMNotConfiguredError,
)
from app.ingestion import ParsedGame, parse_fen, parse_pgn  # noqa: E402
from app.llm.base import BaseLLMClient  # noqa: E402
from app.llm.openai_client import OpenAICompatibleClient  # noqa: E402
from app.llm.prompts import (  # noqa: E402
    MAX_FACT_PACKAGE_TOKENS,
    build_fact_package,
)
from scripts.build_gold_dataset import (  # noqa: E402
    DEFAULT_INPUT_DIR,
    analyses_from_evals,
    build_record,
    export_game_pgn,
    extract_eval_points,
    iter_games,
    write_records,
)

__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_OUTPUT",
    "ONLY_COMMENTARY_INSTRUCTION",
    "POSITION_INSTRUCTION",
    "SKIP_REASON_LABELS",
    "GenerationOutcome",
    "GoldV2Config",
    "GoldV2Result",
    "PositionItem",
    "V2Stats",
    "build_client",
    "build_parser",
    "build_position_fact_package",
    "build_position_record",
    "build_retry_prompt",
    "build_system_prompt",
    "build_v2_record",
    "generate_for_game",
    "generate_with_validation",
    "main",
    "parse_position_record",
    "read_positions",
    "run_build",
]

#: Куда писать датасет v2 (не перезаписывает v1).
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "gold_dataset_v2.jsonl"
#: Целевое число записей.
DEFAULT_LIMIT = 1000
#: Сколько раз переспрашивать учителя при нелегальных ходах/иероглифах.
DEFAULT_MAX_RETRIES = 2
#: Модель-учитель по умолчанию (берётся из `Settings`, если не переопределена).
DEFAULT_MODEL = "deepseek-chat"

#: Требование к формату ответа: только текст, без вступлений и разметки.
ONLY_COMMENTARY_INSTRUCTION = (
    "Верни ТОЛЬКО текст комментария на русском языке: без вступлений, "
    "пояснений, заголовков, списков, Markdown и служебных фраз вроде "
    "«Вот комментарий». Начни сразу с содержательного текста."
)

#: Дополнение к системному промпту для режима изолированных позиций.
POSITION_INSTRUCTION = (
    "Тебе дана изолированная позиция без истории партии. Комментируй только "
    "её: характер позиции, активность фигур, слабые поля и план. Не выдумывай "
    "предыдущие ходы, дебют и ключевые моменты — их в пакете нет."
)

#: Причины пропуска → подпись для сводки.
SKIP_REASON_LABELS: dict[str, str] = {
    "invalid_pgn": "битый PGN",
    "no_evals": "нет [%eval]",
    "invalid_evals": "оценки не совпали с ходами",
    "invalid_llm": "учитель не прошёл валидатор",
    "cjk_rejected": "ответ с иероглифами отклонён (KI-060)",
    "invalid_position": "битая позиция (JSON/FEN)",
}

#: Ключи JSONL позиций: FEN, оценка и лучший ход (сосед готовит датасет).
_FEN_KEYS = ("fen", "position")
_EVAL_KEYS = ("evaluation", "eval", "eval_cp", "cp", "score", "score_cp")
_MATE_KEYS = ("mate", "eval_mate")
_BEST_MOVE_KEYS = ("best_move", "bestmove", "best_move_san", "move", "san")
_ID_KEYS = ("id", "position_id")

#: Строковое число: `1.2`, `+0.35`, `-120` (cp определяется по ключу/величине).
_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
#: Мат в строке: `#3`, `#-3`, `M3`, `-M3`.
_MATE_RE = re.compile(r"^(?:#|m)([+-]?\d+)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PositionItem:
    """Одна изолированная позиция из ``positions.jsonl``.

    Attributes:
        fen: нормализованный FEN (после проверки ``parse_fen``).
        evaluation: человекочитаемая оценка (``+1.20``, ``#3``) или ``None``.
        eval_cp: оценка в сантипешках за белых (если была).
        eval_mate: мат в N (если был).
        best_move: SAN лучшего хода (если он легален в позиции).
        source_id: id записи для ``metadata.id``.
    """

    fen: str
    evaluation: str | None
    eval_cp: int | None
    eval_mate: int | None
    best_move: str | None
    source_id: str


@dataclass(frozen=True, slots=True)
class GoldV2Config:
    """Параметры генерации датасета v2."""

    input_dir: Path = DEFAULT_INPUT_DIR
    output: Path = DEFAULT_OUTPUT
    limit: int = DEFAULT_LIMIT
    level: str = "club"
    max_tokens: int = MAX_FACT_PACKAGE_TOKENS
    max_retries: int = DEFAULT_MAX_RETRIES
    dry_run: bool = False
    model: str | None = None
    #: Режим 2: JSONL изолированных позиций (FEN, best_move, evaluation).
    positions: Path | None = None


@dataclass
class V2Stats:
    """Счётчики генерации: файлы, партии, записи, retry и пропуски."""

    files: int = 0
    games: int = 0
    positions: int = 0
    converted: int = 0
    prepared: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    assistant_chars: int = 0
    fact_sheet_chars: int = 0
    eval_points: int = 0
    api_calls: int = 0
    retries: int = 0
    cjk_retries: int = 0
    cjk_stripped_sentences: int = 0

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    @property
    def average_assistant_chars(self) -> int:
        return (
            self.assistant_chars // self.converted if self.converted else 0
        )

    def record_skipped(self, reason: str) -> None:
        self.skipped[reason] += 1

    def record_converted(self, chars: int, eval_points: int) -> None:
        self.converted += 1
        self.assistant_chars += chars
        self.eval_points += eval_points

    def record_attempts(self, outcome: GenerationOutcome) -> None:
        """Учесть API-вызовы, retry и очистку CJK по результату генерации."""
        self.api_calls += outcome.attempts
        self.retries += max(0, outcome.attempts - 1)
        self.cjk_retries += outcome.cjk_retries
        self.cjk_stripped_sentences += outcome.cjk_stripped_sentences


@dataclass(frozen=True, slots=True)
class GoldV2Result:
    """Итог генерации."""

    output: Path
    stats: V2Stats
    #: ``pgn`` или ``positions`` — какой режим использовался.
    mode: str = "pgn"

    def summary_lines(self) -> list[str]:
        stats = self.stats
        title = (
            "Золотой датасет v2 (DeepSeek Teacher, positions): "
            "schema_version=2"
            if self.mode == "positions"
            else "Золотой датасет v2 (DeepSeek Teacher): schema_version=2"
        )
        lines = [title]
        if self.mode == "positions":
            lines.append(f"Позиций: {stats.positions}")
        else:
            lines.append(f"PGN-файлов: {stats.files}")
            lines.append(f"Партий: {stats.games}")
        if stats.prepared:
            lines.append(f"Пакетов фактов (dry-run): {stats.prepared}")
            lines.append("Записей: 0 (dry-run, файл не пишется)")
        else:
            lines.append(f"Записей: {stats.converted}")
        lines.append(f"API-вызовов: {stats.api_calls}")
        lines.append(f"  retry: {stats.retries}")
        lines.append(f"  CJK-retry: {stats.cjk_retries}")
        lines.append(
            f"  вырезано предложений с иероглифами: "
            f"{stats.cjk_stripped_sentences}"
        )
        lines.append(f"Пропущено: {stats.skipped_total}")
        for reason, count in sorted(stats.skipped.items()):
            lines.append(f"  {SKIP_REASON_LABELS.get(reason, reason)}: {count}")
        if stats.converted:
            lines.append(
                "Средняя длина assistant: "
                f"{stats.average_assistant_chars} символов"
            )
        prefix = "Файл (будет создан)" if stats.prepared else "Файл"
        lines.append(f"{prefix}: {self.output}")
        return lines


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """Результат диалога с учителем по одной партии.

    Attributes:
        text: принятый текст комментария (пустой при ``ok=False``).
        ok: запись можно писать в датасет.
        attempts: сколько API-вызовов сделано.
        skip_reason: ключ :data:`SKIP_REASON_LABELS` при ``ok=False``.
        cjk_retries: сколько раз переспрашивали из-за иероглифов.
        cjk_stripped_sentences: сколько предложений вырезано на последней
            попытке.
    """

    text: str
    ok: bool
    attempts: int
    skip_reason: str | None = None
    cjk_retries: int = 0
    cjk_stripped_sentences: int = 0


def build_system_prompt(level: str = "club", *, positions: bool = False) -> str:
    """Системный промпт уровня + требование «только текст комментария».

    Args:
        level: ``beginner`` / ``club`` / ``gm`` (файл промпта).
        positions: добавить инструкцию для изолированной позиции (режим 2):
            в пакете нет истории ходов и ключевых моментов.

    Returns:
        Текст системного сообщения для DeepSeek.
    """
    parts = [load_system_prompt(level)]
    if positions:
        parts.append(POSITION_INSTRUCTION)
    parts.append(ONLY_COMMENTARY_INSTRUCTION)
    return "\n\n".join(parts)


def _format_cp(cp: int) -> str:
    """Сантипешки → ``+1.20`` / ``-0.35`` (оценка за белых)."""
    sign = "+" if cp >= 0 else "-"
    return f"{sign}{abs(cp) / 100:.2f}"


def _parse_mate(value: Any) -> tuple[int | None, int | None, str | None]:
    """Разобрать мат: ``3``, ``"#3"``, ``"#-2"``, ``"M3"``."""
    if value is None or isinstance(value, bool):
        return None, None, None
    if isinstance(value, str):
        text = value.strip()
        match = _MATE_RE.match(text)
        if match is not None:
            mate = int(match.group(1))
        else:
            try:
                mate = int(text)
            except ValueError:
                return None, None, None
    elif isinstance(value, (int, float)):
        mate = int(value)
    else:
        return None, None, None
    if mate == 0:
        return None, None, None
    sign = -1 if mate < 0 else 1
    cp = sign * (MATE_CP_BASE - 100 * abs(mate))
    return cp, mate, f"#{mate}"


def _parse_eval_value(
    value: Any, key: str
) -> tuple[int | None, int | None, str | None]:
    """Нормализовать оценку: cp, мат и человекочитаемую строку.

    Поддерживаются числа и строки. Единицы определяются **по ключу**:
    ``eval_cp``/``cp``/``score_cp`` — сантипешки; ``evaluation``/``eval``/
    ``score`` — пауны (умножаются на 100). Так `generate_positions.py`
    соседа пишет ``evaluation: 1.2`` (пауны) и ``evaluation: 100.0`` для
    мата. Объект Lichess ``{"type": "cp"|"mate", "value": ...}`` тоже
    поддерживается.
    """
    if value is None or isinstance(value, bool):
        return None, None, None
    if isinstance(value, Mapping):
        kind = str(value.get("type", "")).strip().lower()
        inner = value.get("value")
        if kind == "mate":
            return _parse_mate(inner)
        if kind == "cp":
            return _parse_eval_value(inner, "cp")
        return _parse_eval_value(inner, key)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None, None
        if text.startswith("#") or _MATE_RE.match(text):
            return _parse_mate(text)
        if not _NUMBER_RE.match(text):
            return None, None, None
        number: float = float(text)
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None, None, None

    if key in {"eval_cp", "cp", "score_cp"}:
        cp = round(number)
    else:
        cp = round(number * 100)
    return cp, None, _format_cp(cp)


def _first_field(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _parse_evaluation(
    data: Mapping[str, Any],
) -> tuple[int | None, int | None, str | None]:
    """Оценка позиции: сначала ``mate``, затем cp/пауны/строка."""
    raw_mate = _first_field(data, _MATE_KEYS)
    if raw_mate is not None:
        return _parse_mate(raw_mate)
    for key in _EVAL_KEYS:
        if key not in data:
            continue
        cp, mate, display = _parse_eval_value(data[key], key)
        if cp is not None or mate is not None:
            return cp, mate, display
    return None, None, None


def parse_position_record(
    raw: Any, index: int, *, source: str = "positions"
) -> PositionItem:
    """Разобрать одну JSONL-запись позиции.

    Ожидаемый формат (лишние поля игнорируются)::

        {"fen": "...", "best_move": "Nf3", "evaluation": 1.2}

    Допускаются синонимы: ``position`` вместо ``fen``; ``eval``/``eval_cp``/
    ``cp``/``score`` вместо ``evaluation``; ``mate``; ``bestmove``/
    ``best_move_san``/``move``/``san`` вместо ``best_move``.

    Args:
        raw: распарсенная JSON-строка.
        index: номер строки в файле (1-based) — для id и сообщений.
        source: имя файла для ``source_id``.

    Returns:
        :class:`PositionItem` с проверенным FEN.

    Raises:
        ValueError: запись не объект, нет FEN или FEN некорректен.
    """
    if not isinstance(raw, Mapping):
        raise ValueError(f"строка {index}: ожидается JSON-объект")
    data = {str(key).lower(): value for key, value in raw.items()}
    fen_raw = _first_field(data, _FEN_KEYS)
    if not isinstance(fen_raw, str) or not fen_raw.strip():
        raise ValueError(f"строка {index}: нет поля fen")
    try:
        board = parse_fen(fen_raw)
    except InvalidFenError as exc:
        raise ValueError(f"строка {index}: {exc}") from exc

    best_move: str | None = None
    best_raw = _first_field(data, _BEST_MOVE_KEYS)
    if isinstance(best_raw, str) and best_raw.strip():
        candidate = best_raw.strip()
        move: chess.Move | None = None
        try:
            move = board.parse_san(candidate)
        except ValueError:
            try:
                # `generate_positions.py` пишет best_move в UCI (e2e4).
                move = board.parse_uci(candidate)
            except ValueError:
                move = None
        if move is not None:
            best_move = board.san(move)

    cp, mate, evaluation = _parse_evaluation(data)
    source_id_raw = _first_field(data, _ID_KEYS)
    source_id = (
        str(source_id_raw)
        if source_id_raw not in (None, "")
        else f"{source}:{index}"
    )
    return PositionItem(
        fen=board.fen(),
        evaluation=evaluation,
        eval_cp=cp,
        eval_mate=mate,
        best_move=best_move,
        source_id=source_id,
    )


def read_positions(path: Path) -> Iterator[tuple[int, PositionItem | None]]:
    """Прочитать ``positions.jsonl``; ``None`` — битая строка.

    Пустые строки пропускаются. Ошибки JSON/FEN не прерывают чтение: запись
    отдаётся как ``None``, а вызывающий считает её в ``invalid_position``.
    """
    with path.open("r", encoding="utf-8") as stream:
        for index, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                item = parse_position_record(raw, index, source=path.name)
            except (json.JSONDecodeError, ValueError):
                yield index, None
                continue
            yield index, item


def build_position_fact_package(item: PositionItem) -> str:
    """Упрощённый пакет фактов: только позиция, оценка и лучший ход.

    В отличие от :func:`~app.llm.prompts.build_fact_package`, здесь нет
    истории ходов, ключевых моментов и рекомендаций Policy — данных о
    партии просто не существует.

    Args:
        item: проверенная позиция из ``positions.jsonl``.

    Returns:
        Текст пакета фактов для DeepSeek.
    """
    board = parse_fen(item.fen)
    side = "белые" if board.turn == chess.WHITE else "чёрные"
    lines = [
        "# Позиция",
        f"FEN: {item.fen}",
        f"Ход: {side}",
    ]
    if item.evaluation:
        lines.append(f"Оценка движка: {item.evaluation} (за белых)")
    else:
        lines.append("Оценка движка: нет")
    if item.best_move:
        lines.append(f"Лучший ход: {item.best_move}")
    lines.append("История ходов недоступна: в пакете только эта позиция.")
    return "\n".join(lines)


def build_position_record(
    *,
    item: PositionItem,
    fact_sheet: str,
    text: str,
    system_prompt: str,
    level: str,
    model: str,
) -> dict[str, Any]:
    """Собрать v2-запись для изолированной позиции."""
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": fact_sheet},
            {"role": "assistant", "content": text},
        ],
        "schema_version": 2,
        "metadata": {
            "id": item.source_id,
            "commentary_id": None,
            "game_id": None,
            "editor_id": None,
            "level": level,
            "model": model,
            "created_at": None,
            "fact_sheet_chars": len(fact_sheet),
            "assistant_chars": len(text),
            "eval_points": 1 if item.evaluation else 0,
            "source": "positions",
            "dataset": "gold_v2",
            "teacher_provider": "deepseek",
            "fen": item.fen,
            "evaluation": item.evaluation,
            "best_move": item.best_move,
        },
    }


def build_client(
    settings: Settings,
    *,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> OpenAICompatibleClient:
    """Собрать OpenAI-совместимый клиент DeepSeek.

    Args:
        settings: настройки приложения (ключ, URL, таймауты, цены).
        model: переопределить ``DEEPSEEK_MODEL``.
        http_client: готовый HTTP-клиент (тесты с ``MockTransport``).

    Returns:
        Клиент DeepSeek.

    Raises:
        LLMNotConfiguredError: не задан ``DEEPSEEK_API_KEY``.
    """
    api_key = (settings.deepseek_api_key or "").strip()
    if not api_key:
        raise LLMNotConfiguredError(
            "DEEPSEEK_API_KEY не задан: генерация v2 требует облачную "
            "модель-учителя (T-104b)"
        )
    return OpenAICompatibleClient(
        base_url=settings.deepseek_base_url,
        model=model or settings.deepseek_model or DEFAULT_MODEL,
        api_key=api_key,
        provider_name="deepseek",
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
        retry_backoff_s=settings.llm_retry_backoff_s,
        client=http_client,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        price_in_usd_per_1m=settings.llm_price_in_usd_per_1m,
        price_out_usd_per_1m=settings.llm_price_out_usd_per_1m,
    )


def _illegal_warning(illegal: Sequence[str]) -> str:
    bad = ", ".join(illegal)
    return (
        "ВНИМАНИЕ: в предыдущем ответе были нелегальные ходы: "
        f"{bad}. Перепиши комментарий, используя только ходы из пакета "
        "фактов."
    )


def build_retry_prompt(
    package: str,
    *,
    illegal: Sequence[str] = (),
    cjk: bool = False,
) -> str:
    """Пакет фактов + предупреждения о нелегальных ходах и/или CJK.

    Args:
        package: исходный пакет фактов.
        illegal: нелегальные SAN-ходы предыдущего ответа.
        cjk: в предыдущем ответе были китайские иероглифы.

    Returns:
        Промпт следующей попытки.
    """
    warnings: list[str] = []
    if illegal:
        warnings.append(_illegal_warning(illegal))
    if cjk:
        warnings.append(cjk_warning("ru"))
    if not warnings:
        return package
    return "\n\n".join([package, *warnings])


async def generate_with_validation(
    client: BaseLLMClient,
    *,
    initial_fen: str,
    mainline_san: Sequence[str] = (),
    package: str,
    system_prompt: str,
    max_retries: int,
) -> GenerationOutcome:
    """Сгенерировать комментарий и провалидировать ходы и алфавит.

    Нелегальные ходы — повод переспросить (до ``max_retries`` раз). CJK —
    ровно один retry; если иероглифы остались, предложения с ними
    вырезаются, а при слишком большой доле запись пропускается. На
    последней попытке нелегальные ходы тоже означают пропуск записи.

    Args:
        client: клиент учителя.
        initial_fen: стартовая позиция (партия или изолированная позиция).
        mainline_san: ходы партии для проверки альтернатив; для позиции пусто.
        package: пакет фактов (user-сообщение).
        system_prompt: системный промпт уровня.
        max_retries: число повторов (0 — одна попытка).

    Returns:
        :class:`GenerationOutcome`.
    """
    if max_retries < 0:
        raise ValueError("max_retries не может быть отрицательным")
    prompt = package
    attempts = 0
    cjk_retries = 0
    cjk_retry_used = False
    for attempt in range(1, max_retries + 2):
        result = await client.complete(prompt, system_prompt=system_prompt)
        attempts = attempt
        text = result.text.strip()
        legal, illegal = await validate_llm_response(
            text,
            initial_fen,
            mainline_san=list(mainline_san),
        )
        cjk_present = has_cjk(text)
        if not legal:
            if attempt <= max_retries:
                prompt = build_retry_prompt(
                    package, illegal=illegal, cjk=cjk_present
                )
                continue
            return GenerationOutcome(
                text="",
                ok=False,
                attempts=attempts,
                skip_reason="invalid_llm",
                cjk_retries=cjk_retries,
            )
        if cjk_present:
            if not cjk_retry_used and attempt <= max_retries:
                cjk_retry_used = True
                cjk_retries += 1
                prompt = build_retry_prompt(package, cjk=True)
                continue
            outcome = sanitize_cjk(text)
            if outcome.rejected:
                return GenerationOutcome(
                    text="",
                    ok=False,
                    attempts=attempts,
                    skip_reason="cjk_rejected",
                    cjk_retries=cjk_retries,
                )
            text = outcome.text
            return GenerationOutcome(
                text=text,
                ok=bool(text.strip()),
                attempts=attempts,
                skip_reason=None if text.strip() else "invalid_llm",
                cjk_retries=cjk_retries,
                cjk_stripped_sentences=outcome.removed_sentences,
            )
        if not text:
            return GenerationOutcome(
                text="",
                ok=False,
                attempts=attempts,
                skip_reason="invalid_llm",
                cjk_retries=cjk_retries,
            )
        return GenerationOutcome(
            text=text,
            ok=True,
            attempts=attempts,
            cjk_retries=cjk_retries,
        )
    return GenerationOutcome(
        text="",
        ok=False,
        attempts=attempts,
        skip_reason="invalid_llm",
        cjk_retries=cjk_retries,
    )


async def generate_for_game(
    client: BaseLLMClient,
    *,
    parsed: ParsedGame,
    package: str,
    system_prompt: str,
    max_retries: int,
) -> GenerationOutcome:
    """Обёртка :func:`generate_with_validation` для PGN-партии."""
    return await generate_with_validation(
        client,
        initial_fen=parsed.initial_fen,
        mainline_san=[move.san for move in parsed.moves],
        package=package,
        system_prompt=system_prompt,
        max_retries=max_retries,
    )


def build_v2_record(
    *,
    game: chess.pgn.Game,
    parsed: ParsedGame,
    fact_sheet: str,
    text: str,
    system_prompt: str,
    level: str,
    model: str,
    source_id: str,
    eval_points: int,
) -> dict[str, Any]:
    """Собрать v2-запись с метаданными учителя DeepSeek."""
    record = build_record(
        game=game,
        parsed=parsed,
        fact_sheet=fact_sheet,
        text=text,
        system_prompt=system_prompt,
        level=level,
        model=model,
        source_id=source_id,
        eval_points=eval_points,
    )
    metadata = record["metadata"]
    if isinstance(metadata, dict):
        metadata["dataset"] = "gold_v2"
        metadata["teacher_provider"] = "deepseek"
    return record


async def _run_positions_build(
    config: GoldV2Config,
    *,
    llm_client: BaseLLMClient | None,
    http_client: httpx.AsyncClient | None,
    settings: Settings | None,
) -> GoldV2Result:
    """Режим 2: комментарии для изолированных позиций из JSONL."""
    positions_path = config.positions
    if positions_path is None:  # pragma: no cover - вызывается только с путём
        raise ValueError("--positions не задан")
    if not positions_path.is_file():
        raise FileNotFoundError(f"файл позиций не найден: {positions_path}")

    cfg = settings or get_settings()
    stats = V2Stats()
    records: list[dict[str, Any]] = []
    system_prompt = build_system_prompt(config.level, positions=True)
    client = llm_client
    owns_client = False
    if not config.dry_run and client is None:
        client = build_client(
            cfg, model=config.model, http_client=http_client
        )
        owns_client = True
    if client is not None:
        client.set_level(config.level)

    try:
        for _index, item in read_positions(positions_path):
            if not config.dry_run and len(records) >= config.limit:
                break
            if config.dry_run and stats.prepared >= config.limit:
                break
            if item is None:
                stats.record_skipped("invalid_position")
                continue
            stats.positions += 1
            package = build_position_fact_package(item)
            stats.fact_sheet_chars += len(package)
            eval_points = 1 if item.evaluation else 0
            if config.dry_run:
                stats.prepared += 1
                continue
            if client is None:  # pragma: no cover - защита от мисконфига
                raise LLMNotConfiguredError("LLM-клиент не создан")
            outcome = await generate_with_validation(
                client,
                initial_fen=item.fen,
                package=package,
                system_prompt=system_prompt,
                max_retries=config.max_retries,
            )
            stats.record_attempts(outcome)
            if not outcome.ok:
                stats.record_skipped(outcome.skip_reason or "invalid_llm")
                continue
            records.append(
                build_position_record(
                    item=item,
                    fact_sheet=package,
                    text=outcome.text,
                    system_prompt=system_prompt,
                    level=config.level,
                    model=client.model,
                )
            )
            stats.record_converted(len(outcome.text), eval_points)
    finally:
        if owns_client and client is not None:
            await client.aclose()

    if records:
        write_records(config.output, records)
    return GoldV2Result(
        output=config.output, stats=stats, mode="positions"
    )


async def run_build(
    config: GoldV2Config,
    *,
    llm_client: BaseLLMClient | None = None,
    http_client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> GoldV2Result:
    """Сгенерировать датасет v2 через DeepSeek.

    Режим выбирается `config.positions`: ``None`` — PGN из ``input_dir``,
    иначе JSONL изолированных позиций.

    Args:
        config: пути, лимит, уровень, лимит токенов, retry, dry-run.
        llm_client: готовый клиент (тесты); иначе создаётся по настройкам.
        http_client: готовый HTTP-клиент для ``MockTransport``.
        settings: настройки (по умолчанию — из окружения).

    Returns:
        :class:`GoldV2Result` со статистикой и путём вывода.

    Raises:
        FileNotFoundError: каталога/PGN/positions.jsonl нет.
        ValueError: неверный конфиг.
        LLMNotConfiguredError: dry-run выключен, а ключа DeepSeek нет.
        LLMError: ошибка API DeepSeek.
    """
    if config.limit < 1:
        raise ValueError("--limit должен быть ≥ 1")
    if config.max_retries < 0:
        raise ValueError("--max-retries не может быть отрицательным")
    if config.max_tokens < 1:
        raise ValueError("--max-tokens должен быть ≥ 1")
    if config.positions is not None:
        return await _run_positions_build(
            config,
            llm_client=llm_client,
            http_client=http_client,
            settings=settings,
        )
    if not config.input_dir.is_dir():
        raise FileNotFoundError(f"каталог PGN не найден: {config.input_dir}")
    files = sorted(config.input_dir.glob("*.pgn"))
    if not files:
        raise FileNotFoundError(f"в {config.input_dir} нет *.pgn")

    cfg = settings or get_settings()
    stats = V2Stats(files=len(files))
    records: list[dict[str, Any]] = []
    system_prompt = build_system_prompt(config.level)
    client = llm_client
    owns_client = False
    if not config.dry_run and client is None:
        client = build_client(
            cfg, model=config.model, http_client=http_client
        )
        owns_client = True
    if client is not None:
        client.set_level(config.level)

    try:
        for path in files:
            file_games = iter_games(path)
            if not file_games:
                stats.record_skipped("invalid_pgn")
                continue
            for game_index, game in enumerate(file_games, start=1):
                if not config.dry_run and len(records) >= config.limit:
                    break
                if config.dry_run and stats.prepared >= config.limit:
                    break
                stats.games += 1
                pgn = export_game_pgn(game)
                try:
                    parsed = parse_pgn(pgn)
                except InvalidPgnError:
                    stats.record_skipped("invalid_pgn")
                    continue
                points = extract_eval_points(game)
                if len(points) != parsed.ply_count:
                    stats.record_skipped("invalid_evals")
                    continue
                if not any(point is not None for point in points):
                    stats.record_skipped("no_evals")
                    continue
                try:
                    analyses = analyses_from_evals(parsed, points)
                except ValueError:
                    stats.record_skipped("invalid_evals")
                    continue
                fact_sheet = build_fact_package(
                    parsed,
                    analyses,
                    config.level,
                    max_tokens=config.max_tokens,
                )
                eval_count = sum(point is not None for point in points)
                stats.fact_sheet_chars += len(fact_sheet)
                if config.dry_run:
                    stats.prepared += 1
                    continue
                if client is None:  # pragma: no cover - защита от мисконфига
                    raise LLMNotConfiguredError("LLM-клиент не создан")
                outcome = await generate_for_game(
                    client,
                    parsed=parsed,
                    package=fact_sheet,
                    system_prompt=system_prompt,
                    max_retries=config.max_retries,
                )
                stats.record_attempts(outcome)
                if not outcome.ok:
                    stats.record_skipped(outcome.skip_reason or "invalid_llm")
                    continue
                records.append(
                    build_v2_record(
                        game=game,
                        parsed=parsed,
                        fact_sheet=fact_sheet,
                        text=outcome.text,
                        system_prompt=system_prompt,
                        level=config.level,
                        model=client.model,
                        source_id=f"{path.name}:{game_index}",
                        eval_points=eval_count,
                    )
                )
                stats.record_converted(len(outcome.text), eval_count)
            if not config.dry_run and len(records) >= config.limit:
                break
            if config.dry_run and stats.prepared >= config.limit:
                break
    finally:
        if owns_client and client is not None:
            await client.aclose()

    if records:
        write_records(config.output, records)
    return GoldV2Result(output=config.output, stats=stats)


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


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено {value!r}"
        ) from exc
    if number < 0:
        raise argparse.ArgumentTypeError("должно быть ≥ 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер генератора датасета v2."""
    parser = argparse.ArgumentParser(
        prog="build_gold_dataset_v2.py",
        description=(
            "Сгенерировать schema_version=2 датасет сильной моделью DeepSeek "
            "из PGN Lichess с [%eval] или из JSONL изолированных позиций; "
            "ответы валидируются (SAN + CJK)."
        ),
    )
    parser.add_argument(
        "--input-dir", type=Path, default=DEFAULT_INPUT_DIR
    )
    parser.add_argument(
        "--positions",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "режим 2: JSONL позиций (FEN, evaluation, best_move) вместо "
            "PGN-каталога"
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--limit", type=_positive_int, default=DEFAULT_LIMIT
    )
    parser.add_argument(
        "--level",
        choices=("beginner", "club", "gm"),
        default="club",
    )
    parser.add_argument(
        "--max-tokens",
        type=_positive_int,
        default=MAX_FACT_PACKAGE_TOKENS,
        help="лимит пакета фактов в токенах (по умолчанию 2048)",
    )
    parser.add_argument(
        "--max-retries",
        type=_non_negative_int,
        default=DEFAULT_MAX_RETRIES,
        help="повторы при нелегальных ходах/иероглифах (по умолчанию 2)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="модель-учитель (по умолчанию DEEPSEEK_MODEL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="собрать пакеты фактов и показать статистику без вызова API",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: 0 — успех, 2 — ошибка/пусто."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = GoldV2Config(
        input_dir=args.input_dir,
        output=args.output,
        limit=args.limit,
        level=args.level,
        max_tokens=args.max_tokens,
        max_retries=args.max_retries,
        dry_run=args.dry_run,
        model=args.model,
        positions=args.positions,
    )
    try:
        result = asyncio.run(run_build(config))
    except (OSError, ValueError, LLMError) as exc:
        print(f"Ошибка генерации датасета v2: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # сетевые/неожиданные ошибки — без traceback
        print(f"Ошибка генерации: {exc}", file=sys.stderr)
        return 2

    for line in result.summary_lines():
        print(line)
    if config.dry_run:
        if result.stats.prepared == 0:
            source = (
                "positions.jsonl (FEN)"
                if result.mode == "positions"
                else "PGN и [%eval]"
            )
            print(
                f"Ни одного пакета не собрано: проверьте {source}.",
                file=sys.stderr,
            )
            return 2
        return 0
    if result.stats.converted == 0:
        source = (
            "positions.jsonl (FEN)"
            if result.mode == "positions"
            else "PGN и [%eval]"
        )
        print(
            f"Ни одной записи не собрано: проверьте {source} и ключ "
            "DEEPSEEK_API_KEY.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
