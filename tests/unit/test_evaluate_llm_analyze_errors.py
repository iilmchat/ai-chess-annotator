"""Устойчивость LLM-eval к падению Stockfish на одной партии (итерация 57).

`_judge_cli` не должен прерывать весь прогон, если `POST /v1/analyze` бросил
исключение: сбой логируется, по каждому запрошенному уровню добавляется
строка-ошибка, а обработка продолжается со следующей партией.

Тесты полностью офлайн: `TestClient` подменяется фейком, `create_app` — заглушкой,
`_judge` — async no-op, поэтому БД и сеть не трогаются.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts import evaluate_llm
from scripts.evaluate_llm import (
    CommentaryRow,
    GameRow,
    _judge_cli,
    get_settings,
)

GAMES = [
    GameRow(
        game_id=1,
        pgn="1. e4 e5",
        white="White",
        black="Black",
        event="Test",
    ),
    GameRow(
        game_id=2,
        pgn="1. d4 d5",
        white="White2",
        black="Black2",
        event="Test",
    ),
]


class _FakeTestClient:
    """Фейк `TestClient`: контекстный менеджер, чей `/v1/analyze` всегда падает."""

    def __init__(self, app: Any, exc: BaseException | None = None) -> None:
        self.app = app
        self.exc: BaseException = exc or RuntimeError("Stockfish упал")
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> _FakeTestClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def post(self, url: str, json: dict[str, Any] | None = None) -> None:
        self.calls.append({"url": url, "json": json or {}})
        raise self.exc


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exc: BaseException | None = None,
) -> list[_FakeTestClient]:
    """Подменить `TestClient`/`create_app`/`_judge` и вернуть созданные клиенты."""
    created: list[_FakeTestClient] = []

    def factory(app: Any) -> _FakeTestClient:
        fake = _FakeTestClient(app, exc)
        created.append(fake)
        return fake

    async def _noop_judge(rows: list[CommentaryRow], settings: Any) -> None:
        return None

    monkeypatch.setattr(evaluate_llm, "create_app", lambda _settings: object())
    monkeypatch.setattr(evaluate_llm, "TestClient", factory)
    monkeypatch.setattr(evaluate_llm, "_judge", _noop_judge)
    return created


def test_analyze_exception_does_not_abort_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Падение analyze на первой партии — вторая всё равно обрабатывается."""
    clients = _install_fakes(monkeypatch)

    rows = _judge_cli(GAMES, ["club"], get_settings(), 30.0)

    assert len(rows) == len(GAMES)
    assert [row.level for row in rows] == ["club", "club"]
    for row in rows:
        assert row.error is not None
        assert row.error.startswith("analyze exception")
        assert "RuntimeError: Stockfish упал" in row.error
    # Первый сбой не остановил цикл: клиент видел обе партии.
    assert len(clients) == 1
    assert [call["json"]["pgn"] for call in clients[0].calls] == [
        "1. e4 e5",
        "1. d4 d5",
    ]
    assert all(call["url"] == "/v1/analyze" for call in clients[0].calls)
    captured = capsys.readouterr()
    assert "ошибка RuntimeError: Stockfish упал" in captured.out


def test_analyze_exception_adds_error_row_per_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Одна упавшая партия даёт по строке-ошибке на каждый запрошенный уровень."""
    clients = _install_fakes(monkeypatch)

    rows = _judge_cli(GAMES[:1], ["club", "gm"], get_settings(), 30.0)

    assert [row.level for row in rows] == ["club", "gm"]
    for row in rows:
        assert row.error is not None
        assert row.error.startswith("analyze exception")
        assert row.game is GAMES[0]
    # Исключение не вышло наружу, а analyze вызван ровно один раз.
    assert len(clients) == 1
    assert len(clients[0].calls) == 1


def test_keyboard_interrupt_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`except Exception` не перехватывает BaseException: Ctrl+C работает."""
    _install_fakes(monkeypatch, exc=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        _judge_cli(GAMES[:1], ["club"], get_settings(), 30.0)
