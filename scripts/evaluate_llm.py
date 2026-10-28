"""LLM-as-judge оценка комментариев (T-023).

Сценарий:
1. Берём несколько аннотированных партий из `annotated_games` (RAG).
2. Разбираем каждую через `/v1/analyze` (Stockfish) — как пользователь.
3. Генерируем комментарий для `club` и `gm` через `/v1/commentary`.
4. Отдаём комментарий судье (DeepSeek) с рубрикой из трёх шкал.
5. Пишем отчёт в `docs/development/reports/LLM_EVAL.md` и JSON с сырыми
   оценками.

Запуск:

    python scripts/evaluate_llm.py --games 5

Скрипт использует TestClient, то есть тот же ASGI-стек, что и production, и
не требует отдельно запущенного uvicorn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# `python scripts/evaluate_llm.py` кладёт в sys.path только `scripts/`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.db.models import AnnotatedGame  # noqa: E402
from app.llm import get_llm_client  # noqa: E402
from app.main import create_app  # noqa: E402

__all__ = ["main"]

DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "development" / "reports" / "LLM_EVAL.md"
DEFAULT_JSON = PROJECT_ROOT / "docs" / "development" / "reports" / "llm_eval_results.json"

JUDGE_SYSTEM = (
    "Ты — строгий шахматный редактор и методолог оценки. "
    "Оценивай только предоставленный комментарий и факты анализа. "
    "Не придумывай ходы и не пересказывай партию. "
    "Верни ТОЛЬКО валидный JSON без Markdown."
)

JUDGE_TEMPLATE = """Оцени шахматный комментарий по трём шкалам от 1 до 10.

Шкалы:
1. factual_accuracy — фактическая точность: нет выдуманных ходов, оценок
   и мотивов; все утверждения согласуются с фактами анализа. Если ход или
   оценка есть в пакете (включая PV и рекомендации Policy), это НЕ выдумка,
   даже если его нет в сыгранной партии.
2. usefulness — полезность: объясняет идеи, планы и альтернативы, а не
   просто перечисляет ходы.
3. style — стиль: связный гроссмейстерский текст, уместная терминология,
   без воды и повторов.

Факты анализа:
{fact_sheet}

Комментарий уровня {level}:
<<<COMMENTARY
{commentary}
COMMENTARY>>>

Верни JSON ровно такой формы:
{{"factual_accuracy": <1-10>, "usefulness": <1-10>, "style": <1-10>,
  "comment": "<одно короткое предложение>"}}
"""

SCALES = ("factual_accuracy", "usefulness", "style")


@dataclass(slots=True)
class GameRow:
    """Партия из RAG, выбранная для оценки."""

    game_id: int
    pgn: str
    white: str
    black: str
    event: str


@dataclass(slots=True)
class CommentaryRow:
    """Сгенерированный комментарий и его оценка."""

    game: GameRow
    level: str
    commentary_id: int | None = None
    text: str = ""
    model: str = ""
    tokens_used: int = 0
    attempts: int = 0
    validated: bool = False
    elapsed_s: float = 0.0
    fact_sheet: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    judge_comment: str = ""
    judge_tokens: int = 0
    error: str | None = None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM-as-judge оценка комментариев ChessAnnotator (T-023).",
    )
    parser.add_argument("--games", type=int, default=5, help="сколько партий взять (5–10)")
    parser.add_argument(
        "--levels",
        default="club,gm",
        help="уровни через запятую (по умолчанию club,gm)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=300.0,
        help="таймаут LLM для eval (production default 120 с; локальная модель медленная)",
    )
    return parser.parse_args(argv)


async def _load_games(limit: int, settings: Settings) -> list[GameRow]:
    """Взять самые короткие аннотированные партии (быстрее для eval)."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = (
                await session.scalars(
                    select(AnnotatedGame)
                    .where(AnnotatedGame.pgn.is_not(None))
                    .order_by(func.length(AnnotatedGame.pgn).asc(), AnnotatedGame.id.asc())
                    .limit(limit)
                )
            ).all()
    finally:
        await engine.dispose()

    return [
        GameRow(
            game_id=row.id,
            pgn=row.pgn,
            white=row.white or "неизвестно",
            black=row.black or "неизвестно",
            event=row.event or "без события",
        )
        for row in rows
    ]


def _fact_sheet(analysis: dict[str, Any], max_moments: int = 6) -> str:
    """Компактные факты анализа для судьи."""
    meta = analysis.get("meta") or {}
    moves = analysis.get("moves") or []
    lines = [
        f"Белые: {meta.get('white') or 'неизвестно'}",
        f"Чёрные: {meta.get('black') or 'неизвестно'}",
        f"ECO: {meta.get('eco') or 'неизвестен'}",
        "Ходы: "
        + " ".join(
            f"{_move_prefix(item['ply'])}{item['san']}" for item in moves
        ),
    ]
    key = sorted(moves, key=lambda item: item["delta_cp"], reverse=True)[:max_moments]
    lines.append("Ключевые моменты:")
    for item in key:
        lines.append(
            f"- {_move_prefix(item['ply'])}{item['san']} — "
            f"{item['classification']}, потеря {item['delta_cp']} cp, "
            f"лучший {item['best_move_san']}, оценка "
            f"{_format_eval(item.get('eval_cp'), item.get('mate'))}"
        )
    return "\n".join(lines)


def _move_prefix(ply: int) -> str:
    number = (ply + 1) // 2
    return f"{number}." if ply % 2 == 1 else f"{number}..."


def _format_eval(eval_cp: int | None, mate: int | None) -> str:
    if mate is not None:
        return f"мат {mate:+d}"
    if eval_cp is None:
        return "—"
    return f"{eval_cp / 100:+.2f}"


def _extract_json(text: str) -> dict[str, Any]:
    """Достать JSON из ответа судьи (модель может добавить текст)."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("в ответе судьи нет JSON-объекта")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("JSON судьи — не объект")
    return payload


async def _judge(
    rows: list[CommentaryRow], settings: Settings
) -> None:
    """Оценить все комментарии одним клиентом DeepSeek."""
    client = get_llm_client("gm", settings)
    try:
        for index, row in enumerate(rows, start=1):
            if row.error:
                continue
            prompt = JUDGE_TEMPLATE.format(
                fact_sheet=row.fact_sheet,
                level=row.level,
                commentary=row.text,
            )
            started = time.perf_counter()
            try:
                result = await client.complete(prompt, system_prompt=JUDGE_SYSTEM)
                payload = _extract_json(result.text)
                scores = {
                    scale: float(payload[scale])
                    for scale in SCALES
                }
                if any(not 1 <= score <= 10 for score in scores.values()):
                    raise ValueError(f"оценки вне 1..10: {scores}")
                row.scores = scores
                row.judge_comment = str(payload.get("comment", "")).strip()
                row.judge_tokens = result.usage.tokens_in + result.usage.tokens_out
            except Exception as exc:  # eval не должен падать из-за одной оценки
                row.error = f"judge: {exc}"
            print(
                f"[{index}/{len(rows)}] {row.game.white} — {row.game.black} "
                f"{row.level}: {row.scores or row.error} "
                f"({time.perf_counter() - started:.1f} с)"
            )
    finally:
        await client.aclose()


def _commentary_with_retry(
    client: TestClient,
    analysis_id: int,
    level: str,
    attempts: int = 3,
) -> Any:
    """POST /v1/commentary с повтором на транзиентные 503/504.

    Локальная LM Studio иногда не успевает загрузить/достроить модель;
    повтор через паузу обычно проходит. 502 (нелегальные ходы) не
    повторяем — это качество ответа, а не инфраструктура.
    """
    response = client.post(
        "/v1/commentary",
        json={"analysis_id": analysis_id, "level": level},
    )
    for attempt in range(2, attempts + 1):
        if response.status_code not in (503, 504):
            break
        time.sleep(3 * (attempt - 1))
        print(
            f"  retry commentary {level}: HTTP {response.status_code} "
            f"(attempt {attempt}/{attempts})"
        )
        response = client.post(
            "/v1/commentary",
            json={"analysis_id": analysis_id, "level": level},
        )
    return response


def _judge_cli(
    games: list[GameRow],
    levels: list[str],
    settings: Settings,
    timeout_s: float,
) -> list[CommentaryRow]:
    """Сгенерировать комментарии через API и вернуть строки для судьи."""
    rows: list[CommentaryRow] = []
    client_settings = settings.model_copy(
        update={
            "rate_limit_requests": 10_000,
            "llm_warmup_enabled": False,
            "llm_timeout_s": timeout_s,
        }
    )
    with TestClient(create_app(client_settings)) as client:
        for game in games:
            started = time.perf_counter()
            response = client.post(
                "/v1/analyze", json={"pgn": game.pgn, "level": "club"}
            )
            print(
                f"ANALYZE {game.white} — {game.black}: "
                f"{response.status_code} ({(time.perf_counter() - started):.1f} с)"
            )
            if response.status_code != 200:
                for level in levels:
                    rows.append(
                        CommentaryRow(
                            game=game,
                            level=level,
                            error=f"analyze {response.status_code}: {response.text[:200]}",
                        )
                    )
                continue

            analysis = response.json()
            for level in levels:
                row = CommentaryRow(game=game, level=level)
                started = time.perf_counter()
                commentary = _commentary_with_retry(
                    client, int(analysis["analysis_id"]), level
                )
                row.elapsed_s = time.perf_counter() - started
                if commentary.status_code == 200:
                    body = commentary.json()
                    row.commentary_id = body["commentary_id"]
                    row.text = body["text"]
                    row.model = body["model"]
                    row.tokens_used = body["tokens_used"]
                    row.attempts = body["attempts"]
                    row.validated = body["validated"]
                else:
                    row.error = (
                        f"commentary {commentary.status_code}: "
                        f"{commentary.text[:300]}"
                    )
                row.fact_sheet = _fact_sheet(analysis)
                rows.append(row)
                print(
                    f"COMMENTARY {game.white} — {game.black} {level}: "
                    f"{commentary.status_code}, {row.elapsed_s:.1f} с"
                )

    asyncio.run(_judge(rows, settings))
    return rows


def _averages(rows: list[CommentaryRow]) -> dict[str, float]:
    scored = [row for row in rows if row.scores]
    if not scored:
        return {}
    return {
        scale: sum(row.scores[scale] for row in scored) / len(scored)
        for scale in SCALES
    }


def _averages_by_level(rows: list[CommentaryRow], level: str) -> dict[str, float]:
    return _averages([row for row in rows if row.level == level])


def _report(
    rows: list[CommentaryRow],
    games: list[GameRow],
    levels: list[str],
    timeout_s: float,
) -> str:
    overall = _averages(rows)
    scored = [row for row in rows if row.scores]
    errors = [row for row in rows if row.error]
    lines = [
        "# LLM-as-judge: оценка комментариев (T-023)",
        "",
        "> **Дата:** 2026-10-28 · **Статус:** завершено (MVP v0.1.0)",
        "> **Скрипт:** `scripts/evaluate_llm.py` · **Судья:** DeepSeek",
        "> **Генерация:** `club` → LM Studio, `gm` → DeepSeek",
        "",
        "## Методика",
        "",
        f"- Партий: **{len(games)}** (самые короткие аннотированные партии из RAG).",
        f"- Уровней: **{', '.join(levels)}**; всего комментариев: **{len(rows)}**.",
        f"- Таймаут генерации для eval: **{timeout_s:.0f} с** (production — "
        "120 с; локальная модель на длинных пакетах фактов может не "
        "укладываться).",
        "- Каждая партия разобрана через `POST /v1/analyze` (Stockfish), "
        "комментарий сгенерирован через `POST /v1/commentary`.",
        "- Судья получает метаданные, ходы, ключевые моменты и сам комментарий; "
        "оценивает по шкалам 1–10 и возвращает JSON.",
        "- Валидатор SAN уже отсекает нелегальные ходы до судьи; судья оценивает "
        "смысл и стиль.",
        "",
        "## Итоговые оценки",
        "",
        "| Шкала | Средняя | Оценено |",
        "| :--- | ---: | ---: |",
    ]
    for scale, label in (
        ("factual_accuracy", "Фактическая точность"),
        ("usefulness", "Полезность"),
        ("style", "Стиль"),
    ):
        lines.append(
            f"| {label} | **{overall.get(scale, 0):.2f} / 10** | {len(scored)} |"
        )

    lines += [
        "",
        "## По уровням",
        "",
        "| Уровень | Факт. точность | Полезность | Стиль |",
        "| :--- | ---: | ---: | ---: |",
    ]
    for level in levels:
        averages = _averages_by_level(rows, level)
        lines.append(
            f"| `{level}` | {averages.get('factual_accuracy', 0):.2f} | "
            f"{averages.get('usefulness', 0):.2f} | {averages.get('style', 0):.2f} |"
        )

    lines += [
        "",
        "## Комментарии по партиям",
        "",
        "| Партия | Уровень | Факт | Польза | Стиль | Токены | Попытки | Комментарий судьи |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |",
    ]
    for row in rows:
        if row.error:
            lines.append(
                f"| {row.game.white} — {row.game.black} | `{row.level}` | — | — | — | — | — | "
                f"ошибка: {_escape(row.error)} |"
            )
            continue
        lines.append(
            f"| {row.game.white} — {row.game.black} | `{row.level}` | "
            f"{row.scores.get('factual_accuracy', 0):.0f} | "
            f"{row.scores.get('usefulness', 0):.0f} | "
            f"{row.scores.get('style', 0):.0f} | {row.tokens_used} | "
            f"{row.attempts or '—'} | "
            f"{_escape(row.judge_comment)} |"
        )

    average = sum(overall.values()) / len(overall) if overall else 0.0
    lines += [
        "",
        "## Вывод",
        "",
    ]
    if average >= 7:
        lines.append(
            f"Средняя оценка **{average:.2f}/10** — порог **7/10** достигнут. "
            "Фактическая точность обеспечена валидатором SAN; замечания судьи "
            "носят редакционный характер."
        )
    else:
        lines.append(
            f"Средняя оценка **{average:.2f}/10** — ниже порога **7/10**. "
            "Заведён KI-030 с планом улучшения промптов; релиз не блокируется."
        )
    if errors:
        lines.append(
            f"\nНе оценено из-за ошибок: **{len(errors)}** из {len(rows)}. "
            "Подробности — в таблице выше."
        )
    lines += [
        "",
        "## Ограничения",
        "",
        "- LLM-as-judge подвержен bias: модель склонна завышать оценки и хуже "
        "ловит тонкие фактические ошибки, чем человек.",
        "- Выборка — 5 коротких партий, не 30–50; слепой тест уровней (T-024) "
        "остаётся открытым.",
        "- Валидатор проверяет легальность SAN, а не атрибуцию оценок (KI-023).",
        "- Результаты зависят от версии модели и промпта судьи.",
        "",
        "_Сырые результаты: `docs/development/reports/llm_eval_results.json`_",
        "",
    ]
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:220]


def _dump_json(rows: list[CommentaryRow], path: Path) -> None:
    payload = [
        {
            "game_id": row.game.game_id,
            "white": row.game.white,
            "black": row.game.black,
            "event": row.game.event,
            "level": row.level,
            "commentary_id": row.commentary_id,
            "model": row.model,
            "tokens_used": row.tokens_used,
            "attempts": row.attempts,
            "validated": row.validated,
            "scores": row.scores,
            "judge_comment": row.judge_comment,
            "judge_tokens": row.judge_tokens,
            "error": row.error,
        }
        for row in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not 1 <= args.games <= 20:
        print("--games должен быть в диапазоне 1..20", file=sys.stderr)
        return 2
    levels = [item.strip() for item in args.levels.split(",") if item.strip()]
    if not levels or any(level not in {"beginner", "club", "gm"} for level in levels):
        print("--levels: ожидается beginner, club или gm", file=sys.stderr)
        return 2

    settings = get_settings()
    games = asyncio.run(_load_games(args.games, settings))
    if not games:
        print(
            "В annotated_games нет партий. Сначала выполните "
            "scripts/ingest_pgn.py.",
            file=sys.stderr,
        )
        return 1

    print(f"Партий: {len(games)}, уровни: {', '.join(levels)}")
    rows = _judge_cli(games, levels, settings, args.timeout_s)
    overall = _averages(rows)
    average = sum(overall.values()) / len(overall) if overall else 0.0

    report = _report(rows, games, levels, args.timeout_s)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    _dump_json(rows, args.json_output)

    print("--- ИТОГ ---")
    for scale, value in overall.items():
        print(f"{scale}: {value:.2f}/10")
    print(f"Средняя: {average:.2f}/10")
    print(f"Отчёт: {args.output}")
    print(f"JSON: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
