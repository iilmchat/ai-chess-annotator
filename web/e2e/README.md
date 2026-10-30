# E2E-тесты ChessAnnotator (Playwright) — T-036b

Сквозные сценарии фронтенда на реальном браузере: регистрация/вход, разбор
партии, генерация и правка комментария (SSE), история партий, дневной лимит
токенов, auth-UX итерации 33 (verify-email, forgot/reset-password,
logout-all, экран «подтвердите email»), профиль/PWA (итерация 39) и billing
(итерация 40: Checkout, success/cancel, Customer Portal). Гоняются на двух
проектах:
`chromium` (Desktop Chrome) и `mobile` (Pixel 7), поэтому каждый сценарий
обязан работать и на мобильном вьюпорте.

Основной режим — **mocked**: все запросы к API перехватываются
`page.route` (`web/e2e/fixtures.ts`), поэтому не нужны backend, Postgres,
Stockfish и LLM. Этот же режим используется в CI.

## Быстрый старт

```bash
pnpm --dir web install
pnpm --dir web exec playwright install chromium   # если браузера нет в кэше
pnpm --dir web run test:e2e                       # 34 сценария × 2 проекта = 68 тестов
```

Отдельный проект / отладка:

```bash
pnpm --dir web exec playwright test --project=chromium
pnpm --dir web exec playwright test e2e/auth.spec.ts
pnpm --dir web run test:e2e:ui                    # Playwright UI mode
```

Playwright сам поднимает Vite dev server (`pnpm dev --host 127.0.0.1
--port 5173 --strictPort`) и ждёт `http://127.0.0.1:5173`; вне CI уже
запущенный сервер переиспользуется (`reuseExistingServer: !CI`). Адрес —
именно `127.0.0.1`, не `localhost` (RULES §11.1).

### `E2E_PREVIEW=1` (Windows/DSH-песочница)

В DSH-песочнице на Windows dev-сервер не стартует: esbuild получает
`Access is denied` при записи в `web/node_modules/.vite`. Обход — собрать
`dist` и раздавать его через `vite preview` (preview проксирует `/auth` и
`/v1` так же, как dev):

```powershell
$env:E2E_PREVIEW="1"
pnpm --dir web run test:e2e
```

Этот режим использует те же mocked-сценарии; CI по-прежнему запускает
`pnpm dev`.

## Структура

```
web/
├─ playwright.config.ts     # testDir ./e2e, projects chromium+mobile, webServer
└─ e2e/
   ├─ fixtures.ts           # моки API + helpers (mockApi, registerAndLogin)
   ├─ auth.spec.ts          # регистрация → вход → меню профиля → «Выйти»
   ├─ auth-flow.spec.ts     # verify-email, forgot/reset, unverified gate, logout-all
   ├─ analyze.spec.ts       # анализ PGN → доска + MoveList → /analysis/:id
   ├─ commentary.spec.ts    # SSE-генерация → «Редактировать» → PATCH → «edit #id»
   ├─ history.spec.ts       # «История» в шапке → /history → строка партии
   ├─ limits.spec.ts        # usage-badge «Free: 0/50k» → модалка лимита
   ├─ billing.spec.ts       # Upgrade → Checkout, success/cancel, Portal (T-083)
   ├─ profile.spec.ts       # /profile: имя, уровень, смена пароля (T-082)
   ├─ pwa.spec.ts           # манифест, иконки, theme-color, service worker (T-081)
   ├─ board-arrows.spec.ts / board-variant.spec.ts / chat-context.spec.ts
   └─ pgn-export.spec.ts / pgn-upload.spec.ts
```

Каждый spec независим: в `beforeEach` вызывает `mockApi(page)` и сам
логинится через UI (`registerAndLogin`), e-mail уникальный
(`uniqueEmail()`), поэтому порядок и параллельность не важны. Состояние
браузера между тестами не разделяется (новый context на тест).

## Что мокается (mocked-режим)

Контракты повторяют `web/src/types.ts` / `web/src/api.ts`:

| Метод и путь | Ответ мока |
| --- | --- |
| `POST /auth/register` | `201` + `AuthUser` (обычный email — verified; `unverified-…` — нет) |
| `POST /auth/login` | `200` + `{access_token, token_type, expires_in, user}`; refresh — только в HttpOnly cookie |
| `POST /auth/refresh` | `401` до логина, после логина `200` + `{access_token, token_type, expires_in, user}` (stateful) |
| `POST /auth/logout` | `204` без тела, сбрасывает состояние мока |
| `POST /auth/logout-all` | `204` без тела, сбрасывает состояние мока |
| `GET /auth/verify-email` | `200` + `{email, is_verified:true}`; `token=invalid` → `401` |
| `POST /auth/forgot-password` | `202` + нейтральный `{detail}` |
| `POST /auth/reset-password` | `200` + `{detail}`; `token=invalid` → `401` |
| `GET /auth/me` | `200` + `AuthUser` (учитывает verified-состояние мока) |
| `GET /v1/users/me/usage` | `200` + `{enabled:true, tier:"free", used_tokens:0, limit_tokens:50000, remaining_tokens:50000}` |
| `POST /v1/analyze` | `200` + `AnalyzeResponse` (`analysis_id:"42"`) |
| `GET /v1/games` | `200` + список партий (id `77`) |
| `GET /v1/games/:id` | `200` + `AnalyzeResponse` с тем же id |
| `POST /v1/commentary/stream` | `200 text/event-stream`: `event: meta`, `event: delta` ×2, `event: done` |
| `PATCH /v1/commentaries/:id` | `200` + `EditedCommentary` с текстом из запроса |

SSE собирается ровно в формате `streamRequest`/`parseSseBlock`: блоки
`event: <name>\ndata: <json>\n\n`; `done` содержит `commentary_id`,
`tokens_used`, `model`, `validated`.

В real-backend-режиме моки не ставятся, запросы идут на backend через
Vite-proxy (`/auth` и `/v1` из `vite.config.ts`).

## Селекторы

Только существующая разметка — без `data-testid`:

- роли/тексты RU (язык по умолчанию): `getByRole("tab", {name:"Регистрация"})`,
  `getByLabel("Email")`, `getByRole("button", {name:"Разобрать партию"})`,
  `getByRole("link", {name:"История"})`, `getByRole("region", {name:"Ходы"})`,
  `getByRole("textbox", {name:"Текст комментария"})`,
  `getByRole("menuitem", {name:"Выйти", exact:true})` (подстрока иначе
  совпадает с «Выйти со всех устройств»), меню профиля —
  `getByRole("button", {name:"Вы вошли как <email>"})`;
- устойчивые CSS-классы: `.board-wrap`, `.moves__grid .move`,
  `.commentary-text`, `[role="dialog"]`;
- маркер успешного PATCH — текст `edit #<id>` (`commentary.savedEdit`).

## Режим реального backend (`E2E_REAL_BACKEND=1`)

Нужен запущенный API (uvicorn на `127.0.0.1:8000` или другой порт), а также
его зависимости: Postgres (+pgvector), Redis, Stockfish и LLM (для
генерации комментария). Аккаунты создаются тестами автоматически с
уникальными email вида `e2e-<stamp>-<random>@example.com` — домен именно
`example.com`: `email-validator` (Pydantic `EmailStr`) отклоняет special-use
домены (`.local`, `.test`, `.invalid`, …) с HTTP 422.

Bash:

```bash
E2E_REAL_BACKEND=1 VITE_API_PROXY=http://127.0.0.1:8000 pnpm --dir web run test:e2e
```

PowerShell:

```powershell
$env:E2E_REAL_BACKEND="1"; $env:VITE_API_PROXY="http://127.0.0.1:8000"
pnpm --dir web run test:e2e
```

`VITE_API_PROXY` прокидывается Playwright'ом в env Vite dev server, поэтому
proxy в `vite.config.ts` направлен на реальный API; `baseURL` остаётся
`http://127.0.0.1:5173`.

Отличия real-режима в сценариях:

- `history.spec.ts` сначала создаёт разбор (у нового аккаунта иначе пустая
  история), затем идёт в «Историю»;
- `limits.spec.ts` проверяет тир `Free:` и модалку, точные цифры
  (`0/50k`) — только в mocked-режиме (лимит задаёт конфиг backend);
- **регистрация в real-режиме создаёт неподтверждённый email** (KI-041):
  без перехода по ссылке из structlog-лога (`email_mock_sent`) пользователь
  видит экран «Подтвердите email», а `/v1/analyze` вернёт 403. Поэтому
  real-режим требует ручного подтверждения (или SMTP-провайдера, KI-056);
  mocked-режим считает обычный email подтверждённым, а префикс
  `unverified-` включает экран явно;
- `analyze.spec.ts`/`commentary.spec.ts` увеличивают таймауты
  (Stockfish/LLM): в real-режиме на комментарий даётся до 240 с, т.к.
  генерация через локальную LLM (LM Studio) занимает ~50–60 с;
- в real-режиме удобно сначала прогнать только chromium:
  `pnpm --dir web run test:e2e -- --project=chromium`;
- генерация комментария в real-режиме требует настроенной LLM — без неё
  `commentary.spec.ts` ожидаемо упадёт.

Проверено локально на живом backend (`127.0.0.1:8011`, Postgres 5435,
Redis 6380, Stockfish, LM Studio): real-режим `--project=chromium` — 5
passed (~1.2 мин, итерация 32, до появления email-верификации).
Mocked-режим итерации 40 — **68 passed** (34 сценария × 2 проекта) через
`E2E_PREVIEW=1`.

## Артефакты и CI

При падении: `trace` — на первом retry (`on-first-retry`), скриншот —
`only-on-failure`, видео — `retain-on-failure`. Каталоги:
`web/test-results/` (трейсы, скриншоты, видео), `web/playwright-report/`
(HTML-отчёт, открывается `pnpm --dir web exec playwright show-report`).
Оба каталога в `.gitignore`.

CI: отдельный job `e2e` в `.github/workflows/ci.yml` — `pnpm install
--frozen-lockfile`, `playwright install --with-deps chromium`,
`playwright test` (mocked-режим, `retries: 1` в CI), при падении —
`upload-artifact@v4` с `web/test-results/` и `web/playwright-report/`
(retention 7 дней). Реальный backend в CI не поднимается.

## Версия Playwright

`@playwright/test` закреплён на `1.64.1` — она соответствует Chromium
`1248` из локального кэша `%LOCALAPPDATA%\ms-playwright`. При обновлении
зависимости переустановите браузер: `pnpm --dir web exec playwright install
chromium`.
