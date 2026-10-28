# STEP-01 — Эмбеддер, ingest и retrieval (T-017)

**Дата:** 2026-10-28
**Итерация:** 9 — RAG (T-017)
**Статус:** ✅ выполнен

---

## 1. Эмбеддинги

Проблема: `bge-m3` требует `sentence-transformers` + torch; у LM Studio
единственная embedding-модель — `text-embedding-nomic-embed-text-v1.5`
(768 измерений), а колонка `annotated_games.embedding` — `vector(1024)` из
SPEC §4.

Решение — `HashEmbedder` (`app/rag/embedder.py`):

* токенизация текста (буквы/цифры), blake2b-хеш токена → измерение (со
  знаком), L2-нормализация;
* документ позиции: `fen {fen}\n{fen}` — FEN с двойным весом;
* одинаковый FEN → одинаковый вектор; близкие позиции делят токены
  (фигуры/поля) → ближе по косинусу;
* детерминированно, без зависимостей, покрыто 6 unit-тестами.

Ограничение — KI-025 (семантические эмбеддинги не подключены).

## 2. Ingest

`app/rag/service.py`:

* `parse_pgn_games(text)` — все партии из потока (пустые пропускаются);
* `ingest_pgn_text(session, text, source=...)`:
  - финальная позиция партии → FEN → эмбеддинг;
  - комментарии главной линии → `annotation_text`;
  - `white/black/event/year` из заголовков (`Date` → год);
  - `flush()` на каждую партию (id известен сразу).

Эндпоинт `POST /v1/ingest`:

* multipart `file` + опциональный `source`;
* лимит 5 МБ, декодирование UTF-8 → cp1251 при ошибке;
* ответ `{ingested, ids}`, пустой файл/нет партий → 400 `invalid_pgn`;
* **commit** — иначе загруженные партии откатывались (поймано интеграционным
  тестом, который сначала падал с пустым retrieval).

## 3. Retrieval

```python
distance = AnnotatedGame.embedding.cosine_distance(vector)
select(AnnotatedGame).where(embedding IS NOT NULL).order_by(distance).limit(top_k)
```

* FEN валидируется через ingestion (`InvalidFenError` на мусор);
* возвращает `annotation_text` (или «White — Black (Event)», если
  аннотации нет);
* на маленьких таблицах PostgreSQL сам выбирает seq scan — ivfflat-индекс
  (из T-004) подхватится при росте базы.

## 4. Живая демонстрация

```
POST /v1/ingest (2 партии с комментариями) → {"ingested": 2, "ids": [57, 58]}
query FEN после 3...a6 → r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4
hit 1: «Развитие с темпом / Испанская партия, давление на c6»
hit 2: «Ферзевый гамбит»
```

## 5. Что дальше

Шаг 2 — RAG-контекст в `/v1/commentary`, тесты, коммит.
