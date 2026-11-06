"""LLM-as-judge оценка комментариев (T-023).

Сценарий:
1. Берём несколько аннотированных партий из `annotated_games` (RAG).
2. Разбираем каждую через `/v1/analyze` (Stockfish) — как пользователь.
3. Генерируем комментарий для `club` и `gm` через `/v1/commentary`.
4. Отдаём комментарий судье (DeepSeek) с рубрикой из трёх шкал; лимит
   ответа судьи — `LLM_JUDGE_MAX_TOKENS` (2048), иначе JSON обрезается.
5. Считаем долю CJK-символов (KI-060) и долю объяснений.
6. Пишем отчёт в `docs/development/reports/LLM_EVAL.md` и JSON с сырыми
   оценками.

Модель генерации задаётся настройками (`VLLM_MODEL` или
`LORA_ADAPTER_PATH`), а не кодом скрипта: `qwen-7b-tokenizer` (v2)
оценивается теми же командами, что и прежние адаптеры. Метка версии для
отчёта/JSON задаётся флагом `--model-name` (итерация 60): v3 и v4 гоняются
на одном замороженном наборе, поэтому версия должна быть явной — API может
вернуть базовое имя модели вместо имени LoRA-адаптера.

Запуск:

    python scripts/evaluate_llm.py --games 5
    python scripts/evaluate_llm.py --games 5 --model-name qwen-7b-tokenizer_gguf-v3

По умолчанию партии берутся из **замороженного набора**
`tests/fixtures/eval_games.json` (5 партий Moscow zt 1964) — это обеспечивает
same-sample сравнение версий модели (T-024/KI-023, итерация 58). Режим БД
(случайные «самые короткие» партии из `annotated_games`) остаётся доступен
явно:

    python scripts/evaluate_llm.py --no-frozen-set --source new_samples --games 5

Скрипт использует TestClient, то есть тот же ASGI-стек, что и production, и
не требует отдельно запущенного uvicorn.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import chess.pgn
import httpx

# `python scripts/evaluate_llm.py` кладёт в sys.path только `scripts/`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.commentary.semantic import cjk_share, count_cjk  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db.models import AnnotatedGame, Level, LlmEvaluation  # noqa: E402
from app.llm import BaseLLMClient, get_llm_client  # noqa: E402
from app.main import create_app  # noqa: E402

__all__ = [
    "CjkSummary",
    "FrozenEvalSetError",
    "cjk_stats",
    "main",
]

DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "development" / "reports" / "LLM_EVAL.md"
DEFAULT_JSON = PROJECT_ROOT / "docs" / "development" / "reports" / "llm_eval_results.json"

#: Замороженный eval-набор (итерация 58): фиксированные 5 партий для
#: same-sample сравнения версий модели. Менять состав/PGN нельзя — иначе
#: результаты прошлых прогонов перестают быть сопоставимыми.
DEFAULT_FROZEN_SET = PROJECT_ROOT / "tests" / "fixtures" / "eval_games.json"

#: Метка версии модели по умолчанию для отчёта/JSON (итерация 60).
#: `--model-name` переопределяет её при прогоне v3/v4 на одном наборе.
DEFAULT_MODEL_NAME = "qwen-7b-tokenizer_gguf-v4"

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

#: Маркеры объяснения причины в комментарии (RU/EN). «so that» — EN-аналог
#: «поэтому»/«так что», встречающийся в объяснениях плана.
EXPLANATION_MARKERS = (
    "потому что",
    "так как",
    "поэтому",
    "благодаря",
    "because",
    "since",
    "therefore",
    "so that",
)

#: Маркеры, нормализованные так же, как текст: несколько пробелов/переводов
#: строки внутри маркера не должны ломать проверку.
_NORMALISED_EXPLANATION_MARKERS = tuple(
    " ".join(marker.casefold().split()) for marker in EXPLANATION_MARKERS
)


def has_explanation(text: str) -> bool:
    """Содержит ли комментарий объяснение причины (итерация 38).

    Проверка регистронезависимая и устойчивая к вариантам пробелов:
    несколько пробелов или перевод строки внутри маркера не ломают
    проверку («Потому  что» = «потому что»).

    Args:
        text: текст комментария (может быть пустым или None-подобным).

    Returns:
        True, если найден хотя бы один маркер из `EXPLANATION_MARKERS`.
    """
    if not text or not text.strip():
        return False
    normalised = " ".join(text.casefold().split())
    return any(
        marker in normalised for marker in _NORMALISED_EXPLANATION_MARKERS
    )


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
    has_explanation: bool = False
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


@dataclass(frozen=True, slots=True)
class CjkSummary:
    """Сводка CJK-дрейфа по комментариям (KI-060, итерация 50).

    Attributes:
        cjk_chars: суммарное число иероглифов U+4E00–U+9FFF.
        comments_with_cjk: сколько комментариев содержат хотя бы один.
        evaluated: сколько комментариев с непустым текстом (знаменатель).
        share: доля иероглифов среди всех непустых символов.
    """

    cjk_chars: int
    comments_with_cjk: int
    evaluated: int
    share: float


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LLM-as-judge оценка комментариев ChessAnnotator (T-023).",
    )
    parser.add_argument("--games", type=int, default=5, help="сколько партий взять (5–10)")
    parser.add_argument(
        "--frozen-set",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "брать партии из замороженного набора tests/fixtures/eval_games.json "
            "(по умолчанию; --no-frozen-set — режим БД)"
        ),
    )
    parser.add_argument(
        "--frozen-file",
        type=Path,
        default=None,
        help=(
            "путь к замороженному набору (по умолчанию "
            "tests/fixtures/eval_games.json)"
        ),
    )
    parser.add_argument(
        "--source",
        default=None,
        help=(
            "фильтр по annotated_games.source (например, new_samples); "
            "только вместе с --no-frozen-set"
        ),
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help=(
            "метка версии модели для отчёта/JSON (по умолчанию "
            f"{DEFAULT_MODEL_NAME}); нужна, чтобы v3 и v4 на одном frozen "
            "set не путались"
        ),
    )
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


class FrozenEvalSetError(ValueError):
    """Замороженный eval-набор отсутствует или испорчен (итерация 58)."""


def _load_frozen_games(
    path: Path = DEFAULT_FROZEN_SET, limit: int | None = None
) -> list[GameRow]:
    """Прочитать замороженный eval-набор из JSON-фикстуры.

    Набор фиксирует PGN-партии и sha256 каждой из них: если файл изменили,
    загрузчик падает, а не «тихо» оценивает другую выборку. Это делает
    сравнение версий модели честным (same-sample, T-024/KI-023).

    Args:
        path: путь к JSON (по умолчанию `tests/fixtures/eval_games.json`).
        limit: сколько первых партий вернуть (None/``<=0`` — все).

    Raises:
        FrozenEvalSetError: файла нет, JSON/схема некорректны, sha256 не
            совпал или PGN не разбирается.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FrozenEvalSetError(
            f"Файл замороженного eval-набора не найден: {path}"
        ) from exc
    except OSError as exc:
        raise FrozenEvalSetError(f"Не удалось прочитать {path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FrozenEvalSetError(f"{path}: некорректный JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FrozenEvalSetError(f"{path}: ожидался JSON-объект")
    schema_version = payload.get("schema_version")
    if schema_version != 1:
        raise FrozenEvalSetError(
            f"{path}: неподдерживаемая schema_version={schema_version!r}; "
            "ожидается 1"
        )

    items = payload.get("games")
    if not isinstance(items, list) or not items:
        raise FrozenEvalSetError(f"{path}: список games пуст или отсутствует")

    games: list[GameRow] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise FrozenEvalSetError(f"{path}: games[{index}] — не JSON-объект")
        pgn = item.get("pgn")
        white = item.get("white")
        black = item.get("black")
        if not isinstance(pgn, str) or not pgn.strip():
            raise FrozenEvalSetError(f"{path}: games[{index}].pgn пуст")
        if not isinstance(white, str) or not white.strip():
            raise FrozenEvalSetError(f"{path}: games[{index}].white пуст")
        if not isinstance(black, str) or not black.strip():
            raise FrozenEvalSetError(f"{path}: games[{index}].black пуст")

        expected_sha = item.get("pgn_sha256")
        if expected_sha is not None:
            actual_sha = hashlib.sha256(pgn.encode("utf-8")).hexdigest()
            if actual_sha != expected_sha:
                raise FrozenEvalSetError(
                    f"{path}: games[{index}] PGN изменён "
                    f"(sha256 {actual_sha[:12]}… != {str(expected_sha)[:12]}…); "
                    "замороженный набор нельзя редактировать"
                )

        parsed = chess.pgn.read_game(io.StringIO(pgn))
        if parsed is None or not any(parsed.mainline_moves()):
            raise FrozenEvalSetError(
                f"{path}: games[{index}] — PGN не разбирается или не содержит ходов"
            )

        game_id = item.get("game_id", -index)
        if not isinstance(game_id, int):
            raise FrozenEvalSetError(
                f"{path}: games[{index}].game_id должен быть целым числом"
            )
        event = item.get("event")
        games.append(
            GameRow(
                game_id=game_id,
                pgn=pgn,
                white=white,
                black=black,
                event=str(event) if event else "без события",
            )
        )

    if limit is not None and limit > 0:
        return games[:limit]
    return games


async def _load_games(
    limit: int, settings: Settings, *, source: str | None = None
) -> list[GameRow]:
    """Взять самые короткие аннотированные партии (быстрее для eval).

    Args:
        limit: сколько партий вернуть.
        settings: настройки (URL БД).
        source: если задан — только партии из `annotated_games.source`.
            Нужно для воспроизводимого eval-набора (T-042): без фильтра
            «самые короткие» могут меняться после каждого ingest-а.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            statement = select(AnnotatedGame).where(
                AnnotatedGame.pgn.is_not(None)
            )
            if source:
                statement = statement.where(AnnotatedGame.source == source)
            rows = (
                await session.scalars(
                    statement.order_by(
                        func.length(AnnotatedGame.pgn).asc(),
                        AnnotatedGame.id.asc(),
                    ).limit(limit)
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


def _judge_client(
    settings: Settings, *, client: httpx.AsyncClient | None = None
) -> BaseLLMClient:
    """Клиент судьи (DeepSeek) с отдельным лимитом ответа (итерация 61).

    Судья возвращает JSON с тремя оценками и коротким комментарием. При
    общем лимите `LLM_MAX_TOKENS=1024` ответ обрезался, и `_extract_json`
    падал с «в ответе судьи нет JSON-объекта» — в baseline v3 так потерялись
    2 из 5 партий. Поэтому у судьи свой лимит
    (`LLM_JUDGE_MAX_TOKENS`, по умолчанию 2048), а длина обычных
    комментариев не меняется.

    Args:
        settings: настройки приложения.
        client: готовый `httpx.AsyncClient` (тесты с `MockTransport`).

    Returns:
        Клиент DeepSeek для `gm` с `max_tokens=settings.llm_judge_max_tokens`.
    """
    judge_settings = settings.model_copy(
        update={"llm_max_tokens": settings.llm_judge_max_tokens}
    )
    return get_llm_client("gm", judge_settings, client=client)


async def _judge(
    rows: list[CommentaryRow], settings: Settings
) -> None:
    """Оценить все комментарии одним клиентом DeepSeek."""
    client = _judge_client(settings)
    print(f"Судья: deepseek, max_tokens={settings.llm_judge_max_tokens}")
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
            "token_budget_enabled": False,
            "semantic_cache_enabled": False,
            "llm_warmup_enabled": False,
            "llm_timeout_s": timeout_s,
        }
    )
    with TestClient(create_app(client_settings)) as client:
        for game in games:
            started = time.perf_counter()
            try:
                response = client.post(
                    "/v1/analyze", json={"pgn": game.pgn, "level": "club"}
                )
            except Exception as exc:  # Stockfish/ASGI упал на одной партии
                # Итерация 57: одна упавшая партия не должна ронять весь
                # eval-прогон. Логируем сбой, пишем по строке-ошибке на
                # каждый запрошенный уровень и переходим к следующей партии.
                print(
                    f"ANALYZE {game.white} — {game.black}: ошибка "
                    f"{type(exc).__name__}: {exc}"
                )
                for level in levels:
                    rows.append(
                        CommentaryRow(
                            game=game,
                            level=level,
                            error=f"analyze exception: {type(exc).__name__}: {exc}",
                        )
                    )
                continue
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
                    row.has_explanation = has_explanation(row.text)
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


def _explanation_counts(rows: list[CommentaryRow]) -> tuple[int, int]:
    """Сколько комментариев объясняют причины: (объяснено, оценено).

    В знаменатель входят только строки с непустым текстом: ошибки генерации
    не должны занижать долю объяснений.
    """
    evaluated = [row for row in rows if row.text.strip()]
    explained = sum(1 for row in evaluated if row.has_explanation)
    return explained, len(evaluated)


def _explanation_cell(rows: list[CommentaryRow]) -> str:
    """Ячейка таблицы «Объяснения»: `67% (2/3)` или `—` без данных."""
    explained, evaluated = _explanation_counts(rows)
    if not evaluated:
        return "—"
    return f"{100.0 * explained / evaluated:.0f}% ({explained}/{evaluated})"


def cjk_stats(text: str) -> tuple[int, float]:
    """Число CJK-иероглифов и их доля среди непустых символов текста.

    Args:
        text: комментарий LLM.

    Returns:
        ``(число_иероглифов, доля 0..1)``; для пустого текста — ``(0, 0.0)``.
    """
    if not text:
        return 0, 0.0
    return count_cjk(text), cjk_share(text)


def _cjk_summary(rows: list[CommentaryRow]) -> CjkSummary:
    """Сводная CJK-метрика (KI-060) по комментариям с непустым текстом.

    Доля считается как сумма иероглифов / сумма **непустых** символов,
    поэтому длинные комментарии не «разбавляют» короткие с дрейфом.
    """
    evaluated = [row for row in rows if row.text.strip()]
    cjk_chars = sum(count_cjk(row.text) for row in evaluated)
    total_chars = sum(
        1 for row in evaluated for char in row.text if not char.isspace()
    )
    with_cjk = sum(1 for row in evaluated if count_cjk(row.text) > 0)
    share = cjk_chars / total_chars if total_chars else 0.0
    return CjkSummary(
        cjk_chars=cjk_chars,
        comments_with_cjk=with_cjk,
        evaluated=len(evaluated),
        share=share,
    )


def _cjk_cell(rows: list[CommentaryRow]) -> str:
    """Ячейка таблицы «CJK»: доля символов и число комментариев с дрейфом."""
    summary = _cjk_summary(rows)
    if not summary.evaluated:
        return "—"
    return (
        f"{100.0 * summary.share:.2f}% "
        f"({summary.comments_with_cjk}/{summary.evaluated})"
    )


def _report(
    rows: list[CommentaryRow],
    games: list[GameRow],
    levels: list[str],
    timeout_s: float,
    *,
    frozen_path: Path | None = None,
    model_name: str | None = None,
    judge_max_tokens: int | None = None,
) -> str:
    overall = _averages(rows)
    scored = [row for row in rows if row.scores]
    errors = [row for row in rows if row.error]
    explained, evaluated = _explanation_counts(rows)
    explanation_share = 100.0 * explained / evaluated if evaluated else 0.0
    cjk = _cjk_summary(rows)
    if frozen_path is not None:
        games_line = (
            f"- Партий: **{len(games)}** из замороженного набора "
            f"`{frozen_path.name}` (same-sample сравнение версий модели)."
        )
        generation_line = "> **Генерация:** `club` → LM Studio (fallback DeepSeek), `gm` → DeepSeek"
    else:
        games_line = (
            f"- Партий: **{len(games)}** "
            "(самые короткие аннотированные партии из RAG)."
        )
        generation_line = "> **Генерация:** `club` → LM Studio, `gm` → DeepSeek"
    # Итерация 61: лимит судьи — отдельная настройка; в отчёте фиксируем
    # фактическое значение, чтобы прогоны с обрезкой JSON были видны сразу.
    judge_line = (
        f"- Лимит ответа судьи: **{judge_max_tokens} токенов** "
        "(`LLM_JUDGE_MAX_TOKENS`); при общем лимите 1024 JSON обрезался и "
        "партия терялась (итерация 61)."
        if judge_max_tokens
        else "- Лимит ответа судьи: `LLM_JUDGE_MAX_TOKENS` (по умолчанию 2048)."
    )
    header_lines = [
        "# LLM-as-judge: оценка комментариев (T-023)",
        "",
        f"> **Дата:** {date.today().isoformat()} · "
        "**Статус:** завершено (MVP v0.1.0)",
        "> **Скрипт:** `scripts/evaluate_llm.py` · **Судья:** DeepSeek",
        generation_line,
    ]
    if model_name:
        # Итерация 60: явная метка версии — v3 и v4 оцениваются на одном
        # frozen set, а API может вернуть базовое имя вместо имени адаптера.
        header_lines.append(
            f"> **Модель:** `{model_name}` (метка eval; API-имя — в таблице)"
        )
    lines = [
        *header_lines,
        "",
        "## Методика",
        "",
        games_line,
        f"- Уровней: **{', '.join(levels)}**; всего комментариев: **{len(rows)}**.",
        f"- Таймаут генерации для eval: **{timeout_s:.0f} с** (production — "
        "120 с; локальная модель на длинных пакетах фактов может не "
        "укладываться).",
        "- Каждая партия разобрана через `POST /v1/analyze` (Stockfish), "
        "комментарий сгенерирован через `POST /v1/commentary`.",
        "- Судья получает метаданные, ходы, ключевые моменты и сам комментарий; "
        "оценивает по шкалам 1–10 и возвращает JSON.",
        judge_line,
        "- Валидатор SAN уже отсекает нелегальные ходы до судьи; судья оценивает "
        "смысл и стиль.",
        "- Доля объяснений считается по маркерам «потому что» / «так как» / "
        "«поэтому» / «because» / «since» и их аналогам без учёта регистра; "
        "в знаменателе — комментарии с непустым текстом.",
        "- Доля CJK-символов = иероглифы U+4E00–U+9FFF / все непустые символы "
        "комментария (KI-060). Метрика не блокирует ответ; блокировка и "
        "вырезание живут в `app/commentary/service.py`. Модель задаётся "
        "`VLLM_MODEL`/`LORA_ADAPTER_PATH`, поэтому `qwen-7b-tokenizer` "
        "оценивается тем же скриптом без правок.",
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
        f"**Доля объяснений (потому что/так как/because): "
        f"{explanation_share:.0f}% ({explained}/{evaluated})**",
        "",
        f"**Доля CJK-символов (KI-060): {100.0 * cjk.share:.2f}%; "
        f"комментариев с иероглифами: "
        f"{cjk.comments_with_cjk}/{cjk.evaluated}**",
        "",
        "## По уровням",
        "",
        "| Уровень | Факт. точность | Полезность | Стиль | Объяснения | CJK |",
        "| :--- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for level in levels:
        level_rows = [row for row in rows if row.level == level]
        averages = _averages_by_level(rows, level)
        explanations = _explanation_cell(level_rows)
        cjk_cell = _cjk_cell(level_rows)
        lines.append(
            f"| `{level}` | {averages.get('factual_accuracy', 0):.2f} | "
            f"{averages.get('usefulness', 0):.2f} | {averages.get('style', 0):.2f} | "
            f"{explanations} | {cjk_cell} |"
        )

    lines += [
        "",
        "## Комментарии по партиям",
        "",
        "| Партия | Уровень | Факт | Польза | Стиль | Токены | Попытки | CJK | Комментарий судьи |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |",
    ]
    for row in rows:
        if row.error:
            lines.append(
                f"| {row.game.white} — {row.game.black} | `{row.level}` | — | — | — | — | — | — | "
                f"ошибка: {_escape(row.error)} |"
            )
            continue
        cjk_chars, row_cjk_share = cjk_stats(row.text)
        cjk_cell = (
            f"{cjk_chars} ({100.0 * row_cjk_share:.1f}%)"
            if cjk_chars
            else "0"
        )
        lines.append(
            f"| {row.game.white} — {row.game.black} | `{row.level}` | "
            f"{row.scores.get('factual_accuracy', 0):.0f} | "
            f"{row.scores.get('usefulness', 0):.0f} | "
            f"{row.scores.get('style', 0):.0f} | {row.tokens_used} | "
            f"{row.attempts or '—'} | {cjk_cell} | "
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
            "Сравнение с baseline и решение по модели — в "
            "`docs/development/reports/LLM_EVAL.md` и `docs/KNOWN_ISSUES.md`."
        )
    if cjk.comments_with_cjk:
        lines.append(
            f"\n**CJK-дрейф (KI-060):** {cjk.comments_with_cjk} из "
            f"{cjk.evaluated} комментариев содержат иероглифы "
            f"({100.0 * cjk.share:.2f}% символов). Ответы с дрейфом нужно "
            "переобучить/перепроверить до продакшена."
        )
    else:
        lines.append(
            "\n**CJK-дрейф (KI-060):** иероглифов не обнаружено "
            f"({100.0 * cjk.share:.2f}% символов, "
            f"0/{cjk.evaluated} комментариев)."
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
        "- Выборка — фиксированные 5 партий (замороженный набор), не 30–50; "
        "слепой тест уровней (T-024) остаётся открытым.",
        "- Сравнивать результаты между версиями модели корректно только внутри "
        "замороженного набора: при изменении состава/PGN дельты становятся "
        "межвыборочными (T-024/KI-023).",
        "- Валидатор проверяет легальность SAN, а не атрибуцию оценок (KI-023). "
        "CJK-метрика измеряет языковой дрейф, но не исправляет его: блокировка "
        "и вырезание выполняются в сервисе (итерация 49).",
        "- Результаты зависят от версии модели и промпта судьи.",
        "",
        "_Сырые результаты: `docs/development/reports/llm_eval_results.json`_",
        "",
    ]
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:220]


def _dump_json(
    rows: list[CommentaryRow],
    path: Path,
    *,
    model_name: str | None = None,
) -> None:
    """Записать сырые оценки в JSON.

    Args:
        rows: строки комментариев с оценками.
        path: куда писать.
        model_name: метка версии модели (итерация 60) — попадает в каждую
            запись как ``model_name``, чтобы прогоны v3/v4 на одном frozen
            set различались даже если API вернул одинаковое имя модели.
    """
    payload: list[dict[str, Any]] = []
    for row in rows:
        cjk_chars, cjk_share_value = cjk_stats(row.text)
        payload.append(
            {
                "game_id": row.game.game_id,
                # Отрицательный id — синтетический id замороженного набора,
                # а не строка annotated_games.
                "frozen": row.game.game_id < 0,
                "white": row.game.white,
                "black": row.game.black,
                "event": row.game.event,
                "level": row.level,
                "commentary_id": row.commentary_id,
                # `model` — имя, которое вернул API; `model_name` — метка
                # версии из `--model-name` (для same-sample сравнений).
                "model": row.model,
                "model_name": model_name,
                "tokens_used": row.tokens_used,
                "attempts": row.attempts,
                "validated": row.validated,
                "has_explanation": row.has_explanation,
                "cjk_chars": cjk_chars,
                "cjk_share": round(cjk_share_value, 6),
                "scores": row.scores,
                "judge_comment": row.judge_comment,
                "judge_tokens": row.judge_tokens,
                "error": row.error,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def _save_evaluations(
    rows: list[CommentaryRow], settings: Settings
) -> int:
    """Сохранить оценки в `llm_evaluations` (T-039)."""
    engine = create_async_engine(
        settings.database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    saved = 0
    try:
        async with factory() as session:
            for row in rows:
                if not row.scores:
                    continue
                session.add(
                    LlmEvaluation(
                        model=row.model or "unknown",
                        level=Level(row.level),
                        accuracy_score=row.scores["factual_accuracy"],
                        usefulness_score=row.scores["usefulness"],
                        style_score=row.scores["style"],
                    )
                )
                saved += 1
            await session.commit()
    finally:
        await engine.dispose()
    return saved


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not 1 <= args.games <= 20:
        print("--games должен быть в диапазоне 1..20", file=sys.stderr)
        return 2
    levels = [item.strip() for item in args.levels.split(",") if item.strip()]
    if not levels or any(level not in {"beginner", "club", "gm"} for level in levels):
        print("--levels: ожидается beginner, club или gm", file=sys.stderr)
        return 2
    model_name = (args.model_name or "").strip()
    if not model_name:
        print("--model-name не может быть пустым", file=sys.stderr)
        return 2

    source = (args.source or "").strip() or None
    if source is not None and args.frozen_set:
        print(
            "--source задаёт источник в БД и несовместим с замороженным "
            "набором; добавьте --no-frozen-set, чтобы выбрать партии из "
            "annotated_games.",
            file=sys.stderr,
        )
        return 2

    settings = get_settings()
    frozen_path: Path | None = None
    if args.frozen_set:
        frozen_path = args.frozen_file or DEFAULT_FROZEN_SET
        try:
            games = _load_frozen_games(frozen_path, limit=args.games)
        except FrozenEvalSetError as exc:
            print(f"Ошибка замороженного eval-набора: {exc}", file=sys.stderr)
            return 1
        print(
            f"Замороженный eval-набор: {len(games)} партий из {frozen_path}"
        )
    else:
        games = asyncio.run(_load_games(args.games, settings, source=source))
        if not games:
            filter_hint = f" (source={source})" if source else ""
            print(
                f"В annotated_games нет партий{filter_hint}. Сначала выполните "
                "scripts/ingest_pgn.py.",
                file=sys.stderr,
            )
            return 1

    print(
        f"Партий: {len(games)}, уровни: {', '.join(levels)}"
        + (f", источник: {source}" if source else "")
    )
    print(f"Модель (метка eval): {model_name}")
    rows = _judge_cli(games, levels, settings, args.timeout_s)
    overall = _averages(rows)
    average = sum(overall.values()) / len(overall) if overall else 0.0

    try:
        saved = asyncio.run(_save_evaluations(rows, settings))
        print(f"Сохранено в llm_evaluations: {saved}")
    except Exception as exc:
        print(
            f"Не удалось сохранить llm_evaluations: {exc}",
            file=sys.stderr,
        )

    report = _report(
        rows,
        games,
        levels,
        args.timeout_s,
        frozen_path=frozen_path,
        model_name=model_name,
        judge_max_tokens=settings.llm_judge_max_tokens,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    _dump_json(rows, args.json_output, model_name=model_name)

    print("--- ИТОГ ---")
    print(f"Модель: {model_name}")
    for scale, value in overall.items():
        print(f"{scale}: {value:.2f}/10")
    print(f"Средняя: {average:.2f}/10")
    explained, evaluated = _explanation_counts(rows)
    share = 100.0 * explained / evaluated if evaluated else 0.0
    print(f"Объяснения: {share:.0f}% ({explained}/{evaluated})")
    cjk = _cjk_summary(rows)
    print(
        f"CJK: {100.0 * cjk.share:.2f}% символов, "
        f"комментариев с иероглифами: "
        f"{cjk.comments_with_cjk}/{cjk.evaluated}"
    )
    print(f"Отчёт: {args.output}")
    print(f"JSON: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
