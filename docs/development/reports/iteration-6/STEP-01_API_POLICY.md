# STEP-01 — API: ходы, мотивы и рекомендации (T-012, KI-020)

**Дата:** 2026-10-28
**Итерация:** 6 — API + LLM (T-012, T-013)
**Статус:** ✅ выполнен

---

## 1. Что изменилось в ответе `/v1/analyze`

Добавлен блок `moves` — по элементу на каждый полуход:

```json
{
  "ply": 1,
  "san": "d4",
  "uci": "d2d4",
  "eval_cp": 22,
  "mate": null,
  "best_move_san": "e4",
  "delta_cp": 5,
  "classification": "good",
  "motifs": [
    {"type": "king_safety", "description": "Король белых в центре (e1), ферзи на доске", "importance": 4}
  ],
  "recommended_move": {"san": "e4", "uci": "e2e4", "eval_cp": 27, "mate": null}
}
```

`recommended_move` — результат `choose_recommended_move(lines, level, fen)`,
где `fen` — позиция **до** хода, а `lines` — MultiPV этой позиции (те самые,
что пишутся в `moves.multipv`). То есть рекомендация отвечает на вопрос
«что стоило сыграть этому игроку под его уровень».

Почему `mate` добавлен к `recommended_move`: в линии может быть мат, и
`eval_cp = null` без `mate` выглядел бы как «нет оценки». Поле аддитивное,
на контракт из постановки (san/uci/eval_cp) не влияет.

## 2. Где считается

`_build_moves(parsed, analyses, level)` в `app/api/v1/analyze.py`:

* `fens_before = [parsed.initial_fen, *[m.fen for m in parsed.moves[:-1]]]` —
  позиции до каждого хода (с учётом FEN-старта);
* `_recommended(...)` вызывает Policy и конвертирует UCI→SAN на этой
  позиции (`_uci_to_san`, при сбое отдаёт UCI);
* `eval_cp`/`mate` — те же, что пишутся в БД (`_db_eval`, оценка за белых).

## 3. KI-020: мат разрешён всегда

В `_choose_beginner` первым шагом:

```python
for line in candidates:
    if line.mate is not None and line.mate > 0:
        return line
```

Это выполняется **до** фильтров «ранний ферзь» и «висит фигура», поэтому
жертва, ведущая к мату, не отбрасывается. Отрицательный `mate` (мат
получаем мы) фильтры не обходит — иначе политика советовала бы
проигрывающие ходы.

Тесты: `test_beginner_allows_early_queen_when_it_mates`,
`test_beginner_allows_hanging_piece_when_it_mates`,
`test_beginner_does_not_bypass_filters_for_losing_mate`,
`test_beginner_and_gm_disagree_on_premature_queen`.

## 4. Проверки

| Проверка | Результат |
| :--- | :--- |
| `moves` в ответе | 53 элемента, у всех есть `motifs` и `recommended_move` |
| Первый ход | `d4`, UCI `d2d4`, рекомендация `e4` (club: разница 5 cp < 20 → альтернатива) |
| SLA | не изменился: ≈ 15 с на 53 полухода |
| Policy: beginner vs gm | расходятся на `2.Qh5` (`d1h5` у gm, `f1c4` у beginner) |
| KI-020 | закрыт, 4 теста |

## 5. Что дальше

Шаг 2 — LLM-адаптер.
