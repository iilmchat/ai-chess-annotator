# STEP-02 — SPA и статическая раздача (T-019…T-022, T-025)

**Дата:** 2026-10-28
**Итерация:** 14
**Статус:** ✅ (с ограничениями: без SSE и Playwright)

## Что сделано

1. Полноценная одностраничная SPA на React 19 + Vite:
   - вставка PGN, выбор уровня, кнопка «Разобрать партию»;
   - доска из FEN (свой парсер, Unicode-фигуры, подсветка последнего хода);
   - список ходов с оценками/классами, карточка хода с мотивами,
     лучшим ходом и рекомендацией Policy;
   - панель комментария: генерация, inline-редактор, `PATCH`;
   - чат: история, подсказки, `POST /v1/chat`.
2. API-клиент `web/src/api.ts` с типизированными ответами и `ApiError`.
3. `AnalyzeResponse` дополнен `initial_fen`, у каждого хода — `fen`
   (нужно доске; обратная совместимость сохранена).
4. FastAPI монтирует `web/dist` через `StaticFiles(html=True)` **после**
   API-роутеров; `/v1/*` и `/docs` не перехватываются.
5. Тесты: `board.test.ts` (7), `format.test.ts` (7) — **14 passed**;
   `test_web_static.py` — SPA отдаётся, `/v1/health` работает.
6. CI: `pnpm test` без `--passWithNoTests`, pnpm 12, добавлен job
   `docker build`.

## Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Типы | `pnpm --dir web typecheck` | exit 0 |
| Тесты | `pnpm --dir web test` | `2 passed (14)` |
| Сборка | `pnpm --dir web build` | `dist/index.html`, JS 239.45 kB, CSS 8.13 kB |
| SPA | `pytest tests/integration/test_web_static.py -q` | `1 passed` |
| API не сломан | тот же тест | `GET /v1/health` 200 |

## Файлы

| Файл | Что |
| :--- | :--- |
| `web/src/App.tsx` | layout, состояние, сценарий |
| `web/src/api.ts` | fetch-клиент `/v1/*` |
| `web/src/types.ts` | контракты API |
| `web/src/lib/board.ts` | FEN → 8×8, UCI → клетки |
| `web/src/lib/format.ts` | оценки, классы, уровни |
| `web/src/components/*` | Board, MoveList, CommentaryPanel, ChatPanel |
| `web/src/styles.css` | тёмная тема, адаптивная сетка |
| `app/api/v1/analyze.py` | `initial_fen`, `fen` |
| `app/main.py` | `StaticFiles` |

## Ограничения

- SSE-стриминг не внедрён (KI-028): комментарий/чат приходят целиком.
- Playwright и скриншот-тест не внедрены; вместо них — Vitest и
  интеграционный тест статики.
- Роутинг заменён одностраничным layout; Tailwind/i18n/Zustand не
  использовались (MVP-упрощение).

## Что дальше

T-025 (Docker) и T-026 (документация); SSE — в UI-итерации/Phase 7.
