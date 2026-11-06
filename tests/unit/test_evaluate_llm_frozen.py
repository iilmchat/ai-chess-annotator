"""Замороженный eval-набор (итерация 58, T-024/KI-023).

Проверяется: фикстура `tests/fixtures/eval_games.json` действительно
фиксирует 5 легальных PGN-партий (исторический набор итерации 16),
`_load_frozen_games` читает и валидирует их, защита от редактирования
(sha256) работает, а скрипт по умолчанию оценивает именно этот набор —
режим БД остаётся только явным `--no-frozen-set`.

Все тесты офлайн: БД, Stockfish и LLM не нужны.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import chess.pgn
import pytest

from scripts.evaluate_llm import (
    DEFAULT_FROZEN_SET,
    CommentaryRow,
    FrozenEvalSetError,
    GameRow,
    _dump_json,
    _load_frozen_games,
    _parse_args,
    _report,
    main,
)

#: Порядок и состав набора итерации 16 (Moscow zt 1964).
EXPECTED_PLAYERS = [
    ("Stein, Leonid", "Geller, Efim"),
    ("Stein, Leonid", "Suetin, Alexei"),
    ("Bronstein, David", "Spassky, Boris"),
    ("Bronstein, David", "Kholmov, Ratmir"),
    ("Bronstein, David", "Stein, Leonid"),
]

GAME = GameRow(
    game_id=-1,
    pgn="1. e4 e5",
    white="Stein, Leonid",
    black="Geller, Efim",
    event="Moscow zt",
)


def _fixture_payload() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        DEFAULT_FROZEN_SET.read_text(encoding="utf-8")
    )
    return payload


def _write_payload(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "eval_games.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# --- сама фикстура -----------------------------------------------------------


def test_committed_fixture_has_exactly_five_known_games() -> None:
    payload = _fixture_payload()

    assert payload["schema_version"] == 1
    assert payload["frozen_at"] == "2026-11-06"
    games = payload["games"]
    assert len(games) == 5
    assert [(game["white"], game["black"]) for game in games] == EXPECTED_PLAYERS
    assert [game["game_id"] for game in games] == [-1, -2, -3, -4, -5]


def test_committed_fixture_pgns_are_legal_and_sha256_locked() -> None:
    for index, game in enumerate(_fixture_payload()["games"], start=1):
        pgn = game["pgn"]
        expected_sha = game["pgn_sha256"]

        assert hashlib.sha256(pgn.encode("utf-8")).hexdigest() == expected_sha, (
            f"games[{index}] PGN не совпадает с зафиксированным sha256"
        )

        parsed = chess.pgn.read_game(io.StringIO(pgn))
        assert parsed is not None
        assert not parsed.errors, f"games[{index}] PGN нелегален: {parsed.errors}"
        moves = list(parsed.mainline_moves())
        assert moves, f"games[{index}] PGN без ходов"
        assert parsed.headers["White"] == game["white"]
        assert parsed.headers["Black"] == game["black"]
        assert parsed.headers["Result"] == game["result"]


def test_eval_set_matches_previously_tested_players() -> None:
    """Набор — те же партии, что оценивались в iter-16 (Stein/Bronstein/Spassky)."""
    players = set()
    for game in _fixture_payload()["games"]:
        players.update((game["white"], game["black"]))

    assert {"Stein, Leonid", "Bronstein, David", "Spassky, Boris"} <= players


# --- загрузчик ---------------------------------------------------------------


def test_load_frozen_games_returns_game_rows() -> None:
    games = _load_frozen_games(DEFAULT_FROZEN_SET)

    assert len(games) == 5
    assert all(isinstance(game, GameRow) for game in games)
    assert [game.game_id for game in games] == [-1, -2, -3, -4, -5]
    assert (games[0].white, games[0].black) == ("Stein, Leonid", "Geller, Efim")
    assert games[2].event == "Moscow zt"
    assert games[0].pgn.startswith('[Event "Moscow zt"]')


def test_load_frozen_games_respects_limit() -> None:
    games = _load_frozen_games(DEFAULT_FROZEN_SET, limit=2)

    assert [game.game_id for game in games] == [-1, -2]


def test_load_frozen_games_default_limit_keeps_all() -> None:
    assert len(_load_frozen_games(DEFAULT_FROZEN_SET, limit=None)) == 5
    assert len(_load_frozen_games(DEFAULT_FROZEN_SET, limit=0)) == 5


def test_load_frozen_games_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FrozenEvalSetError, match="не найден"):
        _load_frozen_games(tmp_path / "missing.json")


def test_load_frozen_games_rejects_tampered_pgn(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["games"][0]["pgn"] = payload["games"][0]["pgn"].replace(
        "1. e4 e5", "1. d4 d5", 1
    )

    with pytest.raises(FrozenEvalSetError, match="PGN изменён"):
        _load_frozen_games(_write_payload(tmp_path, payload))


def test_load_frozen_games_rejects_unknown_schema(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["schema_version"] = 2

    with pytest.raises(FrozenEvalSetError, match="schema_version"):
        _load_frozen_games(_write_payload(tmp_path, payload))


def test_load_frozen_games_rejects_empty_games(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["games"] = []

    with pytest.raises(FrozenEvalSetError, match="games"):
        _load_frozen_games(_write_payload(tmp_path, payload))


def test_load_frozen_games_rejects_invalid_pgn(tmp_path: Path) -> None:
    payload = _fixture_payload()
    payload["games"][0]["pgn"] = "не PGN вовсе"
    payload["games"][0].pop("pgn_sha256")

    with pytest.raises(FrozenEvalSetError, match="PGN"):
        _load_frozen_games(_write_payload(tmp_path, payload))


# --- CLI ---------------------------------------------------------------------


def test_parse_args_frozen_set_is_default() -> None:
    args = _parse_args(["--games", "3"])

    assert args.frozen_set is True
    assert args.frozen_file is None


def test_parse_args_can_disable_frozen_set() -> None:
    args = _parse_args(["--no-frozen-set", "--source", "new_samples"])

    assert args.frozen_set is False
    assert args.source == "new_samples"


def test_parse_args_accepts_custom_frozen_file(tmp_path: Path) -> None:
    path = tmp_path / "custom.json"

    args = _parse_args(["--frozen-file", str(path)])

    assert args.frozen_file == path


def test_main_rejects_source_in_frozen_mode_offline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--source", "new_samples", "--games", "1"]) == 2

    captured = capsys.readouterr()
    assert "--no-frozen-set" in captured.err


def test_main_reports_missing_frozen_file_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--frozen-file", str(tmp_path / "missing.json"), "--games", "1"]) == 1

    assert "Ошибка замороженного eval-набора" in capsys.readouterr().err


# --- отчёт и JSON ------------------------------------------------------------


def test_report_mentions_frozen_set() -> None:
    report = _report(
        [], [GAME], ["club"], 300.0, frozen_path=DEFAULT_FROZEN_SET
    )

    assert "замороженного набора" in report
    assert "eval_games.json" in report
    assert "same-sample" in report
    assert "самые короткие аннотированные партии" not in report
    # Совместимость с существующими тестами и таблицами отчёта.
    assert "| CJK |" in report


def test_report_keeps_rag_wording_without_frozen_set() -> None:
    report = _report([], [GAME], ["club"], 300.0)

    assert "самые короткие аннотированные партии из RAG" in report


def test_dump_json_marks_frozen_games(tmp_path: Path) -> None:
    path = tmp_path / "llm_eval_results.json"
    row = CommentaryRow(game=GAME, level="club", text="Белые захватили центр.")

    _dump_json([row], path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["frozen"] is True
    assert payload[0]["game_id"] == -1
