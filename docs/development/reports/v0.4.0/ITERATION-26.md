# Итерация 26 — Auth Tech Debt и Dataset Schema v2 (KI-036, KI-037, KI-038, KI-039, T-028 финал)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **KI-036, KI-037, KI-038, KI-039 закрыты**;
**T-028 (JWT-интеграция в `/admin`) закрыт**. Заведены KI-041…045.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Auth: Argon2id, HttpOnly-cookie, denylist (KI-036, KI-037)

| Файл | Что изменилось |
| :--- | :--- |
| `app/auth/passwords.py` | bcrypt/passlib → **Argon2id** (`argon2.PasswordHasher`, `$argon2id$`); `hash_password`, `verify_password`, `verify_and_update` (нативный rehash при смене параметров; legacy `$2a$/$2b$/$2y$` → `bcrypt.checkpw` → новый Argon2id-хеш); лимит 8..256 символов |
| `app/auth/cookies.py` *(новый)* | `set_refresh_cookie`/`clear_refresh_cookie`/`refresh_cookie_max_age_s`: HttpOnly, `SameSite`, `Path=/auth`, `Secure`, `Domain`, `Max-Age` из настроек |
| `app/auth/denylist.py` *(новый)* | Redis `refresh_denylist:<jti>`: `revoke_refresh_token` (SETEX, TTL до `exp`), `is_refresh_token_revoked`; fail-open + structlog warning при недоступном Redis |
| `app/auth/__init__.py` | Экспорт новых хелперов и `verify_and_update` |
| `app/api/v1/auth.py` | Новый контракт: `/auth/login` — access+user в JSON, refresh в HttpOnly-cookie; `/auth/refresh` — cookie (fallback JSON body), denylist, ротация cookie **и отзыв прежнего jti**; `POST /auth/logout` (204, denylist + очистка cookie); `GET /auth/me` без изменений; Argon2id-миграция в login |
| `app/config.py` | `auth_cookie_name/secure/samesite/path/domain`, `refresh_denylist_enabled`; валидатор «SameSite=None требует Secure=true» |
| `pyproject.toml` | Удалены `passlib[bcrypt]`, `types-passlib`; добавлен `argon2-cffi>=23.1`; `bcrypt>=4.1` — только legacy-verify (без пина `<4.1`) |
| `.env.example` | `AUTH_COOKIE_*`, `REFRESH_DENYLIST_ENABLED` |

### Frontend: память + cookie (KI-037)

| Файл | Что изменилось |
| :--- | :--- |
| `web/src/stores/authStore.ts` | Memory-only: `accessToken`, `user`, `isAuthenticated`, `initializing`; `bootstrapAuth()` (single-flight `/auth/refresh`); удалены `AUTH_STORAGE_KEY`/`PersistedAuth`/`hydrateAuth`/localStorage |
| `web/src/api.ts` | `credentials:"include"` во всех fetch (`send`, `performRefresh`, `streamRequest`, `logoutUser`); refresh без тела (cookie) + single-flight; `logoutUser()` — POST `/auth/logout`; 401-retry сохранён |
| `web/src/types.ts` | `AuthTokens` без `refresh_token`; `RefreshResponse = LoginResponse = AuthTokens & {user}` |
| `web/src/App.tsx` | `void bootstrapAuth()` вместо `hydrateAuth()` |
| `web/src/components/AuthGate.tsx` | `initializing` → loading, затем прежняя логика guard |
| `web/src/components/Layout.tsx` | async logout: `logoutUser()` → `clearSession` → `/login` |
| `web/src/stores/authStore.test.ts`, `web/src/api.auth.test.ts` | Переписаны: memory-only, bootstrap 200/401/сеть, single-flight, credentials include, logout, «localStorage не пишется» |
| `web/e2e/fixtures.ts`, `web/e2e/README.md` | Stateful auth-моки нового контракта (refresh 401 до логина/200 после, logout 204 + сброс, login без refresh_token) |

### FinOps по пользователю (KI-038)

`app/finops.py` (JWT-aware `_identity_from_scope`: `user:{id}` + tier из claim role, без БД; fallback IP + `PRO_IPS`; middleware принимает `Settings`; `request_subject`/`request_tier` — тот же резолвер), `app/main.py` (wiring middleware), `app/api/v1/users.py` (`/v1/users/me/usage` без БД, реальный `used` по user-ключу), `tests/unit/test_token_budget.py`, `tests/integration/test_token_budget_api.py`, `tests/integration/test_usage_roles.py`.

### Admin JWT (T-028 финал)

`app/admin.py` — `_jwt_identity(settings, token)` (decode access → User из БД → is_active → роль admin/auditor), JWT из `Authorization: Bearer`/cookie `access_token` в `AdminAuthProvider` и `AdminAuthGuard`; role `user` → 403 `admin_forbidden`; legacy `X-Admin-Token` и парольная форма сохранены; `tests/integration/test_admin.py` (20 тестов).

### Dataset Schema v2 (KI-039)

`scripts/export_dataset.py` — `schema_version=2` по умолчанию: system = промпт уровня, user = `build_fact_package(parse_pgn(game.pgn), load_analyses(...), level)`, assistant = `edited_text`, вложенный `metadata` (id, commentary_id, game_id, editor_id, level, model, created_at, fact_sheet_chars); v1 — `--schema-version 1`; пропуски no_game/invalid_pgn/no_analysis/invalid_level со статистикой; keyset-батчи.
`scripts/train_lora.py` — `SUPPORTED_SCHEMA_VERSIONS={1,2}`, v2 требует `metadata`, распределение по schema_version, предупреждение о v1-rewrite, эвристика `looks_like_fact_package`, `--system-prompt` для v2.
`tests/fixtures/lora_sample_v2.jsonl` *(новый)*, `tests/integration/test_dataset_export_v2.py` *(новый)*, `tests/unit/test_dataset_export.py`, `tests/unit/test_train_lora.py`, `docs/development/reports/LORA_TRAINING.md`.

### Тесты auth/finops/admin

`tests/unit/test_auth.py`, `tests/integration/test_auth_api.py`, `tests/integration/test_admin.py`, `tests/integration/test_data_isolation.py` (не менялся, но прогоняется в auth-наборах), `tests/unit/test_token_budget.py`, `tests/integration/test_token_budget_api.py`.

### Документация

`README.md` (секция аутентификации, статус, тесты, LoRA/FinOps/KI), `docs/development/SPEC.md` (Argon2id, cookie/denylist, FinOps), `docs/KNOWN_ISSUES.md` (KI-036…039 → Closed, +KI-041…045, сводка 45/24/21), `TODO.md` (T-028 финал, T-043/T-044, T-104 v2), `docs/CHANGELOG.md`, `docs/development/RELEASES.md`, `docs/development/reports/LORA_TRAINING.md`, `.env.example`, этот отчёт.

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Пароли/токены/cookie/denylist | `tests/unit/test_auth.py` | 38 |
| Auth API (cookie/refresh/logout/denylist/argon2) | `tests/integration/test_auth_api.py` | 25 |
| Admin JWT + legacy | `tests/integration/test_admin.py` | 20 |
| FinOps unit | `tests/unit/test_token_budget.py` | 18 |
| FinOps API (user/IP/429/usage) | `tests/integration/test_token_budget_api.py` | 5 |
| Usage по ролям | `tests/integration/test_usage_roles.py` | 4 |
| Экспорт датасета v2/v1 | `tests/unit/test_dataset_export.py` | 20 |
| LoRA-пайплайн v2/v1 | `tests/unit/test_train_lora.py` | 50 |
| Экспорт v2 (seed→export→validate) | `tests/integration/test_dataset_export_v2.py` | 2 |
| Frontend auth | `web/src/stores/authStore.test.ts`, `web/src/api.auth.test.ts` | 22 |
| E2E | `web/e2e/*.spec.ts` | 5 сценариев × 2 проекта |

Проверяемые сценарии:

- **Argon2id:** `$argon2id$`-хеш; roundtrip; `>72 байт` больше не режется; 256/257 символов; `verify_and_update`: argon2-без-rehash → `None`, bcrypt → новый argon2, неверный/битый → `(False, None)`; **живой** login с bcrypt-хешем в БД → `$argon2id$`.
- **Cookies/refresh:** login → `Set-Cookie: refresh_token=…; HttpOnly; Max-Age=604800; Path=/auth; SameSite=lax` (Secure при флаге), в теле refresh нет; refresh по cookie и по body-fallback; ротация; **replay старого refresh после ротации → 401**; logout → denylist → reuse 401; fail-open при Redis down; битые/access-токены → 401; CORS credentialed preflight; rate limit не сломан.
- **Frontend:** bootstrap через `/auth/refresh` (200/401/сеть), single-flight на параллельные 401, `credentials:"include"`, logout POST + очистка сессии, localStorage не используется.
- **FinOps:** admin/auditor → pro, user → free; subject `user:{id}`; A исчерпан (429) при чистом IP, B с того же IP проходит; аноним по IP; usage endpoint отдаёт реальный used/limit/remaining.
- **Admin JWT:** Bearer admin/auditor → 200; user → 403 `admin_forbidden`; битый/просроченный/не-Bearer → 403; cookie `access_token`; без кредов → 401; legacy `X-Admin-Token`/парольная сессия зелёные.
- **Schema v2:** обязательный `metadata`, порядок ролей, `fact_sheet_chars`, v1 legacy, пропуски (нет анализа/битый PGN), dry-run распределение по версиям и warning для v1, реальные `ParsedGame`/`MoveAnalysis` в unit-тестах, интеграционный seed→export.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:** **522/524 passed** (backend, 1 skipped, 1 xfailed), **42/42** frontend, **10/10** E2E mocked, **5/5** E2E real-backend.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД) | `pytest -q` (POSTGRES_PORT=5435, `chessannotator_iter26`, Redis 0, STOCKFISH_PATH) | **522 passed, 1 skipped, 1 xfailed, 1 warning in 218.74s**, exit 0; collected 524. Skip — нет `DEEPSEEK_API_KEY`, xfail — KI-012; **+86** к итерации 25 (435) |
| Auth-набор | `pytest tests/unit/test_auth.py tests/integration/test_auth_api.py tests/integration/test_data_isolation.py -q` | **74 passed** (38+25+11) |
| Admin JWT | `pytest tests/integration/test_admin.py -q` | **20 passed** |
| FinOps | `pytest tests/unit/test_token_budget.py tests/integration/test_token_budget_api.py tests/integration/test_usage_roles.py -q` | **29 passed** |
| Dataset v2 | `pytest tests/unit/test_dataset_export.py tests/unit/test_train_lora.py tests/integration/test_dataset_export_v2.py -q` | **81 passed** |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 69 source files** (exit 0) |
| pip check | `pip check` (после `pip install -e . --no-deps`) | **No broken requirements found** |
| Frontend tests | `pnpm --dir web test` | **42 passed** (6 файлов, 1.27 с; +6 к итерации 25) |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 6.04 с; JS 464.31 КБ (gzip 146.15), CSS 14.29 КБ (gzip 3.77), dist 479 350 B |
| E2E mocked | `pnpm --dir web run test:e2e` | **10 passed** (5 сценариев × chromium/mobile, 13.5 с) |
| E2E real backend | `E2E_REAL_BACKEND=1 VITE_API_PROXY=http://127.0.0.1:8013 … --project=chromium` | **5 passed** (1.3 мин) — cookie `Path=/auth` + `credentials:include` через Vite-proxy работают |
| LoRA dry-run v2 | `python scripts/train_lora.py --dry-run --dataset tests/fixtures/lora_sample_v2.jsonl` | exit 0: 4 записи, `schema_version 2=4`, ~4228 токенов (avg 1057) |
| LoRA dry-run v1 | `… --dataset tests/fixtures/lora_sample.jsonl` | exit 0 + предупреждение о rewrite-семантике (`1=4`) |
| Экспорт v2 (seed) | `python scripts/export_dataset.py --schema-version 2` на seeded-данных | exit 0: exported 1, skipped 2 (`no_analysis` 1, `invalid_pgn` 1); `fact_sheet_chars=550 == len(user)`; system == промпт club |
| Argon2id (live) | bcrypt-хеш в БД → `POST /auth/login` | 200; в БД `$argon2id$…` |

---

## 4. Smoke (ручной сценарий)

**4.1. Auth cookies/denylist (живой uvicorn :8013, `APP_ENV=dev`).**

```http
POST /auth/register → 201
POST /auth/login → 200
  body: {access_token, token_type, expires_in:900, user}   ← refresh_token НЕТ
  Set-Cookie: refresh_token=<JWT>; HttpOnly; Max-Age=604800; Path=/auth; SameSite=lax
GET  /auth/me (Bearer) → 200
POST /auth/refresh (cookie) → 200, cookie ротирована (новый jti)
POST /auth/refresh (старый токен, без cookie) → 401
POST /auth/logout (cookie) → 204
  Set-Cookie: refresh_token=""; HttpOnly; Max-Age=0; Path=/auth; SameSite=lax
POST /auth/refresh (отозванный токен) → 401 {"detail":"Refresh-токен отозван","code":"unauthorized"}
```

Дополнительно: `AUTH_COOKIE_SECURE=true` → в `Set-Cookie` появляется
`Secure`; `SameSite=None` без `Secure` не проходит валидацию конфига;
CORS credentialed preflight на `/auth/login` — `allow-credentials: true`.

**4.2. Argon2id-миграция.** В БД создан пользователь с legacy-хешем
`$2b$12$…`; `POST /auth/login` с верным паролем → 200, после запроса
`users.password_hash` начинается с `$argon2id$` (событие
`password_rehashed` в structlog).

**4.3. FinOps по пользователю.** Пользователь A: в Redis
`usage:<day>:user:<A>` = 123 → `GET /v1/users/me/usage` (Bearer A) →
`used_tokens=123, limit_tokens=50000, remaining=49877, tier=free,
enabled=true`; интеграционный сценарий: A с исчерпанным лимитом → 429,
B с того же IP → middleware пропускает (400 `invalid_pgn` на пустом
теле), аноним с исчерпанным IP-ключом → 429.

**4.4. Admin JWT.** Живой uvicorn: `admin` Bearer → 200, `auditor` → 200,
`user` → 403 `admin_forbidden`, битый Bearer → 403, без кредов → 401,
cookie `access_token=<admin JWT>` → 200, legacy `X-Admin-Token` → 200,
`/admin/login` публична.

**4.5. Экспорт v2 (реальный CLI на seeded-БД).** Seed Game/Move/Motif/
Commentary/Edit + «без анализа» + «битый PGN» → `export_dataset.py
--schema-version 2`:
```
Экспортировано записей: 1; Пропущено записей: 2
  PGN не разобран (InvalidPgnError): 1
  нет анализа ходов (moves пусты): 1
```
Строка: `schema_version=2`; роли system→user→assistant; `user`
начинается с `# Партия`, содержит `# Ключевые моменты`, ход `Nc6`, мотив
`weak_square`; `metadata.fact_sheet_chars == len(user) == 550`;
`system == load_system_prompt("club")`; `train_lora --dry-run` на файле
→ exit 0 (`2=1`). v1-флаг: `user == original_text`, `metadata` нет.

**4.6. E2E real-backend.** `E2E_REAL_BACKEND=1 VITE_API_PROXY=:8013
pnpm --dir web run test:e2e -- --project=chromium` → **5 passed**:
регистрация/вход по cookie, анализ, SSE-комментарий, правка PATCH,
история, usage-badge — всё против живого API.

---

## 5. Что не так

1. **`bcrypt` не удалён полностью (KI-042).** Постановка просила убрать
   `passlib` и `bcrypt`; passlib удалён, но `bcrypt>=4.1` оставлен как
   legacy-verify: Argon2id-библиотеки не умеют проверять bcrypt-хеши, а
   пользователи с такими хешами должны войти. Миграция «на лету» при
   логине; после конвертации всех хешей зависимость удаляется. Это
   осознанное отклонение от буквы задачи — иначе DoD «bcrypt-хеши
   конвертируются при логине» невыполним.
2. **Denylist fail-open.** При недоступном Redis `/auth/logout` всё равно
   возвращает 204 и чистит cookie, а отозванный refresh может работать до
   `exp`. Компромисс доступности (как rate limiting, KI-015),
   задокументирован в `app/auth/denylist.py`; fail-closed — правка одной
   строки при необходимости.
3. **`AUTH_COOKIE_SECURE=false` по умолчанию.** Для dev по http это
   необходимо; в проде за HTTPS обязателен `AUTH_COOKIE_SECURE=true`.
   Добавлена валидация «SameSite=None требует Secure», но сам флаг
   автоматически не включается по `APP_ENV`.
4. **Остаток KI-037 (KI-041).** Нет 2FA, верификации email, сброса
   пароля, «выйти на всех устройствах», лимита попыток по email; парольная
   форма `/admin` остаётся legacy fallback (`ADMIN_PASSWORD`).
5. **RBAC внутри `/admin` не разделён (KI-045).** auditor и admin видят
   одинаковые views (всё read-only, кроме редактирования промптов).
   Итерация закрывала JWT-вход и 403 для `user`, но не политику по views.
6. **User-enumeration по таймингу логина (KI-043).** Для несуществующего
   email 401 быстрее, чем для существующего (Argon2id не выполняется);
   план — dummy-verify.
7. **N+1 при экспорте v2 (KI-044).** `load_analyses` вызывается на каждую
   правку; при нескольких правках одной партии анализ пересобирается.
   Для 100–500 правок приемлемо.
8. **GIT-состояние итерации.** Рабочее дерево содержит изменения всех 5
   задач + доки; коммиты (auth/finops/admin/dataset/frontend/docs) — шаг
   Lead после ревью. `git status` — 31 изменённый + 4 новых файла, все в
   scope, секретов/сгенерированного нет.
9. **e2e-типизация (перенос из итерации 25).** `web/tsconfig.json`
   включает только `src`, поэтому `web/e2e/**` не проверяется
   `pnpm typecheck` (Playwright транспилирует сам). Кандидат — отдельный
   `tsconfig.e2e.json`.
10. **KI-030 не закрыт.** LoRA-обучение на GPU по-прежнему не выполнялось
    (нет CUDA); schema v2 подготовлена, но прирост качества не измерен.
11. **Локальный venv.** После смены зависимостей метаданные
    `pip install -e .` обновлены (`pip check` чист); `passlib` остаётся
    установленным в venv, но в `pyproject.toml` его нет и код не
    импортирует — косметика окружения.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| **KI-041** | Low | auth, security | Остаток KI-037: нет 2FA, верификации email, сброса пароля, отзыва всех сессий; admin RBAC (auditor ≡ admin) |
| **KI-042** | Low | deps, auth | `bcrypt` остаётся зависимостью для legacy-verify; rehash в Argon2id только при успешном логине |
| **KI-043** | Low | auth, security | User-enumeration по таймингу `/auth/login` (нет dummy-Argon2) |
| **KI-044** | Low | dataset, export | N+1 `load_analyses` при экспорте v2 (нет кэша по `game_id`) |
| **KI-045** | Low | admin, security | RBAC внутри `/admin` views не разделён: auditor ≡ admin |

**Закрыты:** KI-036 (Argon2id, passlib удалён), KI-037 (HttpOnly-cookie,
denylist, logout, ротация с отзывом), KI-038 (FinOps по `user_id`),
KI-039 (schema v2 `fact_sheet → commentary`).
**KI-030** остаётся **Open** (обучение LoRA не выполнялось — нет GPU).

Сводка реестра: **45** записей, 24 Open, 21 Closed.

---

## 7. Обоснование

- **Почему Argon2id, а не остаться на bcrypt.** passlib 1.7.4
  несовместим с bcrypt>=4.1 — это был пин и техдолг (KI-036). Argon2id —
  рекомендация OWASP и DESIGN_ADMIN_AND_OPS §4.1, не имеет 72-байтного
  лимита и даёт memory-hard защиту. Библиотека `argon2-cffi` — зрелая,
  с нативным `check_needs_rehash`.
- **Почему bcrypt всё же остаётся.** Удалить его = сломать вход
  пользователям с legacy-хешами: Argon2 не проверяет bcrypt. Выбран
  компромисс: `bcrypt>=4.1` только для legacy-verify и rehash при
  успешном логине; после миграции зависимость удаляется (KI-042).
  Альтернатива (принудительный сброс всех паролей) неприемлема без
  email-флоу.
- **Почему refresh в HttpOnly-cookie, а access — в памяти.** XSS не может
  украсть refresh (HttpOnly) и долгоживущий токен не лежит в
  `localStorage`; access на 15 минут держится в памяти SPA и
  перевыпускается по cookie. `Path=/auth` не отправляет refresh на
  остальной API, `SameSite=Lax` смягчает CSRF для refresh/logout;
  state-changing API по-прежнему требует Bearer (CSRF-поверхность мала).
- **Почему denylist в Redis, а не таблица `refresh_tokens`.** Refresh уже
  подписан и проверяется stateless; для logout нужен быстрый отзыв по
  `jti` с TTL до `exp` — Redis идеален и уже есть в стеке. Fail-open
  выбран ради доступности: недоступность кэша не должна «выключать»
  logout; риск зафиксирован и переключается одной строкой.
- **Почему ротация отзывает прежний jti.** Без этого украденный refresh
  оставался бы валиден до 7 дней даже после легитимного refresh — replay.
  Отзыв старого jti закрывает этот сценарий (найдено верификатором,
  исправлено в этой же итерации + тест).
- **Почему `/auth/logout` появился.** Раньше logout чистил только клиент;
  серверного отзыва не было. Новый эндпоинт не требует access-токена
  (достаточно refresh из cookie), идемпотентен и всегда 204 — клиент не
  должен застревать на выходе.
- **Почему FinOps резолвит пользователя без БД.** Middleware работает до
  зависимостей FastAPI; поход в БД на каждый запрос — лишняя нагрузка и
  циклическая зависимость. JWT уже содержит `sub`/`role`; цена — tier
  берётся из токена (до 15 минут после смены роли), что приемлемо.
  Анонимный путь сохранён для health/старых клиентов.
- **Почему schema_version=2 и восстановление пакета фактов из БД.**
  Цель KI-039 — учить модель генерировать из фактов, а не переписывать.
  Пакет фактов детерминированно восстанавливается из
  `games/moves/motifs` (`build_fact_package`), поэтому не нужна миграция
  и сохранение пакета при каждой генерации; v1 оставлен флагом для
  сравнения и обратной совместимости. Пропуски проблемных записей со
  статистикой лучше, чем падение экспорта на тысячах правок.
- **Почему JWT в `/admin`, а не только пароль.** Пользователи уже
  аутентифицируются JWT; парольная форма — второй секрет и не отражает
  роли. `AuthProvider`/`AuthGuard` принимают access-токен из
  заголовка/cookie, проверяют роль в БД (а не только claim — важна
  актуальность `is_active`/роли), а legacy-пути сохранены для
  автоматизации и переходного периода.
- **Почему тесты и smoke обязательны для всех четырёх KI.** Каждый пункт
  — безопасность/контракт; unit-тестов мало: проверены живые
  Set-Cookie/ротация/denylist, bcrypt→argon2 на реальной БД, JWT-матрица
  admin и per-user лимиты. Верификатор независимо поднимал uvicorn и
  повторял smoke, включая real-backend E2E.
