# Итерация 39 — GPU Transfer Prep, PWA & User Profile (T-080, T-081, T-082)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. Версия — **1.2.0** (продолжение полировки v1.2.0,
без бампа).
**Формат:** RULES §2.18 (7 разделов).

Три направления: подготовка репозитория к переносу LoRA-обучения на
машину с RTX 3060 (T-080), установка приложения на мобильные/десктоп как
PWA (T-081), профиль пользователя и реальная отправка писем через SMTP
(T-082).

---

## 1. Файлы (+/-)

### Новые файлы (10)

| Файл | Назначение |
| :--- | :--- |
| `alembic/versions/a9f1f28d4c45_add_users_display_name.py` | Миграция: `users.display_name` (String(120), nullable) + downgrade |
| `scripts/generate_lora_dataset_from_db.py` | Датасет schema v2 из БД: `--limit` (500), `--level`, `--min-words`, `--seed`, `--dry-run`, `--output`; переиспользует хелперы `scripts/export_dataset.py`; сводка экспорт/пропуски, exit 2 на пустых данных |
| `docs/development/GPU_TRAINING_GUIDE.md` | Пошаговый перенос обучения: venv, PyTorch CUDA 12.1, БД `POSTGRES_PORT=5434`, генерация датасета, `train_lora.py`, VRAM-заметки для 12 ГБ, перенос адаптера архивом/Git LFS, проверка, troubleshooting |
| `tests/unit/test_generate_lora_dataset.py` | Тесты скрипта: формат v2, фильтр по уровню, лимит, skip-причины, детерминизм по seed, `--dry-run`, exit 2 |
| `tests/integration/test_auth_profile.py` | Тесты `PATCH /auth/me` (уровень, имя с trim, 422 на пустое/неизвестное, 401) и `POST /auth/change-password` (успех + логин новым паролем, неверный текущий → 401, короткий/совпадающий → 422) |
| `web/src/pages/ProfilePage.tsx` | Страница `/profile`: имя, уровень, смена пароля с клиентской валидацией (8+, совпадение), success/error-состояния |
| `web/e2e/profile.spec.ts` | 6 E2E-кейсов: открытие из меню, смена уровня (`PATCH` body), смена имени, несовпадение/короткий пароль без запроса, успешная смена, неверный текущий пароль |
| `web/e2e/pwa.spec.ts` | E2E: `link[rel=manifest]`, JSON манифеста (`name=ChessAnnotator`, иконки 200), `theme-color`, регистрация service worker в preview-сборке |
| `web/public/icons/{icon-192,icon-512,icon-maskable-512,apple-touch-icon}.png` | Иконки PWA (192/512/maskable/180), сгенерированы Pillow |
| `docs/development/reports/v1.2.0/ITERATION-39.md` | Этот отчёт |

### Изменённые файлы (22)

| Файл | Изменение |
| :--- | :--- |
| `.gitignore` | `models/`, `*.safetensors`, `*.pt`, `data/lora_*.jsonl`, `data/*.jsonl` (при сохранённом `!data/.gitkeep`) |
| `.env.example` | Блок `SMTP_*` (host/port/user/password/from/starttls/ssl/timeout) + комментарии; `EMAIL_SENDER=mock` по умолчанию |
| `app/db/models.py` | `User.display_name: Mapped[str \| None]` (String(120), nullable) |
| `app/api/v1/auth.py` | `UserProfile` (payload с `display_name`), `PATCH /auth/me`, `POST /auth/change-password`; схемы `ProfileUpdateRequest`, `ChangePasswordRequest` |
| `app/config.py` | `email_sender: Literal["mock","smtp"]` (нормализация регистра), `smtp_host/port/user/password/from/starttls/ssl/timeout` |
| `app/email/sender.py` | `SMTPEmailSender` (aiosmtplib, MIMEText UTF-8, STARTTLS/SSL, таймаут, логи без ссылки/токена), выбор sender по `EMAIL_SENDER`, ошибки конфигурации → `EmailDeliveryError` |
| `pyproject.toml` | Зависимость `aiosmtplib` |
| `tests/unit/test_email_sender.py` | Расширены: SMTP-аргументы через мок `aiosmtplib.send`, MIME-заголовки/UTF-8, ошибки отправки, выбор sender, mock-outbox |
| `tests/integration/test_auth_api.py` | `display_name` в payload `/auth/me` |
| `web/package.json`, `web/pnpm-lock.yaml` | `vite-plugin-pwa` (devDependency) |
| `web/vite.config.ts` | `VitePWA`: манифест (name/short_name/standalone/theme/background/icons), workbox: precache статики, `navigateFallback`, denylist `/v1/`, `/auth/`, `/admin`, `/health`; runtimeCaching для API намеренно отсутствует |
| `web/index.html` | `theme-color`, `description`, iOS-мета-теги (`apple-mobile-web-app-capable`, `-status-bar-style`, `-title`), `apple-touch-icon` |
| `web/src/api.ts` | `updateProfile` (PATCH `/auth/me`), `changePassword` (POST, пустое тело); `/auth/change-password` исключён из refresh-повторов; `request` переносит пустой 200 |
| `web/src/types.ts` | `AuthUser.display_name?: string \| null` |
| `web/src/App.tsx` | Маршрут `/profile` под `AuthGate` |
| `web/src/components/ProfileMenu.tsx` | Пункт «Профиль» → `/profile` |
| `web/src/i18n.ts` | Пространство `profile.*` (RU/EN): заголовки, имя, уровень, пароль (текущий/новый/повтор, ошибки, успех) |
| `README.md` | Версия v1.2.0 в статусе, PWA/профиль/SMTP/GPU-перенос в возможностях, счётчики (1000 / 129 / 60, 30 сценариев) |
| `docs/CHANGELOG.md` | `[1.2.0]` дополнен пунктами итерации 39 |
| `TODO.md` | `[x] T-080`, `[x] T-081`, `[x] T-082` |
| `docs/development/ARCHITECTURE.md`, `docs/development/GETTING_STARTED.md` | Счётчики тестов/E2E (1000, 129, 60/30) |
| `docs/development/RELEASES.md` | Запись итерации 39 |

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| Профиль (backend) | `tests/integration/test_auth_profile.py` | `PATCH /auth/me`: смена уровня, имени (trim), обоих; 422 на пустое имя/неизвестный уровень/лишние поля; 401 без токена; `GET /auth/me` отражает изменения. `POST /auth/change-password`: успех (старый пароль перестаёт работать, новый логинит), неверный текущий → 401 `invalid_credentials`, короткий новый → 422, совпадающий с текущим → 422, 401 без токена |
| SMTP (unit) | `tests/unit/test_email_sender.py` | `SMTPEmailSender` передаёт в `aiosmtplib.send` корректные `hostname/port/start_tls/use_tls/timeout` и `username/password` только при заданном пользователе; MIME From/To/Subject + UTF-8 тело; ошибка транспорта → `EmailDeliveryError`; `create_email_sender`: mock → MockEmailSender, smtp + креды → SMTP, smtp без host/from → ошибка, неизвестный backend → ошибка; mock-outbox не изменился |
| Скрипт датасета | `tests/unit/test_generate_lora_dataset.py` | Формат записи v2, фильтр по уровню, лимит, счётчики пропусков, детерминизм `--seed`, `--dry-run` не пишет файл, exit code 2 на пустых данных, JSONL построчно парсится |
| PWA (E2E) | `web/e2e/pwa.spec.ts` | Манифест доступен и валиден (`name`, иконки → 200), `theme-color`, регистрация service worker в собранном preview |
| Профиль (E2E) | `web/e2e/profile.spec.ts` | Открытие из меню; `PATCH /auth/me` body `{level}` и `{display_name}`; несовпадение/короткий пароль → ошибка и НЕТ запроса; успешная смена → `{current_password,new_password}`; 401 на неверный текущий → понятное сообщение |
| Регрессия | существующие 12 unit-файлов frontend + backend-сюита | без регрессий |

Новых тестов: **+51 backend** (профиль, SMTP, датасет, расширения),
**+0 frontend unit-файлов**, **+7 E2E-сценариев** (`pwa` 1, `profile` 6).

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита | `pytest -q` (БД `chessannotator_iter37`, PostgreSQL 5435, Redis 6382/0, Stockfish 17) | **1000 passed, 3 skipped, 1 xfailed in 295.90 s** (exit 0), 83 файла. Skip: 2 cairo-кейса + `DEEPSEEK_API_KEY`; xfail — KI-012 |
| Линтер / типы | `ruff check .`, `mypy app` | All checks passed; no issues in **86** файлах |
| Зависимости | `pip check` | No broken requirements (aiosmtplib 5.1.3) |
| Миграции | `alembic upgrade head`, `alembic check` | Ревизия `a9f1f28d4c45` применена; «No new upgrade operations detected» |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0, без `any` |
| Frontend-тесты | `pnpm --dir web test` | **129 passed** (12 файлов) |
| Frontend build | `pnpm --dir web build` | **8.07 с**: `index.js` 365.55 kB / gzip **113.64 kB** (лимит 200), async workspace 497.60 / 143.95, CSS 4.81; **PWA v2.0.0** (`generateSW`): `dist/sw.js` + `workbox-9c191d2f.js`, precache **15 записей (937.66 KiB)** |
| E2E mocked | `E2E_PREVIEW=1 pnpm --dir web run test:e2e` | **60 passed** (30 сценариев × chromium/mobile), 36.6 с, exit 0 |

Динамика к итерации 38: backend 949 → **1000** (+51), frontend 129
(без изменений), E2E 46 → **60** (+14 прогонов, +7 сценариев).

---

## 4. Smoke

**GPU-перенос (T-080).** `python scripts/generate_lora_dataset_from_db.py --dry-run`
печатает сводку (экспортировано/пропущено/лимит) и не пишет файл; при
пустой БД — exit 2 с понятным сообщением. `.gitignore` проверен
`git check-ignore`: `models/lora-club-v1/adapter_model.safetensors` и
`data/lora_club_v1.jsonl` игнорируются, `data/.gitkeep` — нет. Гайд
содержит реальные флаги `train_lora.py` и команды для RTX 3060.

**PWA (T-081).** Собранный `dist` содержит `manifest.webmanifest`, `sw.js`,
`workbox-*.js`; в Chromium: установка приложения доступна (манифест с
`display: standalone`), после установки иконка и «ChessAnnotator» в
заголовке; `theme-color` и iOS-мета-теги на месте; API-запросы
(`/v1/*`, `/auth/*`) не кэшируются — исключены из fallback, `runtimeCaching`
для них отсутствует.

**Профиль и SMTP (T-082).** `/profile` из меню «Профиль»: меняем уровень
на `gm` → `PATCH /auth/me` `{level:"gm"}` → «Профиль сохранён»; меняем имя
→ `{display_name:"…"}`; пароль: при несовпадении/коротком — inline-ошибка
и запроса нет; при успехе → `POST /auth/change-password`
`{current_password,new_password}` → «Пароль изменён»; неверный текущий →
«Текущий пароль неверен». SMTP: при `EMAIL_SENDER=smtp` и пустых
`SMTP_HOST/FROM` приложение падает на старте с понятной ошибкой; при
`mock` письма по-прежнему видны в structlog и `outbox`.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| LoRA-обучение не выполнено | Нужна машина с RTX 3060; подготовлены скрипт, гайд, защита весов. KI-030 остаётся Open |
| Реальный SMTP не проверен вживую | Нет провайдера/кредов в этой среде; `SMTPEmailSender` покрыт unit-тестами с моком `aiosmtplib` |
| Установка PWA на iOS/Android вручную не проверена | Нет устройств; мета-теги и apple-touch-icon добавлены, манифест/иконки/SW проверены в Chromium (E2E) |
| Офлайн-режим — только статика | SW не кэширует данные (анализ/чат/история); офлайн-данные осознанно не поддерживаются |
| Смена пароля не отзывает сессии | `revoked_before`/`iat_ms` используется в reset-password; для change-password не подключали, чтобы не менять контракт (в отчёте отмечено) |
| Датасет из БД: качество зависит от данных | Скрипт берёт партии с комментариями; на dev-БД их может быть мало (dry-run покажет) |
| `data/*.jsonl` в `.gitignore` | Экспортированные датасеты не коммитятся — перенос на GPU-машину делается копированием/пересборкой из БД |

---

## 6. Новые KI

**Нет.** Ограничения — осознанные границы объёма и внешние зависимости
(GPU, SMTP-провайдер, iOS-устройство); KI-030 (качество LLM/LoRA) и
KI-056 (SMTP/2FA в списке ограничений auth) остаются открытыми, но
SMTP-часть KI-056 теперь реализована — запись KI-056 не переоткрывалась,
т.к. включает и другие пункты (2FA/SSO/лимит попыток).

---

## 7. Обоснование

- **Почему перенос на GPU оформлен скриптом + гайдом, а не CI.** Обучение
  LoRA требует CUDA-машины и гигабайтов весов; в репозитории появляется
  воспроизводимый путь: датасет из БД (`generate_lora_dataset_from_db.py`
  переиспользует формат `export_dataset.py` schema v2, чтобы `train_lora.py`
  не менялся), инструкция с реальными флагами, и `.gitignore`, который
  физически не даёт закоммитить веса (`models/`, `*.safetensors`) —
  главный риск при переносе.
- **Почему датасет из БД, а не из файлов.** На GPU-машине будет только
  код: БД разворачивается из compose + миграций, а датасет собирается тем
  же скриптом из сохранённых комментариев/правок — не нужно тащить
  `data/` (который и так в `.gitignore`).
- **Почему PWA на `vite-plugin-pwa`.** Штатный для Vite плагин, генерирует
  манифест и SW в `dist` без ручного service worker-кода; workbox
  настраивается декларативно. Критично — не кэшировать API: `navigateFallbackDenylist`
  для `/v1/`, `/auth/`, `/admin`, `/health` и отсутствие `runtimeCaching`
  гарантируют, что SW обслуживает только статику (иначе пользователь
  увидел бы устаревшие данные и чужие сессии).
- **Почему `display_name` отдельной миграцией.** Отображаемое имя — новое
  поле, а не переиспользование email; nullable String(120) не ломает
  существующих пользователей и не требует backfill. Миграция — одна
  логическая операция (RULES §3.1), проверена `upgrade` + `check`.
- **Почему `SMTPEmailSender` через `aiosmtplib` и Protocol.** Существующий
  `EmailSender`-Protocol уже абстрагирует отправку; SMTP-реализация просто
  добавляется рядом с mock, выбор — по `EMAIL_SENDER`. Ошибки
  конфигурации (smtp без host/from) падают громко, а не «тихо» теряют
  письма; в логи не попадают ссылка/токен (секрет), в отличие от mock-режима,
  где ссылка нужна для dev.
- **Почему профиль — `PATCH /auth/me` + отдельный change-password.**
  Частичное обновление профиля семантически соответствует PATCH; смена
  пароля — отдельный эндпоинт с проверкой текущего (нельзя смешивать с
  обновлением имени, у них разные требования к валидации и кодам ошибок).
  Фронтендское исключение `/auth/change-password` из refresh-повторов
  важно: 401 там означает неверный текущий пароль, а не истёкший токен.
