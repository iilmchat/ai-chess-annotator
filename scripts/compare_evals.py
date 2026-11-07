"""Сравнение двух прогонов LLM-as-judge (итерация 62, подготовка v4).

Скрипт читает два JSON-файла, которые пишет
`scripts/evaluate_llm.py --json-output`, и выводит Markdown-таблицу с
дельтами по трём шкалам судьи, доле объяснений и CJK-дрейфу. Это
same-sample сравнение: v3 (baseline) и v4 должны быть прогнаны на одном
замороженном наборе `tests/fixtures/eval_games.json`.

Запуск::

    python scripts/compare_evals.py \\
        docs/development/reports/llm_eval_results_v3.json \\
        docs/development/reports/llm_eval_results_v4.json

Опции::

    --baseline-label v3 --new-label v4   # подписи колонок
    --output docs/development/reports/LLM_EVAL_V3_VS_V4.md
    --strict-samples                     # падать, если наборы партий разные

Метрики:

* шкалы — среднее по записям с непустым `scores` (как в
  `evaluate_llm._averages`);
* объяснения — доля комментариев с `has_explanation=True` среди записей,
  у которых есть текст (сбой генерации не входит в знаменатель);
* CJK — средняя по комментариям доля `cjk_share` (0..1) и число
  комментариев с иероглифами. Это не агрегатная доля из `LLM_EVAL.md`
  (сумма иероглифов / сумма символов), потому что в JSON нет длины
  комментария; средняя по комментариям одинаково считается для обеих
  версий и на нуле совпадает с baseline.

Старые JSON (до итерации 60) не содержат `has_explanation`/`cjk_*`:
такие метрики показываются как «—», а шкалы считаются как обычно.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

__all__ = [
    "SCALES",
    "CjkMetrics",
    "EvalFileError",
    "EvalMetrics",
    "EvalRecord",
    "ExplanationMetrics",
    "compute_metrics",
    "main",
    "render_comparison",
]

SCALES: tuple[str, ...] = ("factual_accuracy", "usefulness", "style")

SCALE_LABELS: Mapping[str, str] = {
    "factual_accuracy": "Факт. точность",
    "usefulness": "Полезность",
    "style": "Стиль",
}

#: `qwen-7b-tokenizer_gguf-v3` → `v3`; `llm_eval_results_v4` → `v4`.
_VERSION_SUFFIX = re.compile(r"(?:^|[-_.])v(\d+)$")


class EvalFileError(ValueError):
    """JSON-файл прогона отсутствует или не соответствует схеме."""


@dataclass(frozen=True, slots=True)
class EvalRecord:
    """Одна запись JSON-прогона (партия × уровень × комментарий)."""

    game_id: int
    level: str
    scores: Mapping[str, float]
    has_explanation: bool | None
    cjk_chars: int | None
    cjk_share: float | None
    error: str | None
    has_text: bool
    model_name: str | None


@dataclass(frozen=True, slots=True)
class ExplanationMetrics:
    """Доля комментариев с объяснением причины."""

    explained: int
    evaluated: int

    @property
    def rate(self) -> float:
        """Доля 0..1; знаменатель всегда > 0 (проверено при сборке)."""
        return self.explained / self.evaluated if self.evaluated else 0.0


@dataclass(frozen=True, slots=True)
class CjkMetrics:
    """CJK-дрейф: средняя доля и число комментариев с иероглифами."""

    share: float
    comments_with_cjk: int
    evaluated: int
    chars: int


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    """Сводные метрики одного прогона."""

    records: int
    scored: int
    levels: tuple[str, ...]
    scores: Mapping[str, float]
    average: float | None
    explanation: ExplanationMetrics | None
    cjk: CjkMetrics | None
    model_name: str | None
    sample_keys: frozenset[tuple[int, str]]


# --- загрузка ----------------------------------------------------------------


def _load_records(path: Path) -> list[EvalRecord]:
    """Прочитать и провалидировать JSON-прогон.

    Args:
        path: путь к файлу, который пишет `evaluate_llm._dump_json`.

    Raises:
        EvalFileError: файл не читается, JSON битый, массив пуст или запись
            не соответствует схеме.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EvalFileError(f"файл не найден: {path}") from exc
    except OSError as exc:
        raise EvalFileError(f"не удалось прочитать {path}: {exc}") from exc

    try:
        payload: object = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise EvalFileError(f"{path}: некорректный JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise EvalFileError(f"{path}: ожидался JSON-массив записей")
    if not payload:
        raise EvalFileError(f"{path}: массив записей пуст")

    return [
        _parse_record(item, index, path)
        for index, item in enumerate(payload, start=1)
    ]


def _parse_record(item: object, index: int, path: Path) -> EvalRecord:
    """Разобрать одну запись, отклоняя неверные типы/диапазоны."""
    if not isinstance(item, dict):
        raise EvalFileError(f"{path}: запись #{index} — не JSON-объект")
    record = cast(dict[str, object], item)

    game_id = record.get("game_id")
    if isinstance(game_id, bool) or not isinstance(game_id, int):
        raise EvalFileError(f"{path}: запись #{index}.game_id должен быть целым числом")

    level = record.get("level")
    if not isinstance(level, str) or not level.strip():
        raise EvalFileError(f"{path}: запись #{index}.level пуст")

    error = record.get("error")
    if error is not None and not isinstance(error, str):
        raise EvalFileError(f"{path}: запись #{index}.error должен быть строкой или null")

    commentary_id = record.get("commentary_id")
    if commentary_id is not None and (
        isinstance(commentary_id, bool) or not isinstance(commentary_id, int)
    ):
        raise EvalFileError(
            f"{path}: запись #{index}.commentary_id должен быть целым числом или null"
        )

    return EvalRecord(
        game_id=game_id,
        level=level.strip(),
        scores=_parse_scores(record.get("scores"), index, path),
        has_explanation=_parse_optional_bool(
            record.get("has_explanation"), "has_explanation", index, path
        ),
        cjk_chars=_parse_cjk_chars(record.get("cjk_chars"), index, path),
        cjk_share=_parse_cjk_share(record.get("cjk_share"), index, path),
        error=error,
        # Текст есть у успешной генерации и у строки, где упал только судья
        # (тогда commentary_id заполнен, а error начинается с «judge:»).
        has_text=error is None or commentary_id is not None,
        model_name=_parse_model_name(record),
    )


def _parse_scores(
    raw: object, index: int, path: Path
) -> dict[str, float]:
    """Разобрать `scores`: только три известные шкалы в диапазоне 1..10."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise EvalFileError(f"{path}: запись #{index}.scores должен быть объектом")
    mapping = cast(dict[str, object], raw)
    scores: dict[str, float] = {}
    for scale in SCALES:
        value = mapping.get(scale)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvalFileError(
                f"{path}: запись #{index}.scores.{scale} должен быть числом"
            )
        score = float(value)
        if not math.isfinite(score) or not 1.0 <= score <= 10.0:
            raise EvalFileError(
                f"{path}: запись #{index}.scores.{scale}={score} вне диапазона 1..10"
            )
        scores[scale] = score
    return scores


def _parse_optional_bool(
    raw: object, field: str, index: int, path: Path
) -> bool | None:
    if raw is None:
        return None
    if not isinstance(raw, bool):
        raise EvalFileError(f"{path}: запись #{index}.{field} должен быть boolean")
    return raw


def _parse_cjk_chars(raw: object, index: int, path: Path) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise EvalFileError(
            f"{path}: запись #{index}.cjk_chars должен быть целым >= 0"
        )
    return raw


def _parse_cjk_share(raw: object, index: int, path: Path) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise EvalFileError(f"{path}: запись #{index}.cjk_share должен быть числом")
    share = float(raw)
    if not math.isfinite(share) or not 0.0 <= share <= 1.0:
        raise EvalFileError(
            f"{path}: запись #{index}.cjk_share={share} вне диапазона 0..1"
        )
    return share


def _parse_model_name(record: Mapping[str, object]) -> str | None:
    """Метка версии: `model_name` (итерация 60), иначе API-имя `model`."""
    for key in ("model_name", "model"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# --- метрики -----------------------------------------------------------------


def compute_metrics(records: Sequence[EvalRecord]) -> EvalMetrics:
    """Посчитать сводные метрики прогона.

    Args:
        records: разобранные записи одного JSON-файла.

    Returns:
        `EvalMetrics` с средними по шкалам, объяснениями, CJK и ключами
        выборки `(game_id, level)` для проверки same-sample.
    """
    totals = dict.fromkeys(SCALES, 0.0)
    counts = dict.fromkeys(SCALES, 0)
    scored = 0
    for record in records:
        if not record.scores:
            continue
        scored += 1
        for scale, value in record.scores.items():
            totals[scale] += value
            counts[scale] += 1

    scores = {
        scale: totals[scale] / counts[scale]
        for scale in SCALES
        if counts[scale]
    }
    average = sum(scores.values()) / len(scores) if scores else None

    evaluated = [record for record in records if record.has_text]
    names = sorted({record.model_name for record in records if record.model_name})

    return EvalMetrics(
        records=len(records),
        scored=scored,
        levels=tuple(sorted({record.level for record in records})),
        scores=scores,
        average=average,
        explanation=_explanation_metrics(evaluated),
        cjk=_cjk_metrics(evaluated),
        model_name=names[0] if len(names) == 1 else None,
        sample_keys=frozenset(
            (record.game_id, record.level) for record in records
        ),
    )


def _explanation_metrics(
    evaluated: Sequence[EvalRecord],
) -> ExplanationMetrics | None:
    """Доля объяснений; None, если поле отсутствует хотя бы у одной записи."""
    if not evaluated or any(
        record.has_explanation is None for record in evaluated
    ):
        return None
    explained = sum(1 for record in evaluated if record.has_explanation)
    return ExplanationMetrics(explained=explained, evaluated=len(evaluated))


def _cjk_metrics(evaluated: Sequence[EvalRecord]) -> CjkMetrics | None:
    """CJK-метрики; None, если поле отсутствует хотя бы у одной записи."""
    if not evaluated or any(
        record.cjk_share is None or record.cjk_chars is None
        for record in evaluated
    ):
        return None
    shares = [record.cjk_share for record in evaluated if record.cjk_share is not None]
    chars = [
        record.cjk_chars for record in evaluated if record.cjk_chars is not None
    ]
    return CjkMetrics(
        share=sum(shares) / len(shares),
        comments_with_cjk=sum(1 for value in chars if value > 0),
        evaluated=len(evaluated),
        chars=sum(chars),
    )


# --- форматирование ----------------------------------------------------------


def _format_score(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _format_delta(
    delta: float | None, *, digits: int = 2, suffix: str = ""
) -> str:
    """Дельта со знаком: `+0.50`, `-0.20`, `0.00`; None → «—»."""
    if delta is None:
        return "—"
    if abs(delta) < 0.5 * 10 ** (-digits):
        return f"{0.0:.{digits}f}{suffix}"
    return f"{delta:+.{digits}f}{suffix}"


def _format_percent(share: float, *, digits: int = 1) -> str:
    return f"{100.0 * share:.{digits}f}%"


def _short_label(text: str) -> str:
    match = _VERSION_SUFFIX.search(text)
    if match:
        return f"v{match.group(1)}"
    return text


def _default_label(path: Path, metrics: EvalMetrics) -> str:
    if metrics.model_name:
        return _short_label(metrics.model_name)
    return _short_label(path.stem)


def _source_line(metrics: EvalMetrics) -> str:
    model = (
        f"модель `{metrics.model_name}`" if metrics.model_name else "модель не указана"
    )
    levels = ", ".join(f"`{level}`" for level in metrics.levels) or "—"
    return (
        f"{model} · записей: {metrics.records}, оценено: {metrics.scored} "
        f"· уровни: {levels}"
    )


def _metric_cell(metrics: ExplanationMetrics | None) -> str:
    if metrics is None:
        return "—"
    return (
        f"{_format_percent(metrics.rate)} "
        f"({metrics.explained}/{metrics.evaluated})"
    )


def _cjk_cell(metrics: CjkMetrics | None) -> str:
    if metrics is None:
        return "—"
    return (
        f"{_format_percent(metrics.share, digits=2)} "
        f"({metrics.comments_with_cjk}/{metrics.evaluated})"
    )


def render_comparison(
    baseline: EvalMetrics,
    new: EvalMetrics,
    *,
    baseline_label: str,
    new_label: str,
    baseline_path: Path,
    new_path: Path,
) -> str:
    """Собрать Markdown-отчёт сравнения (таблица + итог)."""
    lines = [
        f"# Сравнение LLM-eval: {baseline_label} (Baseline) → {new_label} (New)",
        "",
        f"**Baseline:** `{baseline_path.name}` · {_source_line(baseline)}",
        f"**New:** `{new_path.name}` · {_source_line(new)}",
        "",
        f"| Шкала | {baseline_label} (Baseline) | {new_label} (New) | Дельта |",
        "| :--- | ---: | ---: | ---: |",
    ]

    for scale in SCALES:
        old = baseline.scores.get(scale)
        fresh = new.scores.get(scale)
        delta = None if old is None or fresh is None else fresh - old
        lines.append(
            f"| {SCALE_LABELS[scale]} | {_format_score(old)} | "
            f"{_format_score(fresh)} | {_format_delta(delta)} |"
        )

    average_delta = (
        None
        if baseline.average is None or new.average is None
        else new.average - baseline.average
    )
    lines.append(
        f"| **Средняя** | **{_format_score(baseline.average)}** | "
        f"**{_format_score(new.average)}** | **{_format_delta(average_delta)}** |"
    )

    explanation_delta = None
    if baseline.explanation is not None and new.explanation is not None:
        explanation_delta = 100.0 * (
            new.explanation.rate - baseline.explanation.rate
        )
    lines.append(
        f"| Объяснения | {_metric_cell(baseline.explanation)} | "
        f"{_metric_cell(new.explanation)} | "
        f"{_format_delta(explanation_delta, digits=1, suffix=' п.п.')} |"
    )

    cjk_delta = None
    if baseline.cjk is not None and new.cjk is not None:
        cjk_delta = 100.0 * (new.cjk.share - baseline.cjk.share)
    lines.append(
        f"| CJK-символы | {_cjk_cell(baseline.cjk)} | {_cjk_cell(new.cjk)} | "
        f"{_format_delta(cjk_delta, digits=2, suffix=' п.п.')} |"
    )

    lines += ["", "**Итог:**", ""]
    lines.append(_average_verdict(baseline, new, average_delta))
    if baseline.explanation is not None and new.explanation is not None:
        lines.append(
            f"- Объяснения: **{_format_percent(baseline.explanation.rate)} → "
            f"{_format_percent(new.explanation.rate)}** "
            f"({_format_delta(explanation_delta, digits=1, suffix=' п.п.')})."
        )
    if baseline.cjk is not None and new.cjk is not None:
        lines.append(
            f"- CJK: **{_format_percent(baseline.cjk.share, digits=2)} → "
            f"{_format_percent(new.cjk.share, digits=2)}** "
            f"({_format_delta(cjk_delta, digits=2, suffix=' п.п.')})."
        )

    return "\n".join(lines)


def _average_verdict(
    baseline: EvalMetrics, new: EvalMetrics, delta: float | None
) -> str:
    if baseline.average is None or new.average is None or delta is None:
        return "- Средняя: — (недостаточно оценённых записей)."
    threshold = "порог **7/10** достигнут" if new.average >= 7 else "порог **7/10** не достигнут"
    if abs(delta) < 0.005:
        movement = "без изменений"
    elif delta > 0:
        movement = f"рост на **{delta:.2f}**"
    else:
        movement = f"снижение на **{abs(delta):.2f}**"
    return (
        f"- Средняя: **{baseline.average:.2f} → {new.average:.2f}** "
        f"({movement}); {threshold}."
    )


# --- сравнение выборок -------------------------------------------------------


def _sample_differences(
    baseline: EvalMetrics, new: EvalMetrics
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    return (
        sorted(baseline.sample_keys - new.sample_keys),
        sorted(new.sample_keys - baseline.sample_keys),
    )


def _format_key(key: tuple[int, str]) -> str:
    game_id, level = key
    return f"game_id={game_id}, level={level}"


def _comparability_warnings(
    baseline: EvalMetrics, new: EvalMetrics
) -> list[str]:
    """Предупреждения, без которых дельты могут быть межвыборочными."""
    warnings: list[str] = []
    only_baseline, only_new = _sample_differences(baseline, new)
    if only_baseline or only_new:
        parts = []
        if only_baseline:
            sample = ", ".join(_format_key(key) for key in only_baseline[:3])
            suffix = ", …" if len(only_baseline) > 3 else ""
            parts.append(f"только в baseline: {len(only_baseline)} ({sample}{suffix})")
        if only_new:
            sample = ", ".join(_format_key(key) for key in only_new[:3])
            suffix = ", …" if len(only_new) > 3 else ""
            parts.append(f"только в new: {len(only_new)} ({sample}{suffix})")
        warnings.append(
            "наборы партий различаются — дельты межвыборочные: "
            + "; ".join(parts)
        )
    if (
        baseline.model_name is not None
        and baseline.model_name == new.model_name
    ):
        warnings.append(
            f"у обоих прогонов метка модели «{baseline.model_name}» — "
            "проверьте, что `--model-name` для v4 действительно менялся"
        )
    return warnings


def _configure_utf8_output() -> None:
    """Перевести stdout/stderr в UTF-8 (Windows-консоль по умолчанию cp1251).

    В таблице есть символ «→» и кириллица; cp1251 не умеет «→», и `print`
    падал с `UnicodeEncodeError`. В тестах и на Linux `reconfigure` может
    отсутствовать — тогда молча оставляем поток как есть.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                continue


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Сравнить два JSON-прогона LLM-as-judge и вывести Markdown-таблицу "
            "с дельтами по шкалам, объяснениям и CJK."
        ),
    )
    parser.add_argument(
        "baseline",
        type=Path,
        help="JSON baseline-прогона (например, llm_eval_results_v3.json)",
    )
    parser.add_argument(
        "new",
        type=Path,
        help="JSON нового прогона (например, llm_eval_results_v4.json)",
    )
    parser.add_argument(
        "--baseline-label",
        default=None,
        help="подпись baseline-колонки (по умолчанию — версия из model_name)",
    )
    parser.add_argument(
        "--new-label",
        default=None,
        help="подпись new-колонки (по умолчанию — версия из model_name)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="дополнительно записать Markdown-таблицу в файл",
    )
    parser.add_argument(
        "--strict-samples",
        action="store_true",
        help="вернуть код 1, если наборы (game_id, level) различаются",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI. Коды: 0 — сравнение выведено; 1 — ошибка/разные наборы."""
    _configure_utf8_output()
    args = _parse_args(argv)
    try:
        baseline_records = _load_records(args.baseline)
        new_records = _load_records(args.new)
    except EvalFileError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    baseline = compute_metrics(baseline_records)
    new = compute_metrics(new_records)
    baseline_label = args.baseline_label or _default_label(args.baseline, baseline)
    new_label = args.new_label or _default_label(args.new, new)

    warnings = _comparability_warnings(baseline, new)
    for warning in warnings:
        print(f"ВНИМАНИЕ: {warning}", file=sys.stderr)

    only_baseline, only_new = _sample_differences(baseline, new)
    if args.strict_samples and (only_baseline or only_new):
        print(
            "Сравнение остановлено: наборы партий различаются "
            "(--strict-samples).",
            file=sys.stderr,
        )
        return 1

    report = render_comparison(
        baseline,
        new,
        baseline_label=baseline_label,
        new_label=new_label,
        baseline_path=args.baseline,
        new_path=args.new,
    )
    print(report)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
        print(f"Таблица записана: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
