# Итерация 24 — JWT-авторизация и изоляция данных (T-041, KI-035)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **KI-035 закрыт**; заведены KI-036, KI-037, KI-038.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Backend — создано

| Файл | Назначение |
| :--- | :--- |
| `alembic/versions/c7d8e9f0a1b2_jwt_auth_and_ownership.py` | `users.role/is_active/password_hash/last_login_at`, `commentaries.user_id`, `edits.user_id`, индексы |
| `app/auth/__init__.py` | публичный контракт пакета |
| `app/auth/passwords.py` | bcrypt-хеширование (passlib), лимит 72 байта |
| `app/auth/tokens.py` | access/refresh JWT (python-jose), `decode_token` с `expected_type` |
| `app/auth/deps.py` | `get_current_user`, `get_current_user_optional`, `require_admin` |
| `app/auth/access.py` | `ensure_game_access`, `get_accessible_game`, `get_accessible_commentary` (404 для чужого) |
| `app/auth/testing.py` | тестовый seam (только `APP_ENV=test` + `TEST_AUTH_BYPASS`) |
| `app/api/v1/auth.py` | `POST /auth/register|login|refresh`, `GET /auth/me` |

### Backend — изменено

`app/db/models.py` (enum `AdminRole`, поля `User`, `Commentary.user_id`, `Edit.user_id`, индексы, `Edit.editor` join по `editor_id`), `app/config.py` (`test_auth_bypass`), `app/errors.py` (`AuthError`/`ForbiddenError`/`UserExistsError`; `EmbeddingError` — salvage), `app/main.py` (роутер `/auth`, test-seam, rate-limit `/auth/login|register`), `app/api/v1/{analyze,games,commentary,chat,users}.py`, `app/commentary/{service,streaming}.py` (порядок мотивов, `Edit.user_id`, `Commentary.user_id` в SSE), `app/rag/{embedder,service}.py` и `scripts/ingest_pgn.py` (salvage), `tests/conftest.py` (ENV seam), `tests/integration/test_api_games.py`, `tests/unit/test_rag.py`, `pyproject.toml` (passlib+bcrypt<4.1, python-jose, email-validator, стабы).

### Тесты — создано

`tests/unit/test_auth.py`, `tests/integration/test_auth_api.py`, `tests/integration/test_data_isolation.py`, `tests/integration/test_usage_roles.py`, `tests/unit/test_embeddings.py`, `tests/integration/test_embeddings_live.py`, `tests/unit/test_ingest_pgn.py`, `tests/fixtures/rag_sample.pgn`.

### Frontend

Создано: `web/src/stores/authStore.ts`, `web/src/pages/LoginPage.tsx`, `web/src/components/AuthGate.tsx`, `web/src/stores/authStore.test.ts`, `web/src/api.auth.test.ts`.
Изменено: `web/src/api.ts` (Bearer + single-flight refresh), `types.ts` (`AuthUser`, `AuthTokens`, `UserRole` c `auditor`), `App.tsx` (`/login`, guard, `hydrateAuth`), `components/Layout.tsx` (профиль/выход), `styles.css` (`.auth-*`), `i18n.ts` (RU/EN `auth.*`), `vite.config.ts` (прокси `/auth`).

### Документация / конфиг

`.env.example`, `README.md`, `TODO.md` (T-041, T-028), `docs/CHANGELOG.md`, `docs/KNOWN_ISSUES.md` (KI-035 Closed, +KI-036…038), `docs/development/RELEASES.md`, `docs/README.md`, `docs/development/PROMPT_CURRENT.md`, `docs/development/SPEC.md`, этот отчёт.

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Пароли/JWT (unit) | `tests/unit/test_auth.py` | 24 |
| Auth API (real auth) | `tests/integration/test_auth_api.py` | 15 |
| Изоляция данных | `tests/integration/test_data_isolation.py` | 11 |
| Usage по ролям | `tests/integration/test_usage_roles.py` | 4 |
| Эмбеддинги (offline) | `tests/unit/test_embeddings.py` | 21 |
| Эмбеддинги (live LM Studio) | `tests/integration/test_embeddings_live.py` | 4 |
| CLI ingest | `tests/unit/test_ingest_pgn.py` | 20 |
| GET games (parity/FEN/404/комментарии) | `tests/integration/test_api_games.py` | 8 (было 3) |
| Frontend auth store | `web/src/stores/authStore.test.ts` | 7 |
| Frontend API auth | `web/src/api.auth.test.ts` | 9 |

Проверяемые сценарии:

- **auth:** регистрация (201, lowercase email, 409 на дубликат даже в другом
  регистре, 422 на пароль <8 / >72 байт), вход (200 пара токенов+профиль,
  401 неверный пароль, 403 inactive, `last_login_at`), refresh (200 ротация,
  401 access/битый токен), me (200; 401 без токена / с refresh / non-Bearer).
- **изоляция:** user A/B/admin; list — только свои (admin все, legacy
  NULL-owner только admin); GET чужого → 404; POST commentary/chat на чужую
  партию → 404 **до LLM**; PATCH чужого комментария → 404, автор получает
  `edits.user_id`; anonymous → 401 на analyze/games/chat.
- **usage:** anonymous free (IP), user free, admin/auditor pro.
- **регрессии:** вся прежняя сюита (286 тестов) остаётся зелёной под
  тестовым seam; реальная авторизация проверяется отдельными тестами.
- **фронтенд:** Bearer-заголовок, single-flight refresh, очистка сессии при
  неудачном refresh, гидратация store из `localStorage`, login/register.

---

## 3. Результаты

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend suite | `pytest -q` (POSTGRES_PORT=5435, Redis 6380, STOCKFISH_PATH) | **382 passed, 1 skipped, 1 xfailed in 210.78s** |
| Auth (целевой) | `pytest tests/unit/test_auth.py tests/integration/test_auth_api.py -q` | **39 passed** |
| Изоляция/usage | `pytest tests/integration/test_data_isolation.py tests/integration/test_usage_roles.py -q` | **15 passed** (11 + 4) |
| Salvage-набор | 7 файлов (embeddings/CLI/games/rag) | **92 collected**, целевой прогон **64 passed** |
| ruff | `ruff check .` | **All checks passed!** |
| mypy | `mypy app` | **Success: no issues found in 67 source files** |
| Миграции | `alembic upgrade head`, `alembic check` | head `c7d8e9f0a1b2`; «No new upgrade operations detected» |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend tests | `pnpm --dir web test` | **6 файлов / 36 passed** |
| Frontend build | `pnpm --dir web build` | **99 модулей, built in 4.38s**, JS 465.15 kB (gzip 146.41 kB), CSS 14.29 kB |

Build: **0 errors / 0 warnings**; frontend-тесты: 36/36; backend: сбор полного
sюита описан выше (skip — нет `DEEPSEEK_API_KEY`, xfail — маркер KI-012).

---

## 4. Smoke (uvicorn + curl, живой прогон)

Сервер: `uvicorn app.main:app --host 127.0.0.1 --port 8011` (temp Postgres
`127.0.0.1:5435`, Redis `6380`, `APP_ENV=dev` → JWT обязателен, test-seam выключен).

```bash
# 1) Регистрация
curl -s -X POST http://127.0.0.1:8011/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke-875dc8f6@example.com","password":"strong-password-25"}'
# → HTTP 201
# {"id":63,"email":"smoke-875dc8f6@example.com","level":"club","role":"user",
#  "is_active":true,"created_at":"2026-10-29T07:13:48.201881Z"}

# 2) Логин → токены
curl -s -X POST http://127.0.0.1:8011/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke-875dc8f6@example.com","password":"strong-password-25"}'
# → HTTP 200; ключи: access_token, expires_in (900), refresh_token,
#   token_type (bearer), user{id,email,level,role,is_active,created_at}

# 3) Запрос партий С токеном
TOKEN=<access_token из ответа>
curl -s -i http://127.0.0.1:8011/v1/games -H "Authorization: Bearer $TOKEN"
# → HTTP 200; []
#   (у нового пользователя партий нет; список ограничен своими)

# 4) Запрос партий БЕЗ токена
curl -s -i http://127.0.0.1:8011/v1/games
# → HTTP 401
# {"detail":"Требуется авторизация: заголовок Authorization: Bearer",
#  "code":"unauthorized"}

# 5) analyze без токена
curl -s -i -X POST http://127.0.0.1:8011/v1/analyze \
  -H "Content-Type: application/json" -d '{"pgn":"1. e4 e5","level":"club"}'
# → HTTP 401 {"detail":"Требуется авторизация…","code":"unauthorized"}

# 6) Неверный пароль
curl -s -i -X POST http://127.0.0.1:8011/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke-875dc8f6@example.com","password":"wrong-password-1"}'
# → HTTP 401 {"detail":"Неверный email или пароль","code":"unauthorized"}
```

Дополнительно проверено: `GET /auth/me` с токеном → 200 (профиль),
без токена → 401; регистрация/логин через `users` с уникальными email;
`POST /v1/games` без токена — 401; чужой `analysis_id` в `/v1/games/{id}` — 404
(см. `test_data_isolation.py`).

---

## 5. Что не так

1. **Тестовый seam авторизации.** Старая сюита (286 тестов) написана до JWT;
   чтобы не переписывать сотни анонимных вызовов, `create_app()` при
   `APP_ENV=test` **и** `TEST_AUTH_BYPASS=true` подменяет
   `get_current_user` тестовым admin-пользователем. В `dev`/`prod` ветка
   недостижима (условие включает `APP_ENV`), по умолчанию флаг `False`;
   реальная авторизация проверяется отдельными тестами с
   `test_auth_bypass=False`. Риск: если выставить `APP_ENV=test` в
   production-окружении, авторизация будет отключена — это ошибка
   конфигурации, зафиксировано в отчёте.
2. **Refresh-токены статичны.** Нет серверного отзыва/`jti`-denylist; logout
   чистит только клиент; старый refresh живёт до `exp` (7 дней) — KI-037.
3. **Токены в `localStorage`.** Уязвимость к XSS-краже токенов; httpOnly
   cookie требует CSRF-стратегии/BFF — KI-037.
4. **bcrypt-пин.** passlib 1.7.4 несовместим с bcrypt>=4.1 → зафиксирован
   `bcrypt<4.1`; пароль дополнительно ограничен 72 UTF-8 байтами (иначе
   bcrypt-ошибка → 500). Переход на Argon2id — KI-036.
5. **Бюджеты токенов по IP.** `TokenBudgetMiddleware` остался IP-based
   (T-031), хотя `/v1/users/me/usage` уже отдаёт тир по роли — KI-038.
6. **`/admin` — отдельная парольная сессия.** JWT-роли не интегрированы в
   starlette-admin (RBAC внутри админки — T-028).
7. **Нет email-верификации и сброса пароля**, нет лимита попыток входа по
   email (только IP-rate-limit 10/мин на `/auth/login|register`) — KI-037.
8. **Legacy-партии** (`games.user_id IS NULL`) доступны только admin: для
   обычных пользователей они «не существуют» (404). Backfill не делался
   осознанно: привязывать старые партии не к кому.
9. **Живые LM Studio тесты** периодически флакуют при выгрузке модели
   (`Model is unloaded`); финальный полный прогон зелёный, но это свойство
   окружения, а не кода.
10. **Локальная инфраструктура:** порт Postgres 5434 занят чужим
    контейнером → прогоны на 5435; изначально сломанный `.venv`
    (3.11-launcher + cp312-пакеты) починен через `venv --upgrade` на
    bundled Python 3.12.14.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| **KI-036** | Low | deps, auth | passlib 1.7.4 несовместим с bcrypt>=4.1 → `bcrypt<4.1`; план — Argon2id |
| **KI-037** | Med | auth, security | Нет отзыва refresh/2FA/верификации email; токены в `localStorage`; logout локальный |
| **KI-038** | Med | finops, auth | Дневной бюджет токенов считается по IP, а не по JWT-пользователю |

**KI-035 → Closed** (изоляция данных реализована и покрыта 15 интеграционными
тестами + живым smoke).

---

## 7. Обоснование

- **Почему JWT (access+refresh), а не серверные сессии.** Фронтенд — SPA,
  API — отдельный процесс; stateless access-токен на 15 минут + refresh на
  7 дней — стандартная схема без Redis-сессий на каждый запрос. Refresh
  ротируется (новый `jti`), что готовит почву для denylist (KI-037).
- **Почему passlib+bcrypt и python-jose.** Прямое требование постановки
  итерации 24; bcrypt — зрелый выбор для паролей, passlib даёт
  `CryptContext`/миграции схем, python-jose — проверенную реализацию
  JWT (PyJWT тоже был в окружении, но постановка называла python-jose).
  Несовместимость passlib с bcrypt>=4.1 зафиксирована и запинена (KI-036).
- **Почему enum `admin_role` (user/admin/auditor).** DESIGN_ADMIN_AND_OPS
  §4.1; auditor — read-only аудитор, которому нужен доступ к данным, но не
  к мутациям. `pro`-тир не стал отдельной ролью: по постановке tier
  выводится из роли (admin/auditor → pro), а полноценные тарифы — T-031b.
- **Почему проверка владельца в API-слое, а не в сервисах.** Единая точка
  (`app/auth/access.py`), сервисы остаются переиспользуемыми (CLI, тесты,
  будущие админ-задачи); 404 вместо 403 не раскрывает существование чужих
  `games.id`.
- **Почему admin видит всё.** Требование «админка: доступ ко всем партиям»;
  admin-проверка инкапсулирована в `access.py`, а не размазана по роутерам.
- **Почему test-seam, а не массовый рефакторинг тестов.** 286 тестов
  писались до JWT; переписывание каждого вызова на регистрацию/логин
  раздуло бы итерацию и не добавило бы покрытия. Seam ограничен
  `APP_ENV=test`+флагом (в prod недостижим), а вся реальная авторизация и
  изоляция проверяются отдельными тестами (39+15) и живым smoke.
- **Почему `localStorage`, а не httpOnly cookie.** Постановка разрешала
  `localStorage`; cookie требует CSRF-токена/BFF-same-site настройки —
  вынесено в KI-037 вместе с планом перехода.
- **Почему миграция не делает backfill `games.user_id`.** Нет источника
  истины, к какому пользователю отнести старые партии; выбран безопасный
  вариант «legacy — только admin» (документировано).
- **Почему salvage, а не перенос локальной ветки.** Сравнение показало:
  remote-реализации `GET /v1/games/{id}`/ingest/embeddings проще и
  согласованы с текущими контрактами; перенесены только независимые
  улучшения (EmbeddingError/батчи, safe `--limit`, тесты parity/FEN/404,
  порядок мотивов), а не контракты (`pgn`, nullable level) и не миграции.
