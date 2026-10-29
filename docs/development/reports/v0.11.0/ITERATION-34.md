# Итерация 34 — Release Prep: Security, GHCR, EN Localization & Alerts (T-110, T-027, T-106, T-113)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. **T-110** (security review) закрыт: pip-audit
чист, python-jose заменён на PyJWT, добавлены prod-валидаторы секретов и
CORS. **T-027** (GHCR) — `publish-docker` job готов (запуск по тегу).
**T-106** (English localization) — английские промпты и `language` в
commentary. **T-113** (Grafana alerts) — 3 правила провижинятся и видны
в Grafana. Новый KI — KI-058 (English localization ограничена
комментариями).
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### T-110: Security review

| Файл | Что изменилось |
| :--- | :--- |
| `pyproject.toml` | `python-jose[cryptography]` → **`PyJWT>=2.8`**; удалён `types-python-jose` (PyJWT поставляет типы). Причина — PYSEC-2026-4298 (algorithm confusion) и PYSEC-2026-1325 (ecdsa timing), у обоих нет fix-версий |
| `app/auth/tokens.py` | `jwt.encode/decode` → PyJWT (`InvalidTokenError`); формат HS256-токенов не изменился, старые токены валидны |
| `tests/integration/test_admin.py` | `from jose import jwt` → `import jwt` (PyJWT) |
| `app/config.py` | `_validate_security_defaults`: в `APP_ENV=prod/production` падает на dev/коротком `JWT_SECRET` и dev-`ADMIN_API_KEY`; `CORS_ORIGINS=*` запрещён всегда (при `allow_credentials=True`) |
| `tests/unit/test_config_security.py` *(новый)* | 10 тестов: dev-дефолты, prod-плейсхолдеры, короткий ключ, dev admin-key, пустой admin-key, CORS wildcard/list, `DEFAULT_LANGUAGE` |
| `.env.example` | 40-символьный dev-`JWT_SECRET`, комментарии про prod-валидатор и CORS без `*` |
| `tests/unit/test_auth.py`, `tests/integration/{test_auth_api,test_data_isolation,test_usage_roles,test_token_budget_api}.py` | Удлинены тестовые `jwt_secret` (≥32 байт), чтобы PyJWT не сыпал `InsecureKeyLengthWarning` |
| `docs/development/GETTING_STARTED.md` | §3.4/§7.1 — prod-секреты и CORS; §7.5 — GHCR |

### T-027: GHCR

| Файл | Что изменилось |
| :--- | :--- |
| `.github/workflows/ci.yml` | Job `publish-docker`: `if: startsWith(github.ref, 'refs/tags/v')`, `needs: docker`, `permissions: packages: write`; `docker/login-action@v3` (ghcr.io, `GITHUB_TOKEN`), `metadata-action@v5` (теги `vX.Y.Z`/`X.Y.Z`/`latest`), `build-push-action@v6`, gha-cache |
| `Dockerfile` | Build-arg `VITE_API_BASE` (default `""` = same-origin SPA) перед `pnpm build` |
| `docs/development/GETTING_STARTED.md` | §7.5: тег `v*` → GHCR, `docker pull`, `VITE_API_BASE` variable |
| `README.md` | CI-строка: публикация в GHCR |

### T-106: English localization

| Файл | Что изменилось |
| :--- | :--- |
| `app/prompts/beginner_en.txt`, `club_en.txt`, `gm_en.txt` *(новые)* | Переводы правил трёх уровней: SAN/Markdown-ограничения, запрет выдуманных ходов, оценки, структура, few-shot для club; инструкция переводить русский факт-пакет, сохраняя SAN/оценки |
| `app/prompts/__init__.py` | `PROMPT_LANGUAGES={ru,en}`, `resolve_language` (request → Accept-Language → default), кэш `(language, level)`, `load_system_prompt(..., language=)`, `reset_prompt_cache(level, language)`; en читается из `*_en.txt` (БД пока только ru) |
| `app/config.py` | `default_language: Literal["ru","en"] = "ru"` |
| `app/commentary/service.py` | `generate_commentary(..., language=)`: выбор промпта, англоязычная retry-подсказка, `ADDITIONAL STYLE` для en |
| `app/commentary/streaming.py` | `stream_commentary(..., language=)`: тот же выбор промпта/style; `language` в SSE `meta` |
| `app/api/v1/commentary.py` | `CommentaryRequest.language` (`ru`/`en`), `_request_language` (поле → `Accept-Language` → `DEFAULT_LANGUAGE`) для sync и stream |
| `web/src/api.ts` | `generateCommentary(..., language?)`, `streamCommentary(..., language?)`; нормализация `en-US` → `en`, `StreamMeta.language` |
| `web/src/components/CommentaryPanel.tsx` | Передаёт `i18n.language` в `/v1/commentary/stream` |
| `web/src/api.auth.test.ts` | +2 теста: язык в SSE-теле и в JSON-запросе |
| `web/e2e/commentary.spec.ts` | Проверка, что UI отправляет `language:"ru"` |
| `tests/unit/test_prompts.py` | +6 тестов: en-файлы, ru≠en, неизвестный язык, приоритет `resolve_language` |
| `tests/integration/test_api_commentary.py` | +2 теста: сервис (en prompt + en retry) и API (`language=en` + `Accept-Language`) |

### T-113: Grafana alerts

| Файл | Что изменилось |
| :--- | :--- |
| `deploy/grafana/provisioning/alerting/rules.yaml` *(новый)* | 3 правила в папке `ChessAnnotator`: `HighErrorRate` (5xx >5% / 5m), `HighLatency` (p95 `/v1/commentary` >10 000 мс / 5m), `LLMBudgetExceeded` (`increase(llm_cost_usd_total[24h])` >5 USD) |
| `docker-compose.observability.yml` | Комментарий: alerting — Grafana provisioning, отдельный Alertmanager не нужен |
| `docs/development/GETTING_STARTED.md` | §6.2: где смотреть правила, API-проверка |
| `docs/development/DESIGN_ADMIN_AND_OPS.md` | §6.5 переписан под фактические правила |

### Документация

`docs/development/reports/v0.11.0/ITERATION-34.md` (этот файл),
`TODO.md` (T-046, T-027/T-106/T-110/T-113), `docs/CHANGELOG.md`,
`docs/development/RELEASES.md`, `docs/KNOWN_ISSUES.md` (+KI-058),
`docs/development/SPEC.md` (API `language`),
`docs/development/ARCHITECTURE.md` (PyJWT, language, alerts, security),
`docs/development/DESIGN_ADMIN_AND_OPS.md` (SLO/alerts, secrets),
`docs/development/GETTING_STARTED.md` (env, GHCR, alerts, prod secrets),
`docs/user_guide/USER_GUIDE.md` (EN commentary, curl),
`README.md` (779/55, итерация 34, PyJWT, GHCR, alerts),
`docs/README.md` (индекс v0.11.0).

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Security-валидаторы Settings | `tests/unit/test_config_security.py` | 10 |
| English prompts / language resolver | `tests/unit/test_prompts.py` | +6 |
| English commentary (service + API) | `tests/integration/test_api_commentary.py` | +2 |
| Frontend language в API | `web/src/api.auth.test.ts` | +2 |
| E2E language assertion | `web/e2e/commentary.spec.ts` | обновлён |
| Dependency audit | `pip-audit --local` | manual |
| Grafana rules | `rules.yaml` + API Grafana | manual |

Проверяемые сценарии:

- **Secrets:** `APP_ENV=prod` + `JWT_SECRET=dev_secret_change_me` /
  `change_me…` / короче 32 → `ValidationError`; dev-admin-key → ошибка;
  пустой `ADMIN_API_KEY` допустим; dev/test с дефолтами запускаются.
- **CORS:** `CORS_ORIGINS=*` → `ValidationError`; явный список — ок.
- **English prompts:** для всех уровней `load_system_prompt(level,
  language="en")` содержит SAN/Markdown-правила; ru≠en; неизвестный
  язык → `ValueError`; `resolve_language` уважает приоритет
  request → header → default.
- **English commentary:** `language=en` → английский system prompt и
  английская retry-подсказка; API принимает и поле, и `Accept-Language`,
  возвращает английский текст и сохраняет комментарий.
- **Frontend:** UI отправляет нормализованный `language` в SSE и JSON.
- **Dependency audit:** после миграции — `No known vulnerabilities found`.
- **Grafana:** 3 правила в папке `ChessAnnotator`, health `ok`.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend).
**Tests:** **779 passed** (backend; 781 collected, 1 skipped, 1 xfailed),
**55/55** frontend, **28/28** E2E mocked.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| pip-audit (до) | `pip-audit --local` (2.10.1, временная установка в `%TEMP%`) | 3 уязвимости в 2 пакетах: `python-jose 3.5.0` PYSEC-2026-4298, `ecdsa 0.19.2` PYSEC-2026-1325 |
| pip-audit (после) | `pip-audit --local` | **No known vulnerabilities found** (exit 0; проект `chess-annotator` не на PyPI — skip) |
| Backend-сюита | `pytest -q` (POSTGRES_DB=`chessannotator_iter33_final`, Redis 6381, Stockfish) | **779 passed, 1 skipped, 1 xfailed in 268.32 s** (exit 0); collected 781; **+18** к итерации 33 (761) |
| Security-валидаторы | `pytest tests/unit/test_config_security.py -q` | **10 passed** |
| English prompts/API | `pytest tests/unit/test_prompts.py tests/integration/test_api_commentary.py -q` | **passed** (6 + 2 новых) |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 84 source files** (exit 0) |
| Alembic | `upgrade head`, `alembic check` | head `dcf1da1421cd`; схема не менялась, «No new upgrade operations detected» |
| Frontend tests | `pnpm --dir web test` | **55 passed** (8 файлов; +2) |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 5.77 с; JS 480.98 КБ (gzip 149.86), CSS 15.70 КБ (gzip 4.09) |
| E2E mocked | `E2E_PREVIEW=1 pnpm --dir web run test:e2e` | **28 passed** (14 сценариев × chromium/mobile), 28.4 с |
| Grafana rules | `GET /api/v1/provisioning/alert-rules` | **3** правила: `HighErrorRate`, `HighLatency`, `LLMBudgetExceeded`; folderUID `chessannotator`; health `ok` |
| CI YAML | `python -c "yaml.safe_load(...)"` | 5 jobs, `publish-docker.if = startsWith(github.ref, 'refs/tags/v')` |
| Compose config | `docker compose -f docker-compose.observability.yml config` | exit 0 |
| Docker GHCR build | `docker build -t ghcr.io/iilmchat/ai-chess-annotator:v0.11.0 .` | см. §5: Docker Hub auth timeout в этой среде; workflow/Dockerfile проверены статически |

---

## 4. Smoke (ручной сценарий)

**4.1. pip-audit.**

```text
# до: python-jose 3.5.0 PYSEC-2026-4298, ecdsa 0.19.2 PYSEC-2026-1325
# после миграции на PyJWT:
No known vulnerabilities found
Name            Skip Reason
chess-annotator Dependency not found on PyPI and could not be audited
```

**4.2. Prod-валидатор (`APP_ENV=prod`, dev-secret).**

```text
ValidationError: 1 validation error for Settings
  Value error, JWT_SECRET в production должен быть случайной строкой
  длиной ≥ 32 символов ... dev-плейсхолдеры запрещены
```

**4.3. Grafana (локальный стек).**

```text
GET /api/v1/provisioning/alert-rules
uid                     title             folderUID      for condition
high-error-rate         HighErrorRate     chessannotator 5m  B
high-latency-commentary HighLatency       chessannotator 5m  B
llm-budget-exceeded     LLMBudgetExceeded chessannotator 10m B
```

Правила также видны в Grafana → Alerting → Alert rules → папка
`ChessAnnotator`; health — `ok`, state — `inactive` (нет данных/порог не
превышен).

**4.4. English commentary.**

```text
pytest tests/integration/test_api_commentary.py::test_commentary_language_en_via_api
→ 200, text == "The game began with d4, followed by Nf6."
→ system_prompt содержит "Write in English"
```

**4.5. GHCR workflow.** YAML валиден; job `publish-docker` присутствует,
`if` = `startsWith(github.ref, 'refs/tags/v')`, образ
`ghcr.io/${{ github.repository }}` с тегами `vX.Y.Z`, `X.Y.Z`,
`latest`. Фактический push выполняется при `git push origin vX.Y.Z`.

---

## 5. Что не так

1. **Фактический тег/GHCR push не выполнялся.** Workflow готов и
   валидирован, но в сессии не создавался `v*`-тег и нет registry-доступа
   для проверки публикации. Это действие мейнтейнера при релизе.
2. **Docker build в этой среде упал на auth Docker Hub** (`TLS handshake
   timeout` при получении oauth token, несколько попыток; legacy builder —
   та же ошибка). Dockerfile/CI проверены статически; предыдущий образ
   итерации 33 собирался успешно. Это сетевая проблема, не код.
3. **Alert rules без уведомлений.** Grafana unified alerting показывает
   и оценивает правила, но contact point (Telegram/Slack/email) не
   настроен — DoD допускал UI-only. Alertmanager не добавлялся.
4. **English только для комментариев (KI-058).** Чат, PDF-подписи,
   ошибки API и часть UI остаются русскими; английские промпты —
   файлы, не редактируются в админке; факт-пакет русский, промпт просит
   перевести подписи, сохраняя SAN/оценки.
5. **Порог `HighLatency` 10 с будет срабатывать на локальной LLM.**
   Локальная генерация занимает ~15–120 с; правило соответствует
   постановке (p95 >10 с), но в dev-стенде это ожидаемый «шум». Для
   production с локальной моделью порог стоит поднять.
6. **Порог `LLMBudgetExceeded` — 5 USD/сутки, захардкожен в YAML.**
   File-provisioning Grafana не подставляет env; менять порог нужно в
   `rules.yaml` (или перейти на API/Ansible).
7. **`HighErrorRate` считает все `/v1/*`**, а не только commentary;
   это соответствует «5xx > 5%», но шумят и analyze/chat.
8. **Prod-валидатор ломает старые установки с `.env.example`.**
   `JWT_SECRET=dev_secret…` и `ADMIN_API_KEY=dev-admin-token` теперь
   отвергаются в `APP_ENV=prod`; это осознанно (fail-fast), но требует
   обновить секреты перед деплоем.
9. **CORS `*` запрещён даже для публичного API без cookie.** Приложение
   всегда ставит `allow_credentials=True`; чтобы разрешить wildcard,
   нужен отдельный конфиг-флаг и отключение credentials — не в DoD.
10. **`pip-audit` покрывает Python-пакеты, не npm/системные библиотеки.**
    `pnpm audit` и OSV для фронтенда/Ubuntu-пакетов — отдельный шаг.
11. **PyJWT warning для коротких ключей остаётся возможным.** Дефолт
    поднят до 40 символов, тестовые секреты удлинены; пользователь с
    собственным коротким `JWT_SECRET` в dev увидит warning, в prod —
    ошибку старта.
12. **`prompt_slug` в `llm_calls` не содержит язык.** En/ru-вызовы
    различимы по `prompt_hash`/system prompt, но админ-дашборд не
    группирует их по языку; отдельная колонка/атрибут — при
    необходимости.
13. **English prompt не переведён носителем.** Перевод сделан в рамках
    задачи; стилистическая вычитка и few-shot-калибровка — KI-058.
14. **E2E real-backend по-прежнему упирается в email-верификацию**
    (итерация 33); mocked-режим — основной для CI.
15. **Docker build-arg `VITE_API_BASE` не проверен в образе** из-за
    сетевого сбоя; локально build с пустым значением эквивалентен
    previous same-origin сборке.
16. **GHCR-образ по умолчанию private.** После первого push нужно
    вручную сделать package public (или использовать `GITHUB_TOKEN` с
    доступом) — настройка репозитория, не кода.
17. **`cryptography` мог остаться в venv** после удаления
    `python-jose[cryptography]`; в clean-install он больше не нужен.
    `pip check` чист.
18. **Язык комментария не сохраняется в `commentaries`.** Запрос/промпт
    знают `language`, но строка БД — нет; экспорт датасета не отличит
    en-комментарии от ru. Колонка `language` — часть KI-058.
19. **KI-030 не закрыт.** LoRA на GPU по-прежнему не обучалась.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| KI-058 | Low | llm, web, docs | Английская локализация покрывает только комментарии: en-промпты файлами (не в админке), факт-пакет русский, чат/PDF/ошибки — ru |

Других новых KI нет. Сводка реестра: **58** записей, 26 Open, 32 Closed.

---

## 7. Обоснование

- **Почему PyJWT, а не ignore-vuln.** У python-jose 3.5.0 открыт
  algorithm-confusion (PYSEC-2026-4298), а его транзитивный python-ecdsa
  не имеет исправленной версии (PYSEC-2026-1325). Нам нужен только
  HS256; PyJWT — зрелая реализация без ecdsa. Формат токенов
  стандартный, поэтому уже выданные HS256-токены остаются валидными.
- **Почему валидаторы в `Settings`, а не в lifespan.** Ошибка конфигурации
  должна валить процесс до открытия порта и до фоновых задач, а не
  после старта. Pydantic-валидатор даёт одинаковое поведение для
  uvicorn, arq-воркера и тестов; сообщение содержит подсказку
  `openssl rand -hex 32`.
- **Почему `*` в CORS запрещён безусловно.** `CORSMiddleware` в
  `app/main.py` всегда ставит `allow_credentials=True`; сочетание с
  wildcard либо отвергается браузером, либо (в старых реализациях)
  открывает API с cookie любому сайту. Явный список — единственный
  безопасный вариант без нового флага.
- **Почему английские промпты файлами, а не колонкой в БД.** Это
  минимальный путь без миграции: `*_en.txt` упаковываются в wheel,
  читаются через `importlib.resources`, кэшируются по `(language,
  level)`. Ограничение (нельзя редактировать в админке) зафиксировано
  KI-058; переход на `prompts.language` — отдельная миграция.
- **Почему `language` имеет приоритет над `Accept-Language`.** Явный
  параметр запроса — намерение клиента; заголовок — fallback для curl/
  браузера. Неизвестный язык не роняет запрос, а откатывается к
  `DEFAULT_LANGUAGE`, чтобы не ломать клиентов с `de-DE`.
- **Почему факт-пакет не переводится на английский.** Пакет —
  проверенные факты из БД/движка; машинный перевод мог бы исказить
  оценки/мотивы. Промпт требует перевести подписи, сохранив SAN и
  числа; качество — KI-058.
- **Почему frontend передаёт `i18n.language`.** Пользователь уже выбрал
  язык интерфейса; отдельный селектор языка комментария был бы лишним
  UI. Нормализация `en-US` → `en` оставляет контракт API строгим
  (`ru`/`en`).
- **Почему Grafana unified alerting, а не Alertmanager.** Для DoD нужны
  видимые правила в UI; Alertmanager добавил бы ещё один сервис,
  конфиг и маршрутизацию уведомлений. Правила провижинятся файлом,
  используют существующий datasource `prometheus` и метрики
  `traces_span_metrics_*`/`llm_cost_usd_total`.
- **Почему PromQL-условие обёрнуто в threshold-узел (`__expr__`).**
  Так правило явно задаёт `gt` и порог, а не полагается на неявное
  «любое ненулевое значение»; формат совместим с Grafana 11.3
  provisioning.
- **Почему `for: 5m`/`10m`.** 5 минут сглаживает одиночные всплески
  error/latency; для суточной стоимости 10 минут уменьшает дребезг.
- **Почему GHCR job только по тегу.** Публикация `:latest` на каждый
  push засоряла бы registry и ломала воспроизводимость; тег `v*` —
  явный релиз. `needs: docker` гарантирует, что smoke-сборка прошла.
- **Почему `docker/metadata-action`.** Он даёт корректные `vX.Y.Z`/
  `X.Y.Z`/`latest` и OCI-labels без ручных `if`; `build-push-action`
  использует gha-cache и buildx.
- **Почему `VITE_API_BASE` build-arg.** SPA и API обычно на одном
  origin (пустое значение), но split-origin deployment возможен;
  build-arg не бейкает секреты, только публичный URL.
- **Почему тестовые JWT-секреты удлинены.** PyJWT предупреждает о
  ключах <32 байт; это не ошибка, но шум в CI. Тестовые значения не
  влияют на production-валидатор (он проверяет `APP_ENV=prod`).
