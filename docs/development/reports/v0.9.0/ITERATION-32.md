# Итерация 32 — Auth Finalization & ARQ Worker (KI-041, KI-053)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **KI-053 закрыт** (arq-воркер + recovery);
**KI-041 закрыт** (email verification, сброс пароля, logout-all).
Новые KI — KI-054 (retention/signed links экспортов) и KI-055 (2FA/SSO/
SMTP/frontend-страницы).
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### KI-053: ARQ-воркер

| Файл | Что изменилось |
| :--- | :--- |
| `app/worker.py` *(новый)* | `run_export_job(ctx, job_id)` — arq-задача, вызывает `app.dataset.jobs.run_export_job`; `startup` (recovery + `ctx["settings"]`), `shutdown`; `WorkerSettings` (`functions`, `redis_settings`, `queue_name`, `job_timeout=1800`, `max_tries=3`, `keep_result=3600`) |
| `worker.py` *(новый, корень)* | Тонкий реэкспорт `WorkerSettings`/задачи для `arq worker.WorkerSettings`; Docker копирует файл |
| `app/dataset/jobs.py` | `recover_stale_export_jobs` (running старше `EXPORT_JOB_STALE_MINUTES` → failed, `started_at`/`created_at`), `mark_export_job_failed` (queued/running → failed), общий `_update_job_failed`; docstring про arq |
| `app/api/v1/admin_exports.py` | `POST /jobs` больше не `BackgroundTasks`: `request.app.state.arq_pool.enqueue_job("run_export_job", job.id, _job_id=...)`; при отсутствии пула/ошибке — 503 `export_queue_unavailable` + джоба failed; импорт `mark_export_job_failed` |
| `app/main.py` | Lifespan создаёт `arq` pool (`create_pool(RedisSettings.from_dsn(...), default_queue_name=...)`, best-effort), `app.state.arq_pool`; recovery при старте; `aclose` при остановке |
| `app/config.py` | `arq_queue_name="arq:queue"`, `export_job_stale_minutes=15` |
| `app/errors.py` | `ExportQueueUnavailableError` (503, `export_queue_unavailable`) |
| `Dockerfile` | `COPY worker.py ./worker.py` (arq CLI в образе) |
| `docker-compose.yml` | Сервис `worker` (`build: .`, `arq worker.WorkerSettings`, Redis/Postgres, `EXPORT_DIR=/app/data/exports`, bind `./data`) |
| `docker-compose.prod.yml` | Сервис `worker` (тот же образ/env, `prod_export_data:/app/data`); у `app` — `EXPORT_DIR` и volume `prod_export_data` |
| `.env.example` | `ARQ_QUEUE_NAME`, `EXPORT_JOB_STALE_MINUTES` |
| `tests/integration/test_export_jobs.py` | 12 тестов: реальный `arq.Worker(burst=True)` в loop-е TestClient (`portal.call`), enqueue→done→download, пустая выборка, список, RBAC, 409/404/410, path traversal, аудит, cookie-сессия, `WorkerSettings`, recovery stale, 503 при `arq_pool=None` |

### KI-041: Email verification / password reset / logout-all

| Файл | Что изменилось |
| :--- | :--- |
| `alembic/versions/dcf1da1421cd_users_add_is_verified_for_email_.py` *(новый)* | `users.is_verified BOOLEAN NOT NULL DEFAULT true` (legacy/админы остаются verified; регистрация пишет false); down_revision `b0c9d2f13425` |
| `app/db/models.py` | `User.is_verified` (`default=True`, `server_default=true`, docstring) |
| `app/email/__init__.py`, `app/email/sender.py` *(новые)* | `EmailSender` Protocol, `EmailMessage`, `MockEmailSender` (outbox + `email_mock_sent` со ссылкой), `create_email_sender` (mock/ошибка), `get_email_sender` (app.state/fallback), `verification_message`/`password_reset_message` |
| `app/auth/tokens.py` | `TokenType` расширен (`email_verify`, `password_reset`); `iat_ms` в payload; `TokenPayload.issued_at`; `create_email_verification_token`/`create_password_reset_token`; `decode_token` принимает action-типы и заполняет `issued_at` |
| `app/auth/denylist.py` | `revoked_before:<user_id>` (Unix-ms): `global_revocation_key`, `revoke_all_refresh_tokens`, `is_token_globally_revoked` (сравнение `issued_at`), fail-open, docstring |
| `app/auth/deps.py` | `get_current_verified_user` — 403 `email_not_verified` для `is_verified=False`; admin/auditor bypass; `None` (test seam) допускается |
| `app/api/v1/auth.py` | `is_verified` в `UserProfile`; register → `is_verified=False` + письмо; `GET /verify-email`; `POST /forgot-password` (202 всегда); `POST /reset-password` (Argon2id, одноразовость через marker, глобальный отзыв, clear cookie); `POST /logout-all` (204); refresh проверяет global revocation; docstring |
| `app/api/v1/analyze.py`, `app/api/v1/commentary.py` | `/v1/analyze`, `/v1/commentary`, `/commentary/stream` используют `get_current_verified_user` (patch/export — без изменений) |
| `app/errors.py` | `EmailNotVerifiedError` (403 `email_not_verified`), `EmailDeliveryError` (503) |
| `app/config.py` | `app_base_url`, `email_sender`, `email_from`, `email_verification_ttl_hours=24`, `password_reset_ttl_min=30` |
| `app/main.py` | `app.state.email_sender`; rate limit на `forgot/reset/logout-all/verify-email` |
| `tests/integration/test_auth_ki041.py` *(новый)* | 8 тестов: unverified→403 analyze/commentary, verify идемпотентен, invalid/wrong-type токены, forgot enumeration-safe, reset меняет хеш/отзывает refresh/одноразовый, logout-all инвалидирует старый refresh и не мешает новому входу, 401 без access |
| `tests/unit/test_email_sender.py` *(новый)* | 5 тестов: outbox/ссылки, TTL, неизвестный sender, action-токены/типы |
| `tests/unit/test_auth.py` | +4 теста global revocation; `register(...)` вызывает с `Settings` + `MockEmailSender` |
| `tests/integration/test_auth_api.py` | `PROFILE_KEYS` + `is_verified`; регистрация проверяет `false` |
| `tests/integration/test_data_isolation.py` | `_verify_email` по outbox; все A/B/admin подтверждены до analyze/commentary |
| `tests/integration/test_token_budget_api.py` | `_verify_email` до analyze |
| `tests/test_models.py` | +1 тест `users.is_verified` |
| `web/src/types.ts` | `AuthUser.is_verified: boolean` |
| `web/src/api.auth.test.ts`, `api.pdf.test.ts`, `stores/authStore.test.ts` | Фикстуры дополнены `is_verified: true` |
| `.env.example` | `APP_BASE_URL`, `EMAIL_SENDER`, `EMAIL_FROM`, TTL, `ARQ_*` |

### Документация

`docs/development/reports/v0.9.0/ITERATION-32.md` (этот файл),
`TODO.md` (T-034b/KI-053, T-028b/KI-041), `docs/CHANGELOG.md`,
`docs/development/RELEASES.md`, `docs/KNOWN_ISSUES.md` (KI-041/KI-053
Closed, +KI-054/KI-055, KI-053 row/раздел восстановлены),
`docs/development/SPEC.md` (§4 `users.is_verified`, §5 auth-flow),
`docs/development/ARCHITECTURE.md` (§2 `app/email`/`app/worker`, §5.2
auth, §5.3 worker, head `dcf1da1421cd`),
`docs/development/GETTING_STARTED.md` (§3.4 env, §6.3 worker),
`docs/user_guide/USER_GUIDE.md` (§7.10 auth-flow, ограничения),
`docs/development/DESIGN_ADMIN_AND_OPS.md` (§4.1 is_verified/lifecycle),
`README.md` (746 тестов, 46 frontend, 16 таблиц/head `dcf1da1421cd`,
итерация 32, auth/arq), `docs/README.md` (индекс v0.9.0).

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| ARQ-воркер/очередь/recovery | `tests/integration/test_export_jobs.py` | 12 (было 9) |
| Email verification/reset/logout-all | `tests/integration/test_auth_ki041.py` | 8 |
| Mock sender + action-tokens | `tests/unit/test_email_sender.py` | 5 |
| Global revocation helpers | `tests/unit/test_auth.py` | +4 |
| Схема `users.is_verified` | `tests/test_models.py` | +1 |
| Регистрация/профиль | `tests/integration/test_auth_api.py` | обновлены |
| Изоляция/бюджет | `test_data_isolation.py`, `test_token_budget_api.py` | обновлены |

Проверяемые сценарии:

- **ARQ:** POST создаёт `queued` и ставит задачу в Redis; burst-воркер
  (в loop-е приложения, чтобы не смешивать asyncpg-пулы) выполняет
  `run_export_job`, статус `done`, файл/sha256/размер/rows совпадают,
  download отдаёт NDJSON; recovery `running` 20-минутной давности →
  `failed` (`stale:`); `arq_pool=None` → 503 + `failed`, а не вечный
  `queued`; `WorkerSettings` содержит задачу/очередь/startup; 401/403/404/
  409/410 и RBAC сохранены.
- **Email verification:** регистрация → `is_verified=false` и письмо с
  `/auth/verify-email?token=`; `/v1/analyze` и `/v1/commentary` → 403
  `email_not_verified`; `GET /auth/verify-email` → 200 и флаг true
  (идемпотентно); invalid JWT и access-токен вместо verify → 401;
  после verify `commentary` доходит до 404 (несуществующий analysis_id),
  т.е. dependency пропускает.
- **Reset:** `forgot-password` для известного/неизвестного email — один
  и тот же 202 и текст (no enumeration), письмо только известному;
  reset меняет хеш (старый пароль 401, новый 200), отзывает старый
  refresh (401), повторный reset тем же токеном — 401.
- **Logout-all:** 204; старый refresh → 401; новый login после отзыва
  выдаёт валидный refresh (200); без access — 401.
- **Unit:** ссылки/outbox, TTL, неизвестный backend, типы action-токенов,
  `revoked_before` (ms), сравнение `issued_at` до/после, fail-open.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:**
**746/748 passed** (backend; 1 skipped, 1 xfailed), **46/46** frontend,
**10/10** E2E mocked.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД iter32) | `pytest -q` (POSTGRES_DB=`chessannotator_iter32_final`, Redis 6381, `STOCKFISH_PATH`) | **746 passed, 1 skipped, 1 xfailed, 4 warnings in 260.87s**, exit 0; collected 748; **+22** к итерации 31 (724) |
| ARQ-воркер тесты | `pytest tests/integration/test_export_jobs.py -q` | **12 passed** |
| Auth KI-041 | `pytest tests/integration/test_auth_ki041.py -q` | **8 passed** |
| Email/tokens unit | `pytest tests/unit/test_email_sender.py tests/unit/test_auth.py -q` | **53 passed** |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 84 source files** (exit 0; +3 файла) |
| Alembic | `upgrade head`, `alembic check`, `downgrade base → upgrade head` | head `dcf1da1421cd`; «No new upgrade operations detected»; roundtrip OK; **16 таблиц**, 13 ревизий |
| Docker build | `docker build -t chessannotator:iter32 .` | exit 0; `import app.main, worker` OK; `WorkerSettings.functions[0].__name__=run_export_job`; `cairosvg_available()=True` |
| Compose config | `docker compose -f docker-compose.yml config`, `…prod.yml config` | exit 0; сервисы dev `postgres, redis, worker` |
| Docker worker (dev) | `docker compose up -d worker` + enqueue job 10 | worker `Up`, logs `Starting worker for 1 functions: run_export_job`, recovery count=0; job **done**, rows=1, file_size=11660, sha256 `320b3b24…`, **0.33 s** |
| ARQ CLI | `arq worker.WorkerSettings --burst` | started 1 function, recovery count=0, exits 0 |
| Auth live smoke (:8020) | register→login→analyze/verify/reset/logout-all | `register is_verified=False`; analyze before verify `email_not_verified`; verify True; analyze id=18/moves=4; forgot 202; reset 200; old password 401; new login 200; logout-all 204 |
| Frontend tests | `pnpm --dir web test` | **46 passed** (7 файлов) |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 6.92 с; JS 465.21 КБ (gzip 146.45), CSS 14.29 КБ (gzip 3.77) |
| E2E mocked | `pnpm --dir web run test:e2e` | **10 passed** (5 сценариев × chromium/mobile), 7.3 с; локально — через `vite preview` (см. §5) |

---

## 4. Smoke (ручной сценарий)

**4.1. Docker worker (dev).**

```text
docker compose up -d postgres redis worker
NAME                          IMAGE                       COMMAND
ai-chess-annotator-worker-1   ai-chess-annotator-worker   "arq worker.WorkerSe…"   Up

worker-1 | Starting worker for 1 functions: run_export_job
worker-1 | export_jobs_recovery count=0

# insert export_jobs(queued) + enqueue_job("run_export_job", 10)
worker-1 | 0.11s → export-job:10:run_export_job(10)
worker-1 | dataset_export_audited actor=smoke-docker rows=1
worker-1 | export_job_done job_id=10 rows=1 size=11660
worker-1 | 0.33s ← export-job:10:run_export_job ●

SELECT id,status,rows_exported,file_size,sha256
  → 10 | done | 1 | 11660 | 320b3b249d16b428f17a2382d8f58fdecc8712f96f9d4100292c60f54a3a83b1
```

После проверки джоба/файл удалены, worker остановлен.

**4.2. Auth-flow (живой uvicorn :8020, iter32 DB).**

```text
POST /auth/register → 201, is_verified=False (письмо в structlog)
POST /v1/analyze (без verify) → 403 {"code":"email_not_verified"}
GET /auth/verify-email?token=… → 200, is_verified=True
POST /v1/analyze → 200, analysis_id=18, moves_analyzed=4
POST /auth/forgot-password → 202 (нейтральный текст)
POST /auth/reset-password → 200
POST /auth/login (старый пароль) → 401
POST /auth/login (новый пароль) → 200, access_token 268 симв.
POST /auth/logout-all → 204
```

**4.3. ARQ CLI.**

```text
arq worker.WorkerSettings --burst
→ Starting worker for 1 functions: run_export_job
→ export_jobs_recovery count=0
```

**4.4. Миграция.** `alembic upgrade head` → `dcf1da1421cd`;
`alembic check` — без дрейфа; полный `downgrade base && upgrade head`
проходит; 16 таблиц; `users.is_verified` NOT NULL DEFAULT true.

---

## 5. Что не так

1. **Email только mock.** `MockEmailSender` пишет ссылку в structlog и
   outbox; реального SMTP/SES нет. В production письма не уходят — это
   KI-055. `EMAIL_FROM` пока не используется.
2. **Нет frontend-страниц verify/reset.** API возвращает JSON/ссылку;
   пользователь переходит по API-URL. UI-формы (и кнопка «подтвердить»)
   — KI-055. Ссылка reset ведёт на POST-эндпоинт; в письме явно указано
   передать токен в API.
3. **Access-токены не отзываются мгновенно.** `logout-all`/reset
   инвалидируют refresh, но уже выданный access живёт до 15 минут — это
   свойство stateless JWT; для мгновенного отзыва нужен denylist access
   по `jti` или короткий TTL.
4. **Global revocation fail-open.** При недоступном Redis
   `logout-all`/reset возвращают успех, но старые refresh могут работать
   до TTL; как и у denylist, это осознанный компромисс доступности.
5. **Одноразовость reset-токена зависит от Redis.** Если marker не
   записался (Redis down), токен можно повторно использовать до 30 мин;
   в логах — warning.
6. **`iat_ms` — расширение JWT.** Старые токены без `iat_ms` сравниваются
   по `iat` (секунды), поэтому граница отзыва для них грубее на 1 с.
   Новые токены точны до миллисекунд.
7. **Recovery по `started_at`, без heartbeat.** Джоба, которая реально
   идёт >15 минут, при рестарте API/воркера будет помечена `failed`.
   Для v2-экспорта на десятках тысяч строк это возможно; нужен
   `updated_at`/heartbeat или отдельный статус.
8. **Нет авто-retry упавших выгрузок.** ARQ `max_tries=3` для исключений,
   но `run_export_job` ловит ошибки и ставит `failed` (чтобы не дублировать
   запись); пользователь создаёт новую джобу.
9. **Retention/signed links не сделаны** — KI-054; файлы копятся, доступ
   только по JWT/cookie.
10. **`users.is_verified` default true.** Legacy/админские учётки и
    прямые ORM-вставки считаются подтверждёнными; чтобы принудительно
    перевести в false, нужен SQL/админ-инструмент (сейчас нет).
11. **Admin/auditor обходят email-verification.** Это служебные роли,
    создаваемые админом/скриптом; политика зафиксирована в коде.
12. **Нет endpoint resend-verification.** Если письмо потеряно, повторно
    зарегистрироваться нельзя; ссылка живёт 24 ч. Кандидат в KI-055.
13. **Rate limit `/auth/*` — 10/мин на IP** (как общий). За NAT несколько
    пользователей делят лимит; для production нужен per-email/per-account
    лимит (KI-055).
14. **`arq` worker не мониторится отдельно.** Нет healthcheck/queue-depth
    метрик; ошибки видны в логах/`export_jobs`. Наблюдаемость очереди —
    следующий шаг.
15. **E2E локально против `vite preview`.** DSH-песочница блокирует
    запись esbuild в `web/node_modules/.vite` (`Access is denied`),
    поэтому dev-сервер не стартует; preview раздаёт собранный `web/dist`,
    mocked-сценарии те же. CI использует `pnpm dev`.
16. **Джоба в Docker smoke выполнялась на dev-БД `chessannotator`
    (схема `b0c9d2f13425`), а не на iter32-БД.** Для worker-а это неважно
    (используется `export_jobs`), auth-миграция проверена отдельно на
    чистых БД.
17. **KI-030 не закрыт.** LoRA на GPU по-прежнему не обучалась.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| KI-054 | Low | admin, api | Нет retention `EXPORT_DIR`/`export_jobs` и подписанных ссылок: файлы копятся, доступ только по JWT/cookie. План: `scripts/prune_export_jobs.py` + cron, signed URL с TTL |
| KI-055 | Low | auth, security | Остаток KI-041: 2FA/TOTP, SSO, лимит попыток по (email, IP), реальный SMTP/SES, frontend-страницы verify/reset (и resend-verification) |

**KI-041 закрыт** (email verification, reset, logout-all; остаток —
KI-055). **KI-053 закрыт** (arq + recovery; retention — KI-054).
Сводка реестра: **55** записей, 25 Open, 30 Closed.

---

## 7. Обоснование

- **Почему arq, а не BackgroundTasks.** `BackgroundTasks` живёт в
  процессе API: рестарт теряет задачу, а `running` остаётся навсегда.
  ARQ переносит выполнение в отдельный процесс/контейнер, очередь в
  Redis переживает рестарт API, а воркер можно масштабировать. Это
  ровно задача KI-053.
- **Почему `_job_id=export-job:<id>`.** Повторный HTTP-запрос/ретрай
  клиента не должен создавать вторую задачу на ту же строку; ARQ
  возвращает существующую. `run_export_job` дополнительно идемпотентен
  по статусу (`queued` → `running`).
- **Почему recovery и в API, и в воркере.** API может перезапуститься
  раньше воркера и показать «вечный running» в админке; воркер — раньше
  API. Обе точки дешёвы (один UPDATE по индексу) и идемпотентны.
- **Почему порог 15 минут и `started_at`.** Это компромисс: дольше —
  джоба может быть живой, короче — ложные failed. `updated_at`/heartbeat
  в таблице нет; `started_at` — единственная точка отсчёта, fallback —
  `created_at`. Ограничение зафиксировано §5.
- **Почему 503 + failed при недоступной очереди.** Иначе пользователь
  видел бы `queued` без исполнителя. Явная ошибка и `failed`-строка
  позволяют повторить/диагностировать; статус не врёт.
- **Почему общий volume для API/worker.** Воркер пишет файл, API отдаёт
  его через `FileResponse`; без shared volume download видел бы пустой
  путь. В dev — bind `./data`, в prod — named volume.
- **Почему `is_verified` default true, а регистрация false.** Миграция
  не должна отключать существующих пользователей и админские учётки,
  созданные скриптами. Саморегистрация — единственный путь, где
  появляется `false`; это явно в `register`.
- **Почему 403 `email_not_verified`, а не 401/302.** Пользователь
  аутентифицирован, но не имеет права на генерацию; 403 с машинным
  кодом позволяет фронтенду показать «подтвердите email» без разбора
  текста. `admin/auditor` — служебные роли, им флаг не нужен.
- **Почему Protocol + MockSender, а не сразу SMTP.** Нет провайдера и
  секретов; mock полностью тестируем (outbox/link) и не требует сети.
  Смена на SMTP — реализация того же `send`-метода без правок auth.
- **Почему action-токены — JWT, а не таблица.** Email verification и
  reset — короткоживущие одноразовые ссылки; отдельная таблица добавила
  бы миграцию/чистку. Тип (`email_verify`/`password_reset`) исключает
  использование access-токена как ссылки; `jti`/`iat_ms` дают
  одноразовость через global marker без хранения каждого токена.
- **Почему `iat_ms`.** `iat` в секундах даёт гонку: logout-all и только
  что выданный refresh могут попасть в одну секунду, и сравнение
  «раньше/позже» неоднозначно. Миллисекунды делают отзыв точным, при
  этом стандартный `iat` сохранён.
- **Почему global revocation — `revoked_before:<user_id>`, а не список
  jti.** Список всех refresh пользователя требует знания/ротации каждого
  jti и растёт; один маркер отзывает всё, что выпущено раньше, и живёт
  TTL refresh. Reset использует тот же маркер для одноразовости.
- **Почему reset отзывает все refresh.** Смена пароля — сигнал
  компрометации; старые сессии должны умереть. Cookie очищается, нужен
  повторный вход. Access-токены остаются до 15 мин (JWT), это §5.
- **Почему `forgot-password` всегда 202.** Иначе endpoint — оракул
  существования email. Текст одинаков; письмо отправляется только
  известному адресу.
- **Почему reset-токен проверяется на global marker.** Без этого один
  токен можно использовать повторно до TTL. Маркер уже нужен для
  logout-all; переиспользование не добавляет состояния.
- **Почему mock sender пишет email в outbox.** Тесты проверяют ссылку/
  токен без SMTP; в dev ссылка в structlog. Outbox — только mock, в
  production-реализации его не будет.
- **Почему rate limit на новые `/auth/*`.** Forgot-password — спам
  писем, reset/verify — перебор токенов; общий Redis-лимит 10/мин на IP
  уже есть, добавление путей не требует новой инфраструктуры.
- **Почему worker вынесен в `app/worker.py` + корневой shim.** Логика
  доступна из wheel (`app.worker`), а `arq worker.WorkerSettings` —
  короткое имя для Docker/CLI; shim реэкспортирует класс и задачу.
- **Почему тесты воркера используют `client.portal.call`.** Глобальный
  `SessionLocal`/asyncpg-пул привязан к loop-у TestClient; `asyncio.run` в
  новом loop даёт «Future attached to a different loop». Portal
  выполняет burst-воркер в том же loop, что и приложение.
- **Почему recovery не делает retry.** Автоматический повтор мог бы
  дублировать частично записанный файл/аудит; безопаснее пометить
  `failed` и дать пользователю создать новую джобу. `max_tries` ARQ
  страхует только исключения до/вне обработчика.
- **Почему `users.is_verified` без `verified_at`.** Для DoD достаточно
  флага; аудит `email_verified` есть в structlog. Отдельная колонка
  времени — при появлении требований к отчётам (KI-055).
