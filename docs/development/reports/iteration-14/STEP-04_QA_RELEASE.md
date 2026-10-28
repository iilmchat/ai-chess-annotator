# STEP-04 — QA, документация и релиз v0.1.0 (T-026)

**Дата:** 2026-10-28
**Итерация:** 14
**Статус:** ✅ QA зелёный; релиз готов в объёме T-023/T-025/T-026

## Что сделано

1. Обновлены README (статус, стек, API, тесты, Docker), CHANGELOG
   `[0.1.0] — 2026-10-28`, GETTING_STARTED §7, PROMPT_CURRENT,
   TODO (T-019…T-026), docs/README.
2. Созданы `USER_GUIDE.md`, `ASSISTANT_LEVELS.md`, `ARCHITECTURE.md`,
   `MANUAL_TESTING_GUIDE.md`; закрыты KI-008 и KI-009.
3. Заведён KI-030 по результатам LLM-eval.
4. Добавлены отчёты `iteration-14/` и `LLM_EVAL.md`.
5. CI: pnpm 12, `pnpm test` без `--passWithNoTests`, job `docker build`.

## Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 44 source files` |
| Backend | `pytest -q` | **195 passed, 1 xfailed**, 199.09 с |
| Frontend types | `pnpm --dir web typecheck` | exit 0 |
| Frontend tests | `pnpm --dir web test` | **14 passed** (2 файла) |
| Frontend build | `pnpm --dir web build` | JS 239.45 kB / CSS 8.13 kB |
| Docker build | `docker build -t chessannotator-prod:test .` | exit 0, 512 MB |
| Docker stack | `docker compose -p chessannotator-prod ... up -d` | 3 healthy |
| Prod health | `GET 127.0.0.1:8011/v1/health` | `{"status":"ok"}` |
| SPA | `GET 127.0.0.1:8011/` | 200, HTML |
| LLM-eval | `scripts/rejudge_llm.py` | 5.19/10, KI-030 |

## DoD итерации

| Пункт | Статус |
| :--- | :--- |
| Оценка LLM + `LLM_EVAL.md` | ✅ 5.19/10, 9/10 |
| Docker production безопасен и проверен | ✅ loopback-порты, health, SPA |
| README, USER_GUIDE, CHANGELOG под v0.1.0 | ✅ |
| Все тесты зелёные | ✅ pytest 195+1 xfail; pnpm 14 |
| T-023, T-025, T-026 `[x]` | ✅ |

## Остатки

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Порог качества 7/10 | Локальная модель/промпты | KI-030 |
| Слепой тест уровней | Нужны люди | T-024 |
| Тег v0.1.0 + GHCR | Нет registry-доступа | T-027 |
| SSE, Playwright | Сознательно вне MVP | KI-028, T-020 |

## Вердикт

Релиз **v0.1.0** готов как MVP: end-to-end сценарий (PGN → анализ →
комментарий → правка → чат) работает, production-образ собирается и
проходит healthcheck, документация актуальна. Качество комментариев
ниже целевого 7/10 (5.19) — зафиксировано как KI-030 и не скрывается.
