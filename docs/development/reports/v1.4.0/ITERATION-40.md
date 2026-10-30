# Итерация 40 — Stripe Integration & Billing (T-083, T-084, T-085)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. Версия — **1.2.0** (бамп не делался; каталог
`v1.4.0` — по постановке итерации 40).
**Формат:** RULES §2.18 (7 разделов).

Монетизация Free → Pro: Stripe Checkout и webhook (T-083), интерфейс
оплаты и «Моя подписка» (T-084), FinOps с тиром из `users.tier` (T-085).
Живой Stripe-аккаунт в этой среде недоступен — контракт проверен моком SDK
и реальной HMAC-подписью webhook (KI-059).

---

## 1. Файлы (+/-)

### Новые файлы (12)

| Файл | Назначение |
| :--- | :--- |
| `alembic/versions/f2b3c4d5e6a7_users_tier_and_stripe_customer.py` | Миграция `users.tier` (String(16), NOT NULL, server_default `'free'`, CHECK `ck_users_tier`, backfill admin/auditor → pro) и `users.stripe_customer_id` (nullable UNIQUE); downgrade/upgrade проверены roundtrip-ом |
| `app/billing/__init__.py` | Экспорт `BillingGateway`, `StripeBilling`, `apply_webhook_event`, `create_billing`, `get_billing` |
| `app/billing/gateway.py` | Stripe SDK: find-or-create Customer, Checkout Session (`mode=subscription`), Customer Portal, `Webhook.construct_event`; синхронные вызовы через `asyncio.to_thread`; ошибки → доменные `BillingError`/`BillingSignatureError` |
| `app/billing/service.py` | `apply_webhook_event`: идемпотентно `checkout.session.completed` → `tier='pro'` + customer id, `customer.subscription.deleted` → `tier='free'`; неизвестные события игнорируются (200) |
| `app/api/v1/billing.py` | `POST /v1/billing/create-checkout-session`, `create-portal-session`, `webhook`; success/cancel/return URL из `APP_BASE_URL` |
| `tests/unit/test_billing_gateway.py` | Аргументы `stripe.Customer/Checkout/Portal`, поиск/создание Customer, ошибки SDK, HMAC-подпись webhook (валидная/битая/без секрета) |
| `tests/integration/test_billing.py` | Реальный PostgreSQL: checkout (401/503/409, сохранение и переиспользование Customer), webhook с подписанными mock-событиями (pro/free, идемпотентность, 400/503), Portal, usage по `users.tier` |
| `web/src/pages/BillingSuccessPage.tsx` | `/billing/success`: подтверждение оплаты, `GET /auth/me` подтягивает `tier` после webhook |
| `web/src/pages/BillingCancelPage.tsx` | `/billing/cancel`: тариф не изменён, возврат в профиль/на главную |
| `web/src/api.billing.test.ts` | Vitest: `POST` без тела, URL/portal-эндпоинты, `ApiError` 503 |
| `web/e2e/billing.spec.ts` | 4 E2E-сценария: upgrade из модалки, профиль Free, профиль Pro + Portal, страница отмены |
| `docs/development/reports/v1.4.0/ITERATION-40.md` | Этот отчёт |

### Изменённые файлы (основные)

| Файл | Изменение |
| :--- | :--- |
| `pyproject.toml` | Зависимость `stripe>=16.1` |
| `app/config.py` | `stripe_secret_key`/`stripe_webhook_secret`/`stripe_price_id`, свойства `billing_configured`/`billing_webhook_configured`; `pro_ips` помечен устаревшим |
| `app/db/models.py` | `UserTier`, `User.tier`, `User.stripe_customer_id`, CHECK `tier IN ('free','pro')` |
| `app/errors.py` | `BillingError` (502), `BillingNotConfiguredError` (503), `BillingSignatureError` (400), `BillingAlreadySubscribedError` (409), `BillingCustomerMissingError` (409) |
| `app/finops.py` | `lookup_user_tier()` (PK-lookup `users.tier`), асинхронный `request_identity`/`request_tier`, `tier_lookup`-seam middleware, аноним всегда `free`, `PRO_IPS` не используется |
| `app/api/v1/users.py` | `GET /v1/users/me/usage` берёт тир из БД тем же резолвером, что middleware |
| `app/api/v1/auth.py` | `UserProfile.tier` в `/auth/me`, login/register/refresh |
| `app/main.py` | `app.state.billing`, роутер `/v1/billing`, rate limit checkout/portal |
| `tests/integration/test_token_budget_api.py` | Pro-тир из БД поднимает лимит (5 → 50), usage отдаёт pro-лимит |
| `tests/integration/test_usage_roles.py` | Роль admin без `tier='pro'` → free; `tier='pro'` у user → pro |
| `tests/unit/test_token_budget.py` | `tier_lookup` вместо роли; `PRO_IPS` игнорируется |
| `tests/integration/test_auth_api.py`, `test_auth_profile.py`, `tests/test_models.py` | `tier` в профиле/схеме, CHECK/UNIQUE |
| `web/src/types.ts` | `UserTier`, `AuthUser.tier`, `BillingSessionResponse` |
| `web/src/api.ts` | `createCheckoutSession`, `createPortalSession` |
| `web/src/components/UsageBadge.tsx` | Кнопка «Оформить подписку» → Checkout, ошибки/loading в модалке |
| `web/src/pages/ProfilePage.tsx` | Блок «Моя подписка», кнопки checkout/portal |
| `web/src/App.tsx` | Публичные маршруты `/billing/success|cancel` |
| `web/src/i18n.ts` | RU/EN: `billing.*`, обновлён `usage.modal.proHint` |
| `web/e2e/fixtures.ts` | `mockApi(page, {tier})`, `tier` в профиле/usage, моки billing-эндпоинтов |
| `web/e2e/{profile,history}.spec.ts`, `web/src/**/*.test.ts` | `tier: "free"` в фикстурах `AuthUser` |
| `.env.example` | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`; обновлён FinOps-комментарий |
| `docs/development/SPEC.md` | Схема `users`, billing/usage-эндпоинты, FinOps/billing-раздел |
| `docs/development/ARCHITECTURE.md` | Модули `app/billing`, §5.5 «Биллинг и тарифы», frontend-счётчики |
| `docs/development/GETTING_STARTED.md` | `STRIPE_*` в `.env`, счётчики тестов/E2E |
| `docs/user_guide/USER_GUIDE.md` | §7.11 «Подписка Pro и оплата» |
| `docs/KNOWN_ISSUES.md` | KI-059 (живой Stripe-флоу не проверен) |
| `docs/CHANGELOG.md`, `TODO.md`, `README.md`, `docs/development/RELEASES.md`, `web/e2e/README.md` | Задачи, возможности, счётчики, журнал, E2E-справка |

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| Stripe SDK (unit) | `tests/unit/test_billing_gateway.py` | `configured` без цены; переиспользование существующего Customer; поиск по email (list) и создание при отсутствии; параметры Checkout (`mode`, `customer`, `line_items[price]`, success/cancel, `client_reference_id`, metadata, `subscription_data`); ошибка SDK → `BillingError`; параметры Portal; `construct_event` с валидной HMAC-подписью; битая подпись → `BillingSignatureError`; без `STRIPE_WEBHOOK_SECRET` → `BillingNotConfiguredError` |
| Checkout (integration) | `tests/integration/test_billing.py` | 401 без JWT; 503 без ключей; Customer создаётся один раз и `stripe_customer_id` сохраняется, второй checkout его переиспользует; 409 для уже Pro; success/cancel URL из `APP_BASE_URL` |
| Webhook (integration) | `tests/integration/test_billing.py` | `checkout.session.completed` с реальной подписью → `tier='pro'` + customer; повтор события идемпотентен; `customer.subscription.deleted` → `tier='free'` (customer сохранён); неизвестный customer → 200; битая/отсутствующая подпись → 400 `billing_invalid_signature`; без секрета → 503 |
| Customer Portal | `tests/integration/test_billing.py` | Нет `stripe_customer_id` → 409 `billing_customer_missing`; с customer → URL, `return_url` = `APP_BASE_URL/profile` |
| FinOps из БД | `tests/unit/test_token_budget.py`, `tests/integration/test_token_budget_api.py`, `tests/integration/test_usage_roles.py` | Тир из `users.tier` (lookup получает id из JWT); pro-лимит 50 против free 5; usage-эндпоинт отдаёт pro; admin-роль без `tier='pro'` → free; `tier='pro'` у user → pro; `PRO_IPS` и `X-User-Tier` не дают Pro; аноним — free |
| Профиль/схема | `tests/integration/test_auth_api.py`, `test_auth_profile.py`, `tests/test_models.py` | `tier` в `/auth/me`/login/register; регистрация → free; `users.tier` NOT NULL + CHECK + server_default, `stripe_customer_id` nullable UNIQUE |
| Frontend API | `web/src/api.billing.test.ts` | `createCheckoutSession`/`createPortalSession`: POST без тела, путь, credentials, `ApiError` с `code` |
| UI (E2E) | `web/e2e/billing.spec.ts` | Модалка Free → «Оформить подписку» → POST checkout → `/billing/success`; профиль Free показывает тариф и ведёт в checkout; Pro показывает `Pro` и ведёт в Portal; страница отмены объясняет, что тариф не изменён |
| Регрессия | 85 backend-файлов + 13 frontend-файлов + 34 E2E-сценария | без регрессий (все существующие сценарии зелёные) |

Новых тестов: **+29 backend** (billing unit/integration, FinOps DB-tier,
профиль/схема), **+3 frontend** (`api.billing.test.ts`), **+4 E2E**
(`billing.spec.ts`).

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита | `pytest -q` (БД `chessannotator_iter40`, PostgreSQL 5435, Redis 6382/0, Stockfish 19) | **1029 passed, 3 skipped, 1 xfailed in 132.22 s** (exit 0), 85 файлов. Skip: 2 cairo-кейса + `DEEPSEEK_API_KEY`; xfail — KI-012 |
| Линтер / типы | `ruff check .`, `mypy app` | All checks passed; no issues in **90** файлах |
| Зависимости | `pip check` | No broken requirements found (stripe 16.1.0) |
| Миграции | `alembic upgrade head`, `alembic check`, `downgrade a9f1f28d4c45` + `upgrade head` | Ревизия `f2b3c4d5e6a7`; «No new upgrade operations detected»; roundtrip чистый, constraint `ck_users_tier` |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0, без `any` |
| Frontend-тесты | `pnpm --dir web test` | **132 passed** (13 файлов) |
| Frontend build | `pnpm --dir web build` | 3.25 с: `index.js` 371.60 kB / gzip **115.16 kB** (лимит 200), async workspace 497.60 / 143.95, CSS 4.81; PWA: precache **15 записей (943.57 KiB)** |
| E2E mocked | `E2E_PREVIEW=1 pnpm --dir web run test:e2e` | **68 passed** (34 сценария × chromium/mobile), 24.6 с, exit 0 |

Динамика к итерации 39: backend 1000 → **1029** (+29), frontend 129 →
**132** (+3), E2E 60 → **68** (+8 прогонов, +4 сценария).

---

## 4. Smoke

**Миграция.** `alembic upgrade head` на чистой БД `chessannotator_migration40`,
затем `downgrade a9f1f28d4c45` и снова `upgrade head` — чисто;
`SELECT column_name, is_nullable` показывает `tier` (NO) и
`stripe_customer_id` (YES), `pg_constraint` — `ck_users_tier` и
`uq_users_stripe_customer_id`.

**Webhook (mock-событие).** `POST /v1/billing/webhook` с телом
`checkout.session.completed` и заголовком `Stripe-Signature`
(`t=<ts>,v1=HMAC_SHA256(secret, ts.payload)`) → 200
`{"received": true}`, в БД `users.tier='pro'` и
`stripe_customer_id='cus_…'`; повтор того же события → 200, состояние не
меняется. `customer.subscription.deleted` с тем же customer → 200,
`tier='free'` (customer сохранён). Битая подпись → 400
`billing_invalid_signature`, без `STRIPE_WEBHOOK_SECRET` → 503
`billing_not_configured`. Команды и результат зафиксированы тестами
`tests/integration/test_billing.py` (13 кейсов, все passed).

**UI.** E2E `billing.spec.ts` (4 сценария × 2 проекта):
Free-модалка → «Оформить подписку» → POST
`/v1/billing/create-checkout-session` → `/billing/success`; профиль Free
показывает «Текущий тариф: Free» и ведёт в checkout; профиль Pro
показывает «Текущий тариф: Pro» и «Управлять подпиской» → POST
`/v1/billing/create-portal-session`; `/billing/cancel` объясняет, что
тариф не изменён. В mocked-режиме Stripe-URL заменён внутренним
`/billing/success`, поэтому проверяется именно редирект браузера.

**FinOps.** Пользователь с `tier='pro'` в БД и счётчиком 10 токенов
проходит free-лимит 5 (ответ эндпоинта 400 `invalid_pgn`, не 429);
`/v1/users/me/usage` отдаёт `tier='pro'`, `limit_tokens=500000` (в
тестовой конфигурации — `PRO_LIMIT=50`). Роль `admin` без `tier='pro'`
получает free.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| Живой Stripe-флоу не проверен | Нет ключей/аккаунта в среде; checkout/portal/webhook покрыты моком SDK и реальной проверкой HMAC-подписи. KI-059 |
| Цена не отображается в UI | Модалка/профиль показывают тариф и лимит, но не сумму/период — цена задаётся в Stripe (`STRIPE_PRICE_ID`) и в интерфейс не пробрасывается |
| Отмена — только через Stripe Portal | Собственного cancel-эндпоинта нет (по постановке управление подпиской — в Customer Portal), поэтому при отмене тариф меняется только после `customer.subscription.deleted` |
| Webhook-задержка | После `/billing/success` `tier` может оставаться `free` несколько секунд, пока Stripe доставит событие; страница подсказывает обновить её позже |
| Нет отдельных тарифов/лимитов по моделям | В постановке только Free 50k → Pro 500k; «тяжёлые модели» остаются текстом, фактический лимит — дневные токены |
| `users.tier` — строковый CHECK, не нативный enum | Как `export_jobs.status`: проще миграции/downgrade, значение валидируется CHECK-ом и `UserTier` |
| Тестовый seam `tier_lookup` | Unit-тесты FinOps не поднимают PostgreSQL; в проде всегда `lookup_user_tier` из БД, подмена недоступна извне |
| KI-059 остаётся Open | Закрывается только живым прогоном Stripe тестовой картой |

---

## 6. Новые KI

**KI-059 · Open · Low · billing, stripe** — живой Stripe Checkout/Portal/
webhook не проверен (нет ключей и тестового аккаунта). Контракт покрыт
unit-тестами с моком SDK, integration-тестами с реальным PostgreSQL и
HMAC-подписью, E2E на моках; без ключей эндпоинты отвечают 503, приложение
работает. План закрытия — тестовый Stripe-аккаунт, Checkout картой
`4242…`, проверка `tier='pro'`, Portal и отмены → `free`.

Других новых KI нет. KI-030 (качество LLM/LoRA), KI-023 (атрибуция
оценок), KI-056 (SMTP/2FA/SSO) остаются открытыми и к billing не
относятся.

---

## 7. Обоснование

- **Почему `users.tier`, а не роль/claim JWT.** T-083 требует, чтобы
  источником истины был Stripe webhook. Роль в JWT статична до истечения
  токена (15 мин), а после оплаты пользователь должен получить Pro сразу;
  `users.tier` — одно поле, которое webhook меняет атомарно, а FinOps
  читает PK-lookup-ом. Роль admin/auditor больше не даёт Pro: это
  явное изменение поведения (закрывает неявную привилегию), зато тариф
  один и предсказуемый. Backfill миграции сохранил Pro для существующих
  служебных учёток.
- **Почему тир читается в middleware, а не из JWT claim.** В claim пришлось
  бы ждать ротации токена; плюс это дублировало бы источник истины. Цена —
  один indexed SELECT на защищённый запрос (до LLM/Stockfish, которые на
  порядки дороже). При недоступной БД выбран консервативный `free`: лучше
  временно недодать Pro, чем выдать безлимит по ошибке.
- **Почему отдельный `BillingGateway`-протокол.** Тесты не должны ходить в
  сеть Stripe; протокол позволяет подменить шлюз, а `StripeBilling`
  тестируется отдельно с `monkeypatch` SDK. Синхронный SDK вызывается
  через `asyncio.to_thread`, чтобы не блокировать event loop.
- **Почему webhook проверяет подпись до БД.** Иначе любой мог бы
  «повысить» себе тариф POST-ом. `stripe.Webhook.construct_event`
  проверяет HMAC и tolerance; тело читается сырыми байтами (JSON-парсинг
  до проверки сломал бы подпись). Ошибки разделены: 400 — подпись,
  503 — не задан секрет.
- **Почему success/cancel URL строятся на сервере.** Если бы клиент
  присылал `success_url`, он мог бы увести пользователя на внешний сайт
  после оплаты (open redirect) или подменить return URL портала. Поэтому
  URL — из `APP_BASE_URL`, а тело запроса checkout/portal пустое.
- **Почему customer сохраняется до создания Checkout.** Если Stripe
  ответит ошибкой после создания Customer, id уже в БД — повторный запрос
  переиспользует клиента, а не плодит дубли. Поиск по email в
  `find_or_create_customer` закрывает окно «Customer создан, id не успел
  сохраниться».
- **Почему события идемпотентны.** Stripe повторяет доставку при
  таймаутах/5xx; `checkout.session.completed` может прийти несколько раз.
  Обработчик делает `UPDATE`-подобное присваивание, а не инкремент,
  поэтому повтор не меняет результат; неизвестный customer — 200, чтобы
  не зацикливать ретраи.
- **Почему `tier` — String + CHECK, а не нативный enum.** Как у
  `export_jobs.status`: меньше ловушек Alembic (§11.4), значения
  ограничены CHECK-ом и `UserTier` в коде; downgrade не оставляет
  осиротевший тип.
- **Почему E2E на моках, а не на реальном Stripe.** В CI нет ключей и
  внешней сети; сценарий проверяет то, что зависит от нас — запрос,
  редирект, страницы, кнопки. Реальный Checkout — отдельный ручной
  прогон (KI-059).
