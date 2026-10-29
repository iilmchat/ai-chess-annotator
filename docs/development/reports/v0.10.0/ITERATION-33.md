# Итерация 33 — Frontend Auth UX & Export Retention (KI-055, KI-054)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. **KI-055 закрыт** (frontend-страницы verify/reset,
экран подтверждения email, logout-all в меню профиля); **KI-054 закрыт**
(retention `export_jobs` + `EXPORT_DIR`). Новые KI — KI-056 (SMTP/2FA/SSO/
лимит по email/resend) и KI-057 (signed URL экспортов).
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### KI-055: frontend auth UX

| Файл | Что изменилось |
| :--- | :--- |
| `web/src/pages/VerifyEmailPage.tsx` *(новый)* | `/verify-email?token=…`: `GET /auth/verify-email` при монтировании; статусы «Идёт проверка…» / «Email успешно подтвержден!» / ошибка (invalid/missing/network); single-flight на токен (StrictMode); после успеха обновляет профиль в `authStore` через `/auth/me`; кнопки «На главную»/«Войти»/«Повторить» |
| `web/src/pages/ForgotPasswordPage.tsx` *(новый)* | `/forgot-password`: форма email, `POST /auth/forgot-password`, нейтральное сообщение «Если такой email существует…» (no enumeration), ссылка на вход |
| `web/src/pages/ResetPasswordPage.tsx` *(новый)* | `/reset-password?token=…`: новый пароль + повтор, проверки длины/совпадения, `POST /auth/reset-password`, редирект на `/login` с сообщением; отдельный экран для missing/invalid токена |
| `web/src/components/ProfileMenu.tsx` *(новый)* | Выпадающее меню профиля в шапке: «Выйти» и «Выйти со всех устройств» (`POST /auth/logout-all`); закрытие по клику вне/Escape, `aria-haspopup`/`aria-expanded`, `role="menu"` |
| `web/src/components/AuthGate.tsx` | Экран «Подтвердите email» для `is_verified=false` (роль `user`; admin/auditor bypass), кнопка «Я подтвердил(а) — проверить снова» (повторный `/auth/me`), выход |
| `web/src/components/Layout.tsx` | Вместо email+кнопки — `ProfileMenu` |
| `web/src/pages/HomePage.tsx` | 403 `email_not_verified` → понятное сообщение вместо `detail (code)` |
| `web/src/pages/LoginPage.tsx` | Ссылка «Забыли пароль?» (режим входа), баннер успешного сброса из `location.state` |
| `web/src/App.tsx` | Публичные маршруты `/verify-email`, `/forgot-password`, `/reset-password` вне `AuthGate` |
| `web/src/api.ts` | `verifyEmail`, `forgotPassword`, `resetPassword`, `logoutAllUser`; action-эндпоинты в `NO_REFRESH_PATHS` (401 там = «токен недействителен», а не истёк access); `isNoRefreshPath` отрезает query |
| `web/src/types.ts` | `EmailVerificationResponse`, `MessageResponse` |
| `web/src/i18n.ts` | RU/EN: `verifyEmail`, `forgotPassword`, `resetPassword`, `auth.logoutAll`/`forgotPasswordLink`/`emailNotVerified*` |
| `web/src/styles.css` | Стили меню профиля, auth-статусов, ссылок, spinner, `banner--success` |
| `web/src/api.authFlow.test.ts` *(новый)* | 7 Vitest: URL-encoding verify, отсутствие refresh на 401, тела forgot/reset, logout-all 204/сеть |
| `web/e2e/auth-flow.spec.ts` *(новый)* | 9 Playwright-сценариев: verify success/invalid/missing, unverified gate, forgot, reset success/mismatch/invalid, logout-all |
| `web/e2e/fixtures.ts` | Моки `/auth/verify-email`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/logout-all`; `is_verified` в `AuthUser`; префикс `unverified-` для экрана подтверждения |
| `web/e2e/auth.spec.ts`, `web/e2e/README.md` | Меню профиля вместо прямой кнопки; описание новых спеков/моков |
| `web/playwright.config.ts`, `web/vite.config.ts` | `E2E_PREVIEW=1` — build + `vite preview` (обход sandbox-блокировки esbuild) и `preview.proxy` для `/auth`/`/v1` |

### KI-054: retention экспортов

| Файл | Что изменилось |
| :--- | :--- |
| `scripts/prune_export_jobs.py` *(новый)* | Батчевое удаление `export_jobs` старше N дней + файлов внутри `EXPORT_DIR`; безопасные пути (`file_path`/канонический `edits-<id>.jsonl`, симлинк наружу → skip+warn); файл удаляется до строки; `--retention-days`, `--export-dir`, `--batch-size`, `--max-batches`, `--sleep-ms`, `--dry-run`, `--json`; exit 0/2 |
| `app/config.py` | `export_retention_days=7` (KI-054); `app_base_url` теперь публичный URL SPA (dev default `http://localhost:5173`) |
| `app/email/sender.py` | Ссылки писем ведут на `/verify-email` и `/reset-password` (SPA), а не на JSON-API; текст reset без «передайте токен в POST» |
| `tests/unit/test_prune_export_jobs.py` *(новый)* | 9 тестов: cutoff, naive/UTC, защита от 0 дней, CLI defaults/overrides/invalid, JSON-формат |
| `tests/integration/test_prune_export_jobs.py` *(новый)* | 6 тестов: старые строки+файлы удаляются, свежие остаются; батчи; `--max-batches`; dry-run; файл вне `EXPORT_DIR` не тронут; отсутствующий файл; CLI JSON |
| `tests/unit/test_email_sender.py`, `tests/integration/test_auth_ki041.py` | Ожидаемые ссылки обновлены на SPA-маршруты |
| `Dockerfile` | `COPY scripts ./scripts` — ops-скрипты доступны внутри контейнера для cron |
| `.env.example` | `EXPORT_RETENTION_DAYS=7`, `APP_BASE_URL=http://localhost:5173` + комментарии |
| `docker-compose.prod.yml` | `EXPORT_RETENTION_DAYS`, `APP_BASE_URL` (`PROD_*` переопределения) |

### Документация

`docs/development/reports/v0.10.0/ITERATION-33.md` (этот файл),
`TODO.md` (T-045, T-028b/T-034b остатки), `docs/CHANGELOG.md`,
`docs/development/RELEASES.md`, `docs/KNOWN_ISSUES.md` (KI-054/KI-055
Closed, +KI-056/KI-057, сводка 57), `docs/development/SPEC.md` (§5
auth-flow/страницы), `docs/development/ARCHITECTURE.md` (§5.2/§5.3, §6),
`docs/development/GETTING_STARTED.md` (§3.4 env, §9.3 retention/cron),
`docs/development/DESIGN_ADMIN_AND_OPS.md` (§4.1, §5.4),
`docs/user_guide/USER_GUIDE.md` (§7.10 UI-flow, §8 ограничения),
`README.md` (761 backend/53 frontend, итерация 33, 57 KI),
`docs/README.md` (индекс v0.10.0).

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Retention export_jobs/EXPORT_DIR (unit) | `tests/unit/test_prune_export_jobs.py` | 9 |
| Retention export_jobs/EXPORT_DIR (integration, PostgreSQL) | `tests/integration/test_prune_export_jobs.py` | 6 |
| Frontend auth-flow API | `web/src/api.authFlow.test.ts` | 7 |
| Auth-UX E2E | `web/e2e/auth-flow.spec.ts` | 9 сценариев × 2 проекта = 18 |
| E2E всего | `web/e2e/*.spec.ts` | 14 сценариев × 2 = **28** |
| Обновлены | `test_email_sender.py`, `test_auth_ki041.py`, `auth.spec.ts`, `fixtures.ts` | ссылки/меню/моки |

Проверяемые сценарии:

- **Verify email:** успешный токен → «Email успешно подтвержден!» + ссылки;
  невалидный токен → 401-сообщение; отсутствие токена → запрос не уходит;
  `verifyEmail` URL-encode-ит токен и не делает refresh на 401.
- **Unverified gate:** пользователь `unverified-…` логинится, `/auth/me`
  возвращает `is_verified=false`, `AuthGate` показывает заголовок
  «Подтвердите email», email и кнопку повторной проверки; обычный mocked-
  пользователь проходит дальше (регрессия analyze/commentary/history).
- **Forgot/reset:** ссылка «Забыли пароль?» с `/login`; отправка формы →
  нейтральное сообщение; reset с валидным токеном → `/login` + баннер;
  несовпадение паролей и использованный токен → ошибка без редиректа.
- **Logout-all:** меню профиля → пункт «Выйти со всех устройств» →
  реальный `POST /auth/logout-all`, редирект `/login`, сессия очищена;
  старый пункт «Выйти» продолжает работать.
- **Retention:** старые строки и файлы удалены, свежие остались; батчи и
  `--max-batches`; dry-run ничего не меняет; `file_path` вне `EXPORT_DIR`
  не удаляется; отсутствующий файл не роняет запуск; CLI печатает JSON и
  возвращает 0.
- **Ссылки писем:** `/verify-email?token=…`, `/reset-password?token=…`
  (SPA), не `/auth/...`.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend).
**Tests:** **761 passed** (backend; 763 collected, 1 skipped, 1 xfailed),
**53/53** frontend, **28/28** E2E mocked.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД iter33) | `pytest -q` (POSTGRES_DB=`chessannotator_iter33_final`, Redis 6381, `STOCKFISH_PATH`) | **761 passed, 1 skipped, 1 xfailed in 241.81 s** (exit 0); collected 763; **+15** к итерации 32 (746 passed) |
| Retention (unit + integration) | `pytest tests/unit/test_prune_export_jobs.py tests/integration/test_prune_export_jobs.py -q` | **15 passed** |
| Auth KI-041 (обновлённые ссылки) | `pytest tests/integration/test_auth_ki041.py -q` | **8 passed** |
| Email links unit | `pytest tests/unit/test_email_sender.py -q` | **6 passed** |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` + `mypy scripts/prune_export_jobs.py` | **Success: 84 + 1 файл** (exit 0) |
| Alembic | `upgrade head`, `alembic check`, `downgrade base → upgrade head` | head `dcf1da1421cd`; «No new upgrade operations detected»; roundtrip OK; **16 app-таблиц** (+`alembic_version`), 13 ревизий |
| Frontend tests | `pnpm --dir web test` | **53 passed** (8 файлов; +7) |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 5.60 с; JS 480.87 КБ (gzip 149.85), CSS 15.70 КБ (gzip 4.09) |
| E2E mocked | `E2E_PREVIEW=1 pnpm --dir web run test:e2e` | **28 passed** (14 сценариев × chromium/mobile), 23.0 с |
| Docker build | `docker build -t chessannotator:iter33 .` | exit 0; `import app.main, worker, scripts.prune_export_jobs` OK; `run_export_job`; `DEFAULT_RETENTION_DAYS=7`; `cairosvg_available()=True` |
| Compose config | `docker compose config` (dev + prod) | exit 0 оба |

**Про live-тесты:** первый полный прогон дал 759 passed + 2 failed в
`test_api_chat.py::test_live_chat_names_through_api` и
`test_api_commentary.py::test_live_club_commentary_persisted_and_validated`
— LM Studio вернул `Failed to load model "qwen/qwen3-4b-2507"` (модель
перезагружалась). После прогрева модели оба теста прошли (72.38 с), затем
полная сюита была перезапущена; результат — 761 passed.

---

## 4. Smoke (ручной сценарий)

**4.1. Playwright, mocked (Windows/DSH, `E2E_PREVIEW=1`).**

```text
ok  [chromium] auth-flow.spec.ts › verify-email: успешное подтверждение…
ok  [chromium] auth-flow.spec.ts › неподтверждённый пользователь видит экран…
ok  [chromium] auth-flow.spec.ts › forgot-password: нейтральное сообщение…
ok  [chromium] auth-flow.spec.ts › reset-password: успешная смена…
ok  [chromium] auth-flow.spec.ts › logout-all: пункт меню вызывает API…
...
28 passed (23.0s)
```

**4.2. Retention CLI (dry-run на iter33-БД).**

```text
$ python scripts/prune_export_jobs.py --dry-run --json
{
  "retention_days": 7,
  "cutoff": "2026-10-23T...",
  "batch_size": 100,
  "batches": 0,
  "deleted_rows": 0,
  "deleted_files": 0,
  "missing_files": 0,
  "skipped_files": 0,
  "dry_run": true,
  "capped": false
}
```

**4.3. Миграция.** `alembic upgrade head` → `dcf1da1421cd`;
`alembic check` — «No new upgrade operations detected»; полный
`downgrade base && upgrade head` проходит; 17 строк в
`information_schema` (16 app-таблиц + `alembic_version`); 13 ревизий.

**4.4. Docker.** `docker build -t chessannotator:iter33 .` → exit 0;
`docker run --rm chessannotator:iter33 python -c ...`:

```text
main_ok True
worker_fn run_export_job
retention_days 7
cairosvg True
```

`scripts/` скопирован в образ (`Dockerfile`), поэтому retention-скрипт
доступен для cron из контейнера.

---

## 5. Что не так

1. **Email по-прежнему mock.** `MockEmailSender` пишет ссылку в structlog;
   реального SMTP/SES нет — KI-056. `EMAIL_FROM` не используется.
2. **Нет resend-verification.** Если письмо потеряно, пользователь видит
   экран «Подтвердите email», но не может запросить новую ссылку —
   KI-056. Подсказка про «Спам» есть.
3. **Access-токены не отзываются мгновенно.** `logout-all`/reset
   инвалидируют refresh, но уже выданный access живёт до 15 минут —
   свойство stateless JWT (как в итерации 32).
4. **Real-backend E2E теперь упирается в верификацию.** После регистрации
   новый пользователь `is_verified=false`; real-режим требует перехода по
   ссылке из лога, иначе analyze/commentary получат 403. Mocked-режим
   (CI/DoD) авто-подтверждает обычный email; `unverified-` — явный тест
   экрана. Real-режим в итерации 33 не перезапускался (KI-040/056).
5. **Dev-сервер Vite не стартует в DSH-песочнице на Windows.** esbuild
   получает `Access is denied` при записи в `web/node_modules/.vite`
   (как в итерации 32). E2E локально — `E2E_PREVIEW=1` (build+preview);
   CI использует `pnpm dev`.
6. **Retention удаляет и `queued`/`running` старше cutoff.** Такие строки
   заведомо мертвы (recovery переводит `running>15 мин → failed`), но
   формально это не только завершённые джобы. Для v1 сознательно.
7. **Ошибка удаления файла не блокирует удаление строки.** При
   правах/занятости файла строка удаляется, файл остаётся (диск может
   течь); в логах `export_prune_file_remove_failed`, в отчёте
   `skipped_files`. После исправления прав следующий запуск строку уже не
   увидит — файл-сироту нужно удалить вручную.
8. **Файлы-сироты без строки в БД не подметаются.** Скрипт идёт от
   `export_jobs`; если строку удалили вручную, файл останется. Для
   штатного потока (файл создаётся только джобой) это не проблема.
9. **Cutoff по `created_at`, а не `finished_at`.** Долгая джоба,
   завершившаяся позже, может быть удалена раньше, чем через 7 дней после
   завершения. Для фоновых экспортов (минуты) допустимо.
10. **Нет signed URL.** Скачивание — только JWT/cookie admin/auditor;
    внешняя ссылка с TTL — KI-057.
11. **`scripts/` теперь в Docker-образе.** Это нужно для cron из
    контейнера, но немного увеличивает образ; отдельного планировщика
    внутри приложения нет.
12. **`APP_BASE_URL` изменил смысл.** Раньше — API-URL, теперь — публичный
    URL SPA. В `docker-compose.prod.yml` по умолчанию
    `http://127.0.0.1:8000` (loopback) — для реального домена нужно
    переопределить `PROD_APP_BASE_URL`.
13. **Письма в dev ведут на `localhost:5173`**, а API — на 8000; это
    осознанно (Vite-роуты), но требует, чтобы фронтенд был запущен.
14. **Пароль на фронте проверяется только по длине ≥ 8.** Верхняя граница
    256 и сложность — на backend; сообщения об ошибках 422 показываются
    как generic.
15. **`logout-all` fail-open.** При недоступном Redis backend отвечает
    204, но старые refresh могут жить до TTL; так же в итерации 32.
16. **Per-email rate limit не сделан.** Общий 10/мин на IP (KI-056);
    за NAT несколько пользователей делят лимит.
17. **2FA/TOTP/SSO не сделаны** — KI-056/T-118.
18. **AuthGate блокирует все приватные маршруты**, а не только `/`:
    неверифицированный пользователь не увидит и `/history`. Это
    соответствует «генерация закрыта», но жёстче, чем backend (он
    разрешает чтение).
19. **E2E-моки считают обычный email verified.** Расхождение с реальным
    backend (регистрация → false) компенсируется префиксом `unverified-`;
    сценарий «регистрация → письмо → verify» в E2E не проверяется (нужен
    доступ к outbox/логу).
20. **Live-тесты LLM нестабильны в окружении.** Первый полный прогон
    упал на загрузке модели в LM Studio; повтор после прогрева — зелёный.
    Это не изменение кода, но полный прогон стоит повторять при сбое
    модели.
21. **KI-030 не закрыт.** LoRA на GPU по-прежнему не обучалась.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| KI-056 | Low | auth, security | Auth hardening: реальный SMTP/SES, 2FA/TOTP, SSO, per-(email, IP) лимит попыток, resend-verification. Frontend-страницы и logout-all закрыты в итерации 33 |
| KI-057 | Low | admin, api, security | Нет подписанных URL для скачивания готовых экспортов (TTL/scope); доступ только JWT/cookie admin/auditor. Retention закрыт в итерации 33 |

**KI-054 закрыт** (retention; signed URL — KI-057). **KI-055 закрыт**
(frontend auth-UX; остаток — KI-056). Сводка реестра: **57** записей,
25 Open, 32 Closed.

---

## 7. Обоснование

- **Почему ссылки в письмах ведут на SPA, а не на API.** Задача KI-055 —
  убрать ручное копирование токена. API-эндпоинт возвращает JSON, поэтому
  письмо должно открывать React-маршрут `/verify-email?token=…`, который
  сам делает запрос. `APP_BASE_URL` переосмыслен как публичный URL
  приложения (dev — Vite 5173, prod — тот же origin, где раздаётся
  `web/dist`), а не URL API.
- **Почему `verifyEmail` не делает refresh на 401.** 401 здесь означает
  «токен письма недействителен/просрочен», а не «истёк access». Refresh
  замаскировал бы ошибку и мог бы очистить валидную сессию. Те же пути
  (`forgot/reset`) добавлены в `NO_REFRESH_PATHS`; проверка пути отрезает
  query, иначе `/auth/verify-email?token=…` не совпал бы с set.
- **Почему single-flight на verify-токен.** React.StrictMode в dev
  запускает эффект дважды; подтверждение идемпотентно на backend, но два
  GET — лишний шум и потенциальная гонка. Модульный `Map<token, Promise>`
  переживает повторный mount и удаляется после завершения, чтобы
  «Повторить» создал новый запрос.
- **Почему профиль обновляется после verify.** `AuthGate` кэширует
  `user`; без `getMe()`/`setUser()` после успешного подтверждения переход
  «На главную» снова показал бы экран «Подтвердите email» до перезагрузки
  страницы.
- **Почему экран подтверждения в `AuthGate`, а не только 403 в `api.ts`.**
  AuthGate видит `is_verified` из `/auth/me` и блокирует UX до запроса;
  `HomePage` дополнительно обрабатывает 403 `email_not_verified` на случай
  устаревшего профиля. Два уровня дают понятный экран и точную ошибку.
- **Почему admin/auditor обходят gate.** Backend (`get_current_verified_user`)
  уже пропускает служебные роли; UI повторяет ту же политику, иначе
  админ с `is_verified=false` был бы заблокирован в интерфейсе.
- **Почему dropdown, а не две кнопки.** «Выйти» и «Выйти со всех
  устройств» — редкие деструктивные действия; меню профиля убирает
  случайные клики и вмещает оба пункта. Триггер сохраняет текст
  «Вы вошли как email», поэтому существующие E2E-хелперы (`registerAndLogin`)
  не меняются.
- **Почему `logout-all` не пробрасывает ошибку.** Пользователь нажал
  «выйти»; локальная сессия должна исчезнуть даже при недоступном
  backend (как у `logoutUser`). Backend fail-open и так отвечает 204.
- **Почему retention батчами.** Один большой `DELETE` дольше держит
  блокировки и раздувает WAL; батчи по 100 с коммитом и паузой дают
  autovacuum'у шанс. `--max-batches` ограничивает объём одного cron-запуска.
- **Почему файл удаляется до строки.** Падение между шагами оставит
  строку без файла (download → 410, это корректный статус) и освободит
  диск. Обратный порядок дал бы файл-сироту, о котором никто не знает, —
  диск бы не освободился никогда.
- **Почему путь проверяется через `resolve()`/`is_relative_to`.** `file_path`
  хранится в БД и может быть подменён; канонический
  `edits-<id>.jsonl` может оказаться симлинком наружу. Скрипт удаляет
  только пути внутри `EXPORT_DIR`, остальные логирует как `skipped`.
- **Почему cutoff по `created_at`.** Это самая ранняя точка жизни джобы;
  для экспортов, живущих минуты, разница с `finished_at` несущественна, а
  отдельная колонка не нужна. Мёртвые `queued/running` старше 7 дней всё
  равно надо чистить.
- **Почему значение 7 дней.** Экспорт — воспроизводимый артефакт: его
  можно пересоздать из `edits`. Неделя покрывает типичный цикл
  «сделал → скачал → обучил», но не даёт диску расти квадратично. Настройка
  вынесена в `EXPORT_RETENTION_DAYS`, cron может переопределить
  `--retention-days`.
- **Почему `--dry-run`/`--json` как у `prune_llm_calls.py`.** Единый
  ops-интерфейс: те же exit codes, машинный отчёт, возможность сначала
  посмотреть объём. Это снижает риск «cron удалил не то».
- **Почему `scripts/` добавлены в Docker-образ.** Инструкция по cron
  должна работать и в контейнерной поставке; иначе
  `docker compose exec app python scripts/prune_export_jobs.py` падал бы
  с `No such file`. Логика уже не импортируется из `scripts` в `app`
  (KI-051), так что копирование безопасно.
- **Почему `E2E_PREVIEW` вместо правки тестов.** Ограничение среды
  (sandbox блокирует esbuild-кэш) не должно менять сценарии; preview
  раздаёт тот же собранный `dist`, а proxy `/auth`/`/v1` сохраняет
  real-backend-режим. CI продолжает использовать dev-сервер.
- **Почему новые страницы без lazy-loading.** В проекте уже нет
  route-level code-split; добавление трёх небольших страниц не меняет
  порядок бандла (480.87 КБ / 149.85 gzip, +15.7 КБ к итерации 32).
  Выносить их в отдельные чанки — отдельная задача производительности.
