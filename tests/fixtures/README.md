# Тестовые фикстуры

Две PGN-фикстуры одной и той же партии (Гукеш — Каймер, Прага 2024) — под
разные задачи тестов, плюс аннотированные партии для RAG и синтетический
JSONL для LoRA.

## `example.pgn` — «реальная» партия

Партия **Д. Гукеш — В. Каймер**, 6th Prague Masters 2024, раунд 8.2
(2024-03-06), результат `1-0`, ECO `E21` (Нимцович, вариант с `4.Nf3 d6`).

- **Источник:** The Week in Chess 1531 (Mark Crowther, 2024-03-11),
  заголовки сохранены как в исходнике.
- **Объём:** 53 полухода (27 ходов), 27-й ход белых — `Bg6+`, чёрные сдались.
- **Зачем:** парсинг реальных заголовков (`WhiteElo`, `EventDate`, `ECO`,
  `PlyCount`), ECO из заголовка, отсутствие эндшпиля в 27-ходовой партии.
- **Почему очищена первая строка:** в исходном файле перед PGN стояла
  строка «Партия 2. Д.Гукеш — В.Каймер, 2024», из-за которой
  `chess.pgn.read_game` возвращал пустую партию (0 полуходов, все
  заголовки `?`). Файл приведён к валидному PGN; описание игры теперь
  здесь, а не внутри фикстуры.

## `test_game.pgn` — контролируемые метаданные

Тот же ход партии с обезличенными игроками (`Test White` / `Test Black`),
`Event "Test"`, `Date "2026.03.30"` и **без заголовка `[ECO]`**.

- **Зачем:** проверка маппинга произвольных заголовков в `GameMetadata` и
  работы fallback-а ECO (таблица префиксов → `E21` по
  `1.d4 Nf6 2.c4 e6 3.Nc3 Bb4 4.Nf3`).
- **Набор ходов** идентичен `example.pgn` — это гарантирует, что тесты не
  «подгоняют» ожидания под конкретный файл.

### Инварианты для тестов

| Параметр | `example.pgn` | `test_game.pgn` |
| :--- | :--- | :--- |
| Полуходов | 53 | 53 |
| Ходов (полных) | 27 | 27 |
| Результат | `1-0` | `1-0` |
| ECO | `E21` (заголовок) | `E21` (таблица префиксов) |
| White / Black | `Gukesh, D.` / `Keymer, Vincent` | `Test White` / `Test Black` |
| Event | `6th Prague Masters 2024` | `Test` |
| Эндшпиль по разметке фаз | нет (`endgame_start_ply = None`) | нет |
| Последний ход | `Bg6+` (шах) | `Bg6+` (шах) |
| `python-chess` | `board.is_valid()` → `True` | `True` |

## `rag_sample.pgn` — 3 аннотированные партии для RAG/CLI

Три короткие легальные партии (по 12 полуходов) с заголовками и
`{комментариями}` в главной линии: «Italian Demo», «Queen's Gambit Demo»,
«King's Indian Demo».

- **Зачем:** тесты `tests/unit/test_ingest_pgn.py`, живые тесты
  `tests/integration/test_embeddings_live.py` и демо
  `scripts/ingest_pgn.py --file tests/fixtures/rag_sample.pgn --limit 2`.
- **Инварианты:** ровно 3 партии, у каждой есть ходы и хотя бы один
  комментарий; `annotated_games.embedding` после ingest — 768 измерений.

## `lora_sample.jsonl` — синтетический датасет для LoRA (T-104)

4 записи в схеме SFT `system → user → assistant` (`schema_version=1`,
`level="club"`). `system` — сокращённый `app/prompts/club.txt`,
`user` — «исходный комментарий», `assistant` — «правка редактора»
(синтетические, не из реальных `edits`).

- **Зачем:** офлайн-проверка пайплайна без GPU и БД:
  `python scripts/train_lora.py --dry-run --dataset tests/fixtures/lora_sample.jsonl`
  и unit-тесты `tests/unit/test_train_lora.py` / `test_dataset_export.py`.
- **Инварианты:** все 4 записи валидны по строгой схеме; порядок ролей
  `system→user→assistant`; непустой `content`; `schema_version=1`.
- **Почему не из БД:** реальный экспорт делает
  `scripts/export_dataset.py --level club` (нужны PostgreSQL и `edits`);
  фикстура детерминирована и коммитится.

