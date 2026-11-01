"""Unit-тесты загрузчика Lichess PGN (T-092), без сети.

HTTP подменяется ``httpx.MockTransport``: проверяются URL/параметры,
POST-тело для ID, фильтр рейтинга/варианта/evals и разбиение на файлы.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.fetch_lichess_games import (
    DEFAULT_MAX_ELO,
    DEFAULT_MIN_ELO,
    FetchConfig,
    FetchError,
    build_parser,
    fetch_pgn,
    filter_games,
    main,
    parse_games,
    run_fetch,
    write_pgn_chunks,
)


def _game_pgn(
    game_id: str,
    *,
    white_elo: object = 1900,
    black_elo: object = 2000,
    evals: bool = True,
    variant: str = "Standard",
) -> str:
    comment = " { [%eval 0.23] }" if evals else ""
    return f"""[Event "Rated Blitz game"]
[Site "https://lichess.org/{game_id}"]
[White "White"]
[Black "Black"]
[Result "1-0"]
[WhiteElo "{white_elo}"]
[BlackElo "{black_elo}"]
[Variant "{variant}"]

1. e4{comment} e5{comment} 2. Nf3 Nc6 1-0
"""


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _combine(*pgns: str) -> str:
    return "\n\n".join(pgn.rstrip() for pgn in pgns) + "\n"


def test_fetch_user_pgn_builds_request() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, text=_game_pgn("abc12345"))

    with _client(handler) as client:
        text = fetch_pgn(client, username="DrNykterstein", max_games=50)

    assert "abc12345" in text
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/games/user/DrNykterstein"
    assert captured["params"]["evals"] == "true"
    assert captured["params"]["rated"] == "true"
    assert captured["params"]["max"] == "50"
    assert "blitz" in captured["params"]["perfType"]


def test_fetch_game_ids_posts_csv_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, text=_game_pgn("abc12345"))

    with _client(handler) as client:
        fetch_pgn(client, game_ids=("abc12345", "def67890"))

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/games/export/_ids"
    assert captured["body"] == "abc12345,def67890"
    assert captured["params"]["evals"] == "true"
    assert "perfType" not in captured["params"]


def test_fetch_requires_exactly_one_selector() -> None:
    with _client(lambda request: httpx.Response(200, text="")) as client:
        with pytest.raises(FetchError, match="ровно одно"):
            fetch_pgn(client)


def test_fetch_empty_response_is_error() -> None:
    with _client(lambda request: httpx.Response(200, text="  ")) as client:
        with pytest.raises(FetchError, match="пустой"):
            fetch_pgn(client, username="user")


def test_parse_games_reads_multiple_games() -> None:
    text = _combine(_game_pgn("game1"), _game_pgn("game2"))

    games = parse_games(text)

    assert [game.headers["Site"] for game in games] == [
        "https://lichess.org/game1",
        "https://lichess.org/game2",
    ]


def test_filter_games_by_rating_variant_and_evals() -> None:
    games = parse_games(
        _combine(
            _game_pgn("ok"),
            _game_pgn("low", white_elo=1700),
            _game_pgn("noeval", evals=False),
            _game_pgn("variant", variant="Chess960"),
            _game_pgn("missing", white_elo="?"),
        )
    )

    selected, stats = filter_games(games)

    assert [item.game.headers["Site"] for item in selected] == [
        "https://lichess.org/ok"
    ]
    assert stats.fetched == 5
    assert stats.selected == 1
    assert stats.skipped_rating == 2
    assert stats.skipped_no_evals == 1
    assert stats.skipped_variant == 1


def test_filter_games_respects_custom_range() -> None:
    games = parse_games(_game_pgn("mid", white_elo=1500, black_elo=1600))

    selected, stats = filter_games(games, min_elo=1400, max_elo=1650)

    assert len(selected) == 1
    assert stats.selected == 1


def test_write_pgn_chunks_splits_and_roundtrips(tmp_path: Path) -> None:
    games = parse_games(
        _combine(*(_game_pgn(f"g{index}") for index in range(5)))
    )
    selected, _ = filter_games(games)

    files = write_pgn_chunks(
        selected, tmp_path, prefix="user name", per_file=2
    )

    assert len(files) == 3
    assert [path.name for path in files] == [
        "user_name_001.pgn",
        "user_name_002.pgn",
        "user_name_003.pgn",
    ]
    counts = [len(parse_games(path.read_text(encoding="utf-8"))) for path in files]
    assert counts == [2, 2, 1]


def test_write_pgn_chunks_rejects_bad_per_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="per_file"):
        write_pgn_chunks((), tmp_path, per_file=0)
    with pytest.raises(ValueError, match="per_file"):
        write_pgn_chunks((), tmp_path, per_file=101)


def test_run_fetch_writes_selected_games(tmp_path: Path) -> None:
    text = _combine(_game_pgn("ok"), _game_pgn("low", white_elo=2300))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=text)

    with _client(handler) as client:
        result = run_fetch(
            FetchConfig(
                username="player",
                output_dir=tmp_path,
                max_games=10,
                per_file=50,
            ),
            client=client,
        )

    assert result.stats.selected == 1
    assert result.stats.skipped_rating == 1
    assert len(result.files) == 1
    assert len(parse_games(result.files[0].read_text(encoding="utf-8"))) == 1


def test_main_with_mock_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_game_pgn("ok"))

    with _client(handler) as client:
        code = main(
            [
                "--username",
                "player",
                "--output-dir",
                str(tmp_path),
            ],
            client=client,
        )

    assert code == 0
    assert len(list(tmp_path.glob("*.pgn"))) == 1
    out = capsys.readouterr().out
    assert "получено партий: 1" in out
    assert "выбрано" in out


def test_main_returns_2_on_http_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with _client(handler) as client:
        code = main(
            ["--username", "missing", "--output-dir", str(tmp_path)],
            client=client,
        )

    assert code == 2
    assert "Ошибка загрузки Lichess" in capsys.readouterr().err


def test_main_returns_2_when_nothing_selected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=_game_pgn("low", white_elo=1200, black_elo=1300)
        )

    with _client(handler) as client:
        code = main(
            ["--username", "player", "--output-dir", str(tmp_path)],
            client=client,
        )

    assert code == 2
    assert "Ни одной партии" in capsys.readouterr().err


def test_parser_requires_source() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([])

    assert exc_info.value.code == 2


def test_parser_rejects_too_many_per_file() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(
            ["--username", "user", "--per-file", "101"]
        )

    assert exc_info.value.code == 2


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--game-ids", "a,b"])

    assert args.game_ids == "a,b"
    assert args.min_elo == DEFAULT_MIN_ELO
    assert args.max_elo == DEFAULT_MAX_ELO
    assert args.per_file == 50
