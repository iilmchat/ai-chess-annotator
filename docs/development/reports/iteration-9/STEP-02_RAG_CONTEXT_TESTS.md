# STEP-02 — RAG в комментарии, тесты, коммит (T-017)

**Дата:** 2026-10-28
**Итерация:** 9 — RAG (T-017)
**Статус:** ✅ выполнен

---

## 1. RAG-контекст для гроссмейстерского уровня

`app/rag/context.py`:

```python
make_rag_context_builder(settings, session_factory=SessionLocal)
    → async (parsed, analyses) -> str | None
```

* работает только при `settings.rag_enabled` и только если есть моменты
  `blunder|mistake|inaccuracy`;
* берёт до 3 ключевых моментов, для каждого — `find_similar_positions(fen_before, top_k=2)`;
* формирует раздел «# Похожие позиции из базы аннотаций (RAG)» с
  подзаголовками по полуходам;
* любая ошибка поиска логируется (`rag_lookup_failed`) и не ломает
  генерацию — контекст просто не добавляется.

В `app/main.py` в lifespan: `app.state.rag_context_builder = make_rag_context_builder(cfg)`.
`/v1/commentary` вызывает builder только для `level == "gm"` (SPEC §3:
RAG нужен гроссмейстеру, новичку — нет).

## 2. Тесты (15)

`tests/unit/test_rag.py` (6): размерность 1024, L2-норма, детерминизм,
разные позиции → разные векторы, пустой текст → нулевой вектор,
`position_document` содержит FEN дважды, валидация размерности.

`tests/integration/test_rag.py` (9):

| Тест | Что проверяет |
| :--- | :--- |
| `test_parse_pgn_games_reads_all_games` | 2 партии из одного потока |
| `test_ingest_saves_annotations_and_embeddings` | 2 записи, эмбеддинги, аннотации, годы, `source` |
| `test_find_similar_positions_returns_matching_game` | запрос по FEN находит нужную партию первой |
| `test_find_similar_positions_validates_fen` | мусорный FEN → ошибка |
| `test_ingest_endpoint_loads_games_and_retrieval_finds_them` | API: `ingested=2`, затем retrieval находит |
| `test_ingest_rejects_empty_file` | 400 `invalid_pgn` |
| `test_rag_context_builder_returns_section` | формат раздела RAG (fake find + fake session) |
| `test_rag_context_builder_returns_none_without_mistakes` | без ошибок RAG не добавляется |
| `test_gm_commentary_includes_rag_context` | промпт `gm` содержит RAG-раздел, промпт `club` — нет (fake LLM + fake builder) |

## 3. Побочные находки

* `/v1/ingest` не коммитил транзакцию — данные откатывались; добавлен
  `await session.commit()`. Тест на API поймал это сразу.
* Нельзя использовать глобальный async-engine из двух event loop: в тесте
  retrieval вызывается **после** выхода из `TestClient` (пул соединений
  закрывается в lifespan → новый цикл создаёт новые соединения).

## 4. Полный прогон

```
pytest -q      → 181 passed, 1 xfailed (два прогона: 192.79 с и 151.16 с)
ruff check .   → All checks passed!
mypy app       → Success: no issues found in 41 files
```

## 5. Коммит

```
feat(rag): add annotated-game ingestion and similarity retrieval (T-017)
```

## 6. Definition of Done — T-017

| Критерий | Статус | Факт |
| :---: | :---: | :--- |
| `annotated_games` готова принимать данные | ✅ | таблица из T-004, `vector(1024)` + ivfflat |
| `POST /v1/ingest` грузит партии | ✅ | живой прогон: 2 партии, ids [57, 58] |
| `find_similar_positions` находит по FEN | ✅ | hit 1 — нужная партия |
| RAG в `/v1/commentary` для `gm` | ✅ | тест: gm-промпт содержит, club — нет |
| Тест на 2–3 партии через ingest + FEN-поиск | ✅ | `test_ingest_endpoint...` |
| `ruff`/`mypy`/`pytest` зелёные | ✅ | 0 / 0 / 180 passed |
| T-017 `[x]` | ✅ | `TODO.md` |
