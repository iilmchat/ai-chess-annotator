"""Unit-тесты сравнения прогонов LLM-eval (итерация 62).

Проверяем `scripts/compare_evals.py` на синтетических JSON-файлах: средние по
шкалам, дельты, доля объяснений, CJK-метрика, формат Markdown-таблицы и
коды возврата CLI. Ни БД, ни сети, ни Stockfish не нужны.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from scripts.compare_evals import (
    CjkMetrics,
    EvalFileError,
    EvalRecord,
    ExplanationMetrics,
    _configure_utf8_output,
    _load_records,
    compute_metrics,
    main,
    render_comparison,
)

V3 = "qwen-7b-tokenizer_gguf-v3"
V4 = "qwen-7b-tokenizer_gguf-v4"


def _record(
    *,
    game_id: int = -1,
    level: str = "club",
    scores: tuple[float, float, float] | None = (2.0, 3.0, 4.0),
    has_explanation: bool = True,
    cjk_chars: int = 0,
    cjk_share: float = 0.0,
    error: str | None = None,
    commentary_id: int | None = 1,
    model_name: str | None = V3,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "game_id": game_id,
        "level": level,
        "commentary_id": commentary_id,
        "model": "qwen-7b-tokenizer",
        "model_name": model_name,
        "has_explanation": has_explanation,
        "cjk_chars": cjk_chars,
        "cjk_share": cjk_share,
        "scores": {}
        if scores is None
        else {
            "factual_accuracy": scores[0],
            "usefulness": scores[1],
            "style": scores[2],
        },
        "judge_comment": "",
        "error": error,
    }
    return payload


def _write(tmp_path: Path, name: str, records: list[dict[str, object]]) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _parse(records: list[dict[str, object]], tmp_path: Path) -> list[EvalRecord]:
    return _load_records(_write(tmp_path, "eval.json", records))


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    baseline = _write(tmp_path, "v3.json", [_record()])
    new = _write(
        tmp_path,
        "v4.json",
        [
            _record(
                scores=(3.0, 4.0, 5.0),
                has_explanation=False,
                cjk_chars=3,
                cjk_share=0.05,
                model_name=V4,
            )
        ],
    )
    return baseline, new


# --- метрики -----------------------------------------------------------------


def test_compute_metrics_averages_each_scale(tmp_path: Path) -> None:
    records = _parse(
        [
            _record(scores=(2.0, 4.0, 6.0)),
            _record(scores=(4.0, 6.0, 8.0), game_id=-2),
        ],
        tmp_path,
    )

    metrics = compute_metrics(records)

    assert metrics.scores == {
        "factual_accuracy": pytest.approx(3.0),
        "usefulness": pytest.approx(5.0),
        "style": pytest.approx(7.0),
    }
    assert metrics.average == pytest.approx(5.0)
    assert metrics.records == 2
    assert metrics.scored == 2


def test_unscored_rows_do_not_change_scale_averages(tmp_path: Path) -> None:
    records = _parse(
        [
            _record(scores=(3.0, 3.0, 3.0)),
            _record(scores=None, error="judge: boom", game_id=-2),
        ],
        tmp_path,
    )

    metrics = compute_metrics(records)

    assert metrics.scored == 1
    assert metrics.scores["factual_accuracy"] == pytest.approx(3.0)


def test_levels_and_sample_keys_are_collected(tmp_path: Path) -> None:
    records = _parse(
        [
            _record(game_id=-1, level="club"),
            _record(game_id=-2, level="gm"),
        ],
        tmp_path,
    )

    metrics = compute_metrics(records)

    assert metrics.levels == ("club", "gm")
    assert metrics.sample_keys == frozenset({(-1, "club"), (-2, "gm")})


# --- объяснения --------------------------------------------------------------


def test_explanation_rate_excludes_generation_errors(tmp_path: Path) -> None:
    records = _parse(
        [
            _record(has_explanation=True),
            _record(has_explanation=False, game_id=-2),
            _record(
                has_explanation=False,
                game_id=-3,
                commentary_id=None,
                error="commentary 503: unavailable",
            ),
        ],
        tmp_path,
    )

    metrics = compute_metrics(records)

    assert isinstance(metrics.explanation, ExplanationMetrics)
    assert metrics.explanation.explained == 1
    assert metrics.explanation.evaluated == 2
    assert metrics.explanation.rate == pytest.approx(0.5)


def test_explanation_rate_includes_judge_errors_when_text_exists(
    tmp_path: Path,
) -> None:
    records = _parse(
        [
            _record(has_explanation=False),
            _record(
                has_explanation=True,
                game_id=-2,
                scores=None,
                error="judge: truncated JSON",
                commentary_id=77,
            ),
        ],
        tmp_path,
    )

    metrics = compute_metrics(records)

    assert isinstance(metrics.explanation, ExplanationMetrics)
    assert metrics.explanation.explained == 1
    assert metrics.explanation.evaluated == 2


def test_explanation_is_unavailable_for_old_json_schema(tmp_path: Path) -> None:
    record = _record()
    record.pop("has_explanation")

    metrics = compute_metrics(_parse([record], tmp_path))

    assert metrics.explanation is None


# --- CJK ---------------------------------------------------------------------


def test_cjk_share_is_mean_of_comment_shares(tmp_path: Path) -> None:
    records = _parse(
        [
            _record(cjk_chars=5, cjk_share=0.1),
            _record(cjk_chars=0, cjk_share=0.0, game_id=-2),
            _record(cjk_chars=2, cjk_share=0.05, game_id=-3),
        ],
        tmp_path,
    )

    metrics = compute_metrics(records)

    assert isinstance(metrics.cjk, CjkMetrics)
    assert metrics.cjk.share == pytest.approx(0.05)
    assert metrics.cjk.comments_with_cjk == 2
    assert metrics.cjk.evaluated == 3
    assert metrics.cjk.chars == 7


def test_cjk_is_unavailable_for_old_json_schema(tmp_path: Path) -> None:
    record = _record()
    record.pop("cjk_chars")
    record.pop("cjk_share")

    metrics = compute_metrics(_parse([record], tmp_path))

    assert metrics.cjk is None


# --- таблица -----------------------------------------------------------------


def _render(tmp_path: Path) -> str:
    baseline_path, new_path = _pair(tmp_path)
    baseline = compute_metrics(_load_records(baseline_path))
    new = compute_metrics(_load_records(new_path))
    return render_comparison(
        baseline,
        new,
        baseline_label="v3",
        new_label="v4",
        baseline_path=baseline_path,
        new_path=new_path,
    )


def test_table_has_scale_rows_and_signed_deltas(tmp_path: Path) -> None:
    table = _render(tmp_path)

    assert "| Шкала | v3 (Baseline) | v4 (New) | Дельта |" in table
    assert "| Факт. точность | 2.00 | 3.00 | +1.00 |" in table
    assert "| Полезность | 3.00 | 4.00 | +1.00 |" in table
    assert "| Стиль | 4.00 | 5.00 | +1.00 |" in table
    assert "| **Средняя** | **3.00** | **4.00** | **+1.00** |" in table


def test_table_shows_negative_and_zero_deltas(tmp_path: Path) -> None:
    baseline_path = _write(
        tmp_path, "old.json", [_record(scores=(4.0, 4.0, 4.0))]
    )
    new_path = _write(
        tmp_path,
        "new.json",
        [_record(scores=(3.0, 4.0, 4.0), model_name=V4)],
    )
    table = render_comparison(
        compute_metrics(_load_records(baseline_path)),
        compute_metrics(_load_records(new_path)),
        baseline_label="v3",
        new_label="v4",
        baseline_path=baseline_path,
        new_path=new_path,
    )

    assert "| Факт. точность | 4.00 | 3.00 | -1.00 |" in table
    assert "| Полезность | 4.00 | 4.00 | 0.00 |" in table
    assert "| Стиль | 4.00 | 4.00 | 0.00 |" in table
    assert "| **Средняя** | **4.00** | **3.67** | **-0.33** |" in table
    assert "снижение на **0.33**" in table


def test_table_has_explanation_and_cjk_rows(tmp_path: Path) -> None:
    baseline_path, new_path = _pair(tmp_path)
    table = render_comparison(
        compute_metrics(_load_records(baseline_path)),
        compute_metrics(_load_records(new_path)),
        baseline_label="v3",
        new_label="v4",
        baseline_path=baseline_path,
        new_path=new_path,
    )

    assert "| Объяснения | 100.0% (1/1) | 0.0% (0/1) | -100.0 п.п. |" in table
    assert "| CJK-символы | 0.00% (0/1) | 5.00% (1/1) | +5.00 п.п. |" in table


def test_table_verdict_mentions_growth_and_threshold(tmp_path: Path) -> None:
    table = _render(tmp_path)

    assert "**3.00 → 4.00**" in table
    assert "рост на **1.00**" in table
    assert "порог **7/10** не достигнут" in table


def test_old_schema_renders_dashes_instead_of_crashing(tmp_path: Path) -> None:
    record = _record()
    record.pop("has_explanation")
    record.pop("cjk_chars")
    record.pop("cjk_share")
    baseline_path = _write(tmp_path, "old.json", [record])
    new_path = _write(tmp_path, "new.json", [record, _record(game_id=-2)])
    table = render_comparison(
        compute_metrics(_load_records(baseline_path)),
        compute_metrics(_load_records(new_path)),
        baseline_label="old",
        new_label="new",
        baseline_path=baseline_path,
        new_path=new_path,
    )

    assert "| Объяснения | — | — | — |" in table
    assert "| CJK-символы | — | — | — |" in table


# --- CLI ---------------------------------------------------------------------


def test_cli_prints_table_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, new = _pair(tmp_path)

    assert main([str(baseline), str(new)]) == 0

    captured = capsys.readouterr()
    assert "| Шкала | v3 (Baseline) | v4 (New) | Дельта |" in captured.out
    assert "| Факт. точность | 2.00 | 3.00 | +1.00 |" in captured.out


def test_cli_derives_labels_from_model_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, new = _pair(tmp_path)

    assert main([str(baseline), str(new)]) == 0

    assert "v3 (Baseline)" in capsys.readouterr().out


def test_cli_accepts_label_overrides(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, new = _pair(tmp_path)

    assert (
        main(
            [
                str(baseline),
                str(new),
                "--baseline-label",
                "before",
                "--new-label",
                "after",
            ]
        )
        == 0
    )

    assert "| Шкала | before (Baseline) | after (New) | Дельта |" in capsys.readouterr().out


def test_cli_writes_markdown_to_output_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline, new = _pair(tmp_path)
    output = tmp_path / "comparison.md"

    assert main([str(baseline), str(new), "--output", str(output)]) == 0

    written = output.read_text(encoding="utf-8")
    assert "| Факт. точность |" in written
    assert written.endswith("\n")
    assert "Таблица записана" in capsys.readouterr().err


def test_cli_missing_file_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = _write(tmp_path, "v3.json", [_record()])

    assert main([str(tmp_path / "absent.json"), str(existing)]) == 1

    assert "файл не найден" in capsys.readouterr().err


def test_cli_invalid_json_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    existing = _write(tmp_path, "v3.json", [_record()])

    assert main([str(broken), str(existing)]) == 1

    assert "некорректный JSON" in capsys.readouterr().err


def test_cli_non_list_payload_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = tmp_path / "dict.json"
    payload.write_text(json.dumps({"scores": {}}), encoding="utf-8")
    existing = _write(tmp_path, "v3.json", [_record()])

    assert main([str(payload), str(existing)]) == 1

    assert "ожидался JSON-массив" in capsys.readouterr().err


def test_cli_empty_list_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = _write(tmp_path, "empty.json", [])
    existing = _write(tmp_path, "v3.json", [_record()])

    assert main([str(empty), str(existing)]) == 1

    assert "массив записей пуст" in capsys.readouterr().err


def test_cli_score_out_of_range_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _write(tmp_path, "bad.json", [_record(scores=(11.0, 3.0, 4.0))])
    existing = _write(tmp_path, "v3.json", [_record()])

    assert main([str(bad), str(existing)]) == 1

    assert "вне диапазона 1..10" in capsys.readouterr().err


def test_cli_warns_when_samples_differ(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write(tmp_path, "v3.json", [_record(game_id=-1)])
    new = _write(tmp_path, "v4.json", [_record(game_id=-2, model_name=V4)])

    assert main([str(baseline), str(new)]) == 0

    captured = capsys.readouterr()
    assert "наборы партий различаются" in captured.err
    assert "только в baseline: 1" in captured.err


def test_cli_strict_samples_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write(tmp_path, "v3.json", [_record(game_id=-1)])
    new = _write(tmp_path, "v4.json", [_record(game_id=-2, model_name=V4)])

    assert main([str(baseline), str(new), "--strict-samples"]) == 1

    assert "Сравнение остановлено" in capsys.readouterr().err


def test_cli_warns_when_model_labels_are_equal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write(tmp_path, "v3.json", [_record(model_name=V3)])
    new = _write(tmp_path, "v4.json", [_record(model_name=V3)])

    assert main([str(baseline), str(new)]) == 0

    assert "метка модели" in capsys.readouterr().err


def test_load_records_rejects_non_object_item(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["not an object"]), encoding="utf-8")

    with pytest.raises(EvalFileError, match="не JSON-объект"):
        _load_records(path)


class _FakeStream:
    """Минимальный поток с reconfigure — проверка UTF-8 без реальной консоли."""

    def __init__(self) -> None:
        self.encodings: list[str] = []

    def reconfigure(self, *, encoding: str) -> None:
        self.encodings.append(encoding)


def test_configure_utf8_output_reconfigures_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _FakeStream()
    err = _FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    _configure_utf8_output()

    assert out.encodings == ["utf-8"]
    assert err.encodings == ["utf-8"]


def test_configure_utf8_output_ignores_streams_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    _configure_utf8_output()  # не должно упасть: у StringIO нет reconfigure
