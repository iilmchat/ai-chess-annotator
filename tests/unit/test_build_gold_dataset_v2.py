"""Unit-тесты генератора датасета v2 через DeepSeek (T-104b, итерация 49).

Сеть не используется: ``httpx.MockTransport`` эмулирует DeepSeek
(``POST /chat/completions``). Проверяются контракт записи, реальный
``build_fact_package``, retry на нелегальных ходах и CJK, dry-run без API и
пропуск записей с «китайским» ответом.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.errors import LLMNotConfiguredError
from scripts.build_gold_dataset_v2 import (
    DEFAULT_OUTPUT,
    ONLY_COMMENTARY_INSTRUCTION,
    POSITION_INSTRUCTION,
    GoldV2Config,
    build_client,
    build_parser,
    build_position_fact_package,
    build_system_prompt,
    main,
    parse_position_record,
    read_positions,
    run_build,
)
from scripts.train_lora import validate_dataset

Handler = Callable[[httpx.Request], httpx.Response]

EVAL_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/game1"]
[White "White"]
[Black "Black"]
[Result "1-0"]
[WhiteElo "1900"]
[BlackElo "2000"]

1. e4 { [%eval 0.20] } e5 { [%eval 0.10] }
2. Nf3 { [%eval -1.50] } Nc6 { [%eval -1.40] } 1-0
"""

NO_EVAL_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/noeval"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 1-0
"""

CLEAN_TEXT = (
    "Партия прошла в напряжённой борьбе. Белые активно действовали в "
    "центре, а чёрные искали контригру на ферзевом фланге."
)
DRIFTED_TEXT = "Партия была интересной. Конь идет на 马e4."
ALL_CJK_TEXT = "马马马 这是中文"

#: Нелегальный ход в позиции после 1.e4 e5 2.Nf3 Nc6.
ILLEGAL_TEXT = "Белые могли сыграть Qxf7 и выиграть."

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
#: После 1.e4 — ход чёрных.
BLACK_TO_MOVE_FEN = (
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
)

POSITIONS_JSONL = "\n".join(
    [
        json.dumps(
            {
                "id": "p1",
                "fen": START_FEN,
                "evaluation": 0.35,
                "best_move": "e4",
            }
        ),
        json.dumps(
            {
                "id": "p2",
                "fen": BLACK_TO_MOVE_FEN,
                "eval_cp": -120,
                "best_move": "e5",
            }
        ),
    ]
) + "\n"


def _completion(text: str, model: str = "deepseek-chat") -> dict[str, Any]:
    return {
        "id": "chatcmpl-deepseek-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 700, "completion_tokens": 120},
    }


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-chat",
        "deepseek_api_key": "test-deepseek-key",
        "llm_max_retries": 0,
        "llm_retry_backoff_s": 0.0,
        "llm_max_tokens": 1024,
    }
    base.update(overrides)
    return Settings(**base)


class _ScriptedDeepSeek:
    """MockTransport-обработчик: отдаёт ответы по очереди и пишет запросы."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.texts) - 1)
        return httpx.Response(200, json=_completion(self.texts[index]))

    def payload(self, index: int) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[index].content)
        return body


def _write_pgn(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


async def _run(
    tmp_path: Path,
    handler: Handler,
    *,
    max_retries: int = 1,
    limit: int = 10,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> Any:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir(exist_ok=True)
    _write_pgn(input_dir / "one.pgn", EVAL_PGN)
    output = tmp_path / "gold_v2.jsonl"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        return await run_build(
            GoldV2Config(
                input_dir=input_dir,
                output=output,
                limit=limit,
                max_retries=max_retries,
                dry_run=dry_run,
            ),
            http_client=http,
            settings=settings or _settings(),
        )


# --- контракт записи и запроса ----------------------------------------------


async def test_run_build_writes_v2_dataset(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([CLEAN_TEXT])

    result = await _run(tmp_path, handler)

    assert result.stats.converted == 1
    assert result.stats.api_calls == 1
    assert result.stats.retries == 0
    output = tmp_path / "gold_v2.jsonl"
    assert output.is_file()
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["schema_version"] == 2
    assert [message["role"] for message in record["messages"]] == [
        "system",
        "user",
        "assistant",
    ]
    assert record["messages"][2]["content"] == CLEAN_TEXT
    assert record["metadata"]["model"] == "deepseek-chat"
    assert record["metadata"]["dataset"] == "gold_v2"
    assert record["metadata"]["teacher_provider"] == "deepseek"
    assert record["metadata"]["eval_points"] == 4
    assert validate_dataset(output) == (1, [])


async def test_request_uses_club_prompt_fact_package_and_model(
    tmp_path: Path,
) -> None:
    handler = _ScriptedDeepSeek([CLEAN_TEXT])

    await _run(tmp_path, handler)

    request = handler.requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer test-deepseek-key"
    payload = handler.payload(0)
    assert payload["model"] == "deepseek-chat"
    assert payload["max_tokens"] == 1024
    assert payload["temperature"] == pytest.approx(0.2)
    messages = payload["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "Ты — шахматный комментатор" in messages[0]["content"]
    assert ONLY_COMMENTARY_INSTRUCTION in messages[0]["content"]
    assert messages[1]["content"].startswith("# Партия")
    assert "## 1. Ход 2 белых: Nf3" in messages[1]["content"]
    assert "Лучший ход движка неизвестен" in messages[1]["content"]


def test_build_system_prompt_requires_only_commentary_text() -> None:
    prompt = build_system_prompt("club")

    assert "Ты — шахматный комментатор" in prompt
    assert "ТОЛЬКО текст комментария" in prompt
    assert "без вступлений" in prompt


def test_build_client_requires_api_key() -> None:
    with pytest.raises(LLMNotConfiguredError, match="DEEPSEEK_API_KEY"):
        build_client(_settings(deepseek_api_key=""))


def test_build_client_model_override() -> None:
    client = build_client(_settings(), model="deepseek-reasoner")

    assert client.model == "deepseek-reasoner"
    assert client.provider == "deepseek"


# --- retry ------------------------------------------------------------------


async def test_illegal_move_triggers_retry(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([ILLEGAL_TEXT, CLEAN_TEXT])

    result = await _run(tmp_path, handler, max_retries=1)

    assert result.stats.converted == 1
    assert result.stats.retries == 1
    assert len(handler.requests) == 2
    retry_prompt = handler.payload(1)["messages"][1]["content"]
    assert "нелегальные ходы" in retry_prompt
    assert "Qxf7" in retry_prompt
    record = json.loads(
        (tmp_path / "gold_v2.jsonl").read_text(encoding="utf-8")
    )
    assert record["messages"][2]["content"] == CLEAN_TEXT


async def test_illegal_move_exhausted_is_skipped(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([ILLEGAL_TEXT])

    result = await _run(tmp_path, handler, max_retries=1)

    assert result.stats.converted == 0
    assert result.stats.skipped["invalid_llm"] == 1
    assert len(handler.requests) == 2
    assert not (tmp_path / "gold_v2.jsonl").exists()


async def test_cjk_triggers_russian_only_retry(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([DRIFTED_TEXT, CLEAN_TEXT])

    result = await _run(tmp_path, handler, max_retries=1)

    assert result.stats.converted == 1
    assert result.stats.cjk_retries == 1
    retry_prompt = handler.payload(1)["messages"][1]["content"]
    assert "Отвечай только на русском языке" in retry_prompt
    assert "Убери китайские иероглифы" in retry_prompt


async def test_cjk_on_last_attempt_is_stripped(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([DRIFTED_TEXT, DRIFTED_TEXT])

    result = await _run(tmp_path, handler, max_retries=1)

    assert result.stats.converted == 1
    assert result.stats.cjk_stripped_sentences == 1
    record = json.loads(
        (tmp_path / "gold_v2.jsonl").read_text(encoding="utf-8")
    )
    assistant = record["messages"][2]["content"]
    assert "马" not in assistant
    assert assistant == "Партия была интересной."


async def test_mostly_cjk_answer_is_skipped(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([ALL_CJK_TEXT])

    result = await _run(tmp_path, handler, max_retries=0)

    assert result.stats.converted == 0
    assert result.stats.skipped["cjk_rejected"] == 1
    assert not (tmp_path / "gold_v2.jsonl").exists()


# --- dry-run и CLI ----------------------------------------------------------


async def test_dry_run_builds_packages_without_api(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("dry-run не должен ходить в DeepSeek")

    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "one.pgn", EVAL_PGN)
    output = tmp_path / "gold_v2.jsonl"

    result = await run_build(
        GoldV2Config(
            input_dir=input_dir,
            output=output,
            limit=10,
            dry_run=True,
        ),
        settings=_settings(deepseek_api_key=""),
    )

    assert calls == []
    assert result.stats.prepared == 1
    assert result.stats.converted == 0
    assert result.stats.fact_sheet_chars > 0
    assert not output.exists()


async def test_dry_run_skips_no_evals(tmp_path: Path) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "noeval.pgn", NO_EVAL_PGN)

    result = await run_build(
        GoldV2Config(
            input_dir=input_dir,
            output=tmp_path / "out.jsonl",
            dry_run=True,
        ),
        settings=_settings(deepseek_api_key=""),
    )

    assert result.stats.prepared == 0
    assert result.stats.skipped["no_evals"] == 1


async def test_missing_input_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="каталог"):
        await run_build(
            GoldV2Config(
                input_dir=tmp_path / "missing",
                output=tmp_path / "out.jsonl",
                dry_run=True,
            )
        )


async def test_empty_input_dir_raises(tmp_path: Path) -> None:
    input_dir = tmp_path / "empty"
    input_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="нет"):
        await run_build(
            GoldV2Config(
                input_dir=input_dir,
                output=tmp_path / "out.jsonl",
                dry_run=True,
            )
        )


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])

    assert args.limit == 1000
    assert args.level == "club"
    assert args.max_tokens == 2048
    assert args.max_retries == 2
    assert args.dry_run is False
    assert args.output == DEFAULT_OUTPUT


def test_parser_rejects_non_positive_limit() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--limit", "0"])

    assert exc_info.value.code == 2


def test_main_dry_run_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "one.pgn", EVAL_PGN)
    output = tmp_path / "gold_v2.jsonl"

    code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output",
            str(output),
            "--dry-run",
            "--limit",
            "1",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "Пакетов фактов (dry-run): 1" in captured.out
    assert not output.exists()


def test_main_returns_2_for_missing_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--input-dir",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "out.jsonl"),
            "--dry-run",
        ]
    )

    assert code == 2
    assert "Ошибка" in capsys.readouterr().err


# --- режим 2: изолированные позиции ----------------------------------------


def _position(fen: str = START_FEN, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"fen": fen}
    payload.update(fields)
    return payload


async def _run_positions(
    tmp_path: Path,
    handler: Handler,
    *,
    content: str = POSITIONS_JSONL,
    max_retries: int = 1,
    limit: int = 10,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> Any:
    positions = tmp_path / "positions.jsonl"
    positions.write_text(content, encoding="utf-8")
    output = tmp_path / "gold_v2.jsonl"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        return await run_build(
            GoldV2Config(
                positions=positions,
                output=output,
                limit=limit,
                max_retries=max_retries,
                dry_run=dry_run,
            ),
            http_client=http,
            settings=settings or _settings(),
        )


def test_parse_position_record_normalizes_evaluation() -> None:
    item = parse_position_record(
        _position(evaluation=1.2, best_move="e4"), 1, source="p.jsonl"
    )

    assert item.fen == START_FEN
    assert item.evaluation == "+1.20"
    assert item.eval_cp == 120
    assert item.eval_mate is None
    assert item.best_move == "e4"
    assert item.source_id == "p.jsonl:1"


def test_parse_position_record_cp_units() -> None:
    item = parse_position_record(_position(eval_cp=-120), 1)

    assert item.evaluation == "-1.20"
    assert item.eval_cp == -120


def test_parse_position_record_mate() -> None:
    item = parse_position_record(_position(evaluation="#3"), 1)

    assert item.evaluation == "#3"
    assert item.eval_mate == 3
    assert item.eval_cp is not None and item.eval_cp > 0


def test_parse_position_record_lichess_eval_object() -> None:
    item = parse_position_record(
        _position(evaluation={"type": "cp", "value": 42}), 1
    )

    assert item.evaluation == "+0.42"
    assert item.eval_cp == 42


def test_parse_position_record_ignores_illegal_best_move() -> None:
    item = parse_position_record(_position(best_move="Qxf7"), 1)

    assert item.best_move is None


def test_parse_position_record_accepts_uci_best_move() -> None:
    """`generate_positions.py` соседа пишет best_move в UCI (e2e4)."""
    item = parse_position_record(_position(best_move="e2e4"), 1)

    assert item.best_move == "e4"


def test_parse_position_record_rejects_invalid_fen() -> None:
    with pytest.raises(ValueError, match="FEN"):
        parse_position_record(_position(fen="not-a-fen"), 1)


def test_read_positions_skips_invalid_lines(tmp_path: Path) -> None:
    path = tmp_path / "positions.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(_position(id="ok-1")),
                "not json",
                json.dumps(_position(fen="bad")),
                "",
                json.dumps(_position(id="ok-2")),
            ]
        ),
        encoding="utf-8",
    )

    items = list(read_positions(path))

    assert [item.source_id if item else None for _, item in items] == [
        "ok-1",
        None,
        None,
        "ok-2",
    ]


def test_build_position_fact_package_is_minimal() -> None:
    item = parse_position_record(
        _position(evaluation=1.2, best_move="e4"), 1
    )

    package = build_position_fact_package(item)

    assert package.startswith("# Позиция")
    assert f"FEN: {START_FEN}" in package
    assert "Ход: белые" in package
    assert "Оценка движка: +1.20 (за белых)" in package
    assert "Лучший ход: e4" in package
    assert "# Партия" not in package
    assert "# Ходы партии" not in package
    assert "ключевые моменты" not in package.lower()


def test_build_system_prompt_positions_adds_instruction() -> None:
    prompt = build_system_prompt("club", positions=True)

    assert POSITION_INSTRUCTION in prompt
    assert ONLY_COMMENTARY_INSTRUCTION in prompt


async def test_run_build_positions_writes_dataset(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([CLEAN_TEXT])

    result = await _run_positions(tmp_path, handler)

    assert result.mode == "positions"
    assert result.stats.positions == 2
    assert result.stats.converted == 2
    output = tmp_path / "gold_v2.jsonl"
    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert validate_dataset(output) == (2, [])
    first = records[0]
    assert first["messages"][1]["content"].startswith("# Позиция")
    assert first["metadata"]["source"] == "positions"
    assert first["metadata"]["fen"] == START_FEN
    assert first["metadata"]["evaluation"] == "+0.35"
    assert first["metadata"]["best_move"] == "e4"
    assert first["metadata"]["eval_points"] == 1
    assert first["metadata"]["teacher_provider"] == "deepseek"
    # Системный промпт режима 2 требует не выдумывать историю ходов.
    assert POSITION_INSTRUCTION in first["messages"][0]["content"]
    request_payload = handler.payload(0)
    assert request_payload["messages"][1]["content"].startswith("# Позиция")


async def test_run_build_positions_retries_illegal_move(
    tmp_path: Path,
) -> None:
    handler = _ScriptedDeepSeek([ILLEGAL_TEXT, CLEAN_TEXT])

    result = await _run_positions(tmp_path, handler, max_retries=1)

    assert result.stats.converted == 2
    assert result.stats.retries == 1
    assert "нелегальные ходы" in handler.payload(1)["messages"][1]["content"]


async def test_run_build_positions_cjk_retry(tmp_path: Path) -> None:
    handler = _ScriptedDeepSeek([DRIFTED_TEXT, CLEAN_TEXT])

    result = await _run_positions(tmp_path, handler, max_retries=1)

    assert result.stats.cjk_retries == 1
    assert "Отвечай только на русском" in handler.payload(1)["messages"][1][
        "content"
    ]


async def test_run_build_positions_dry_run_without_api(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("dry-run не должен ходить в DeepSeek")

    result = await _run_positions(
        tmp_path,
        handler,
        dry_run=True,
        settings=_settings(deepseek_api_key=""),
    )

    assert result.stats.prepared == 2
    assert result.stats.converted == 0
    assert not (tmp_path / "gold_v2.jsonl").exists()


async def test_run_build_positions_empty_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("API не должен вызываться")

    result = await _run_positions(
        tmp_path,
        handler,
        content="",
        dry_run=True,
        settings=_settings(deepseek_api_key=""),
    )

    assert result.stats.prepared == 0


async def test_run_build_positions_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="позиций"):
        await run_build(
            GoldV2Config(
                positions=tmp_path / "missing.jsonl",
                output=tmp_path / "out.jsonl",
                dry_run=True,
            )
        )


def test_parser_accepts_positions_path(tmp_path: Path) -> None:
    path = tmp_path / "positions.jsonl"
    args = build_parser().parse_args(["--positions", str(path)])

    assert args.positions == path
    assert args.dry_run is False


def test_main_positions_dry_run_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    positions = tmp_path / "positions.jsonl"
    positions.write_text(POSITIONS_JSONL, encoding="utf-8")
    output = tmp_path / "gold_v2.jsonl"

    code = main(
        [
            "--positions",
            str(positions),
            "--output",
            str(output),
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "Позиций: 2" in captured.out
    assert "Пакетов фактов (dry-run): 2" in captured.out
    assert not output.exists()
