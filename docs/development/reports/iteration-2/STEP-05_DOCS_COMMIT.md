# STEP-05 — Документация, отчёт итерации и коммит

**Дата:** 2026-10-28
**Итерация:** 2 — Ingestion (T-007)
**Статус:** ✅ выполнен

---

## 1. Финальный прогон

```
ruff check .   → All checks passed!                            [exit=0]
mypy app       → Success: no issues found in 15 files          [exit=0]
pytest -q      → 38 passed, 1 xfailed, 1 warning in 3.81s      [exit=0]
```

`xfailed` — `test_parse_pgn_unknown_token_is_rejected` (KI-012,
`strict=True`): это не скрытый пропуск, а зафиксированное ограничение
`python-chess`.

## 2. Обновлённая документация

| Файл | Что |
| :--- | :--- |
| `TODO.md` | T-007 → `[x]`; T-008 остаётся `[ ]` |
| `docs/development/RELEASES.md` | Раздел «Итерация 2 — Ingestion (T-007)»: что сделано, что не сделано (таблица KI), метрики, что дальше |
| `docs/CHANGELOG.md` | `[Unreleased] → Added`: ingestion, ошибки, модели, тесты, фикстура; `Changed`: `RUF001`, комментарий к `phase_breakdown` |
| `docs/KNOWN_ISSUES.md` | +KI-010 (раскладка тестов), +KI-011 (нет базы ECO), +KI-012 (тихий обрыв PGN); сводка 9 → 12 |
| `README.md` | Бейдж тестов, блок «Статус проекта»: итерация 2, 38 passed + 1 xfailed, строка «Ingestion», KI — 12 |
| `docs/README.md` | Ссылки на `tests/unit/test_ingestion.py`, `test_game.pgn`, отчёты итерации 2; `KNOWN_ISSUES` → KI-012 |
| `docs/development/PROMPT_CURRENT.md` | Версия 1.1, метрики итерации 2, следующий шаг — T-008 |
| `app/db/models.py` | Комментарий к `games.phase_breakdown` = фактический JSON (`opening_end_ply` / `endgame_start_ply`) |
| `tests/fixtures/README.md` | Инварианты двух фикстур, provenance `test_game.pgn` |
| `pyproject.toml` | `RUF001` в `ignore` (русские сообщения) |

## 3. Коммит

```
feat(ingestion): normalize PGN/FEN into ParsedGame (T-007)
```

Состав:

- `app/errors.py` (new), `app/ingestion/{__init__,models,eco,parser}.py` (new);
- `tests/unit/{__init__,test_ingestion}.py` (new),
  `tests/fixtures/test_game.pgn` (new), `tests/fixtures/README.md` (upd);
- `pyproject.toml` (RUF001), `app/db/models.py` (комментарий);
- docs: `RELEASES.md`, `CHANGELOG.md`, `KNOWN_ISSUES.md`, `README.md`,
  `docs/README.md`, `PROMPT_CURRENT.md`, отчёты `iteration-2/`.

Хеш — `git log -1 --oneline`.

## 4. Definition of Done — T-007

| Критерий | Статус | Факт |
| :--- | :---: | :--- |
| Создан пакет `app/ingestion/` со всеми файлами | ✅ | `__init__.py`, `models.py`, `parser.py`, `eco.py` |
| `InvalidPgnError` / `InvalidFenError` в `app/errors.py`, корректно обрабатываются | ✅ | поднимаются на всех ветках, тесты на пустой/мусорный PGN, нелегальный ход, FEN |
| `parse_pgn` возвращает полную `ParsedGame` | ✅ | 53 полухода, метаданные, ECO, фазы, `final_fen` |
| `parse_fen` валидирует FEN | ✅ | стартовая позиция, синтаксис, невозможная позиция, пустая строка |
| Тесты `tests/unit/test_ingestion.py` написаны и проходят | ✅ | 33 теста, `pytest` зелёный |
| `ruff check .` и `mypy app` без ошибок | ✅ | 0 / 0 |
| `TODO.md`: T-007 `[x]` | ✅ | отмечено |
| `RELEASES.md`: секция «Итерация 2» | ✅ | заполнена |
| 10 тестовых PGN (DoD из `TODO.md`) | ✅ | 2 фикстуры + 8 дебютов в параметризованном тесте |

## 5. Наблюдения, которые стоит помнить T-008

1. **`ParsedMove.fen` — позиция ПОСЛЕ хода.** Движку для оценки нужно
   передавать доску «после хода» (или «до» + `move`) — не перепутать при
   интеграции.
2. **`is_capture`/`is_check` уже посчитаны** — движку их дублировать не
   нужно, но классификация (blunder/mistake/…) будет сравнивать
   `eval_cp` до и после.
3. **Эндшпиль в тестовой партии не наступает** — для тестов фаз T-008
   нужен отдельный PGN с разменом ферзей (или FEN-старт, как в
   `SPARSE_ENDGAME_PGN`).
4. **KI-012** может «тихо» испортить бенчмарк на реальных PGN — при
   ingest-е больших файлов проверять `ply_count` против `PlyCount`
   заголовка.
