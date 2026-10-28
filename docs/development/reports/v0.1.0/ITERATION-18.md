# Итерация 18 — Prompt Injection Guardrails и Security Audit (T-033, T-035)

**Дата:** 2026-10-29
**Статус:** ✅ T-033/T-035 в объёме итерации; KI-031/KI-032 закрыты.

## 1. Файлы (+/-)

### Созданы

- `app/security/__init__.py`, `app/security/guardrails.py` — нормализация
  Unicode, лимит 2000, 7 RU/EN-правил, `inspect_user_input` и
  `sanitize_user_input`.
- `app/audit.py` — `append_audit`, hash-chain, advisory-lock,
  `verify_audit_chain`, `record_injection_attempt`.
- `app/prompts/pubsub.py` — канал `prompts_updates`, publish/watcher.
- `alembic/versions/f4a5b6c7d8e9_add_audit_logs.py` — `audit_logs`,
  индексы, trigger `UPDATE/DELETE/TRUNCATE → append-only`.
- `app/templates/audit_verify.html` — страница проверки целостности.
- `tests/unit/test_guardrails.py` (7), `tests/unit/test_prompt_pubsub.py`
  (6), `tests/integration/test_audit.py` (5).
- `docs/development/reports/v0.1.0/ITERATION-18.md`,
  `ITERATION-18_prompt_audit_smoke.log`,
  `ITERATION-18_injection_smoke.log`,
  `ITERATION-18_audit_admin_smoke.log`.

### Изменены

- `app/db/models.py` — модель `AuditLog`.
- `app/admin.py` — аудит и Pub/Sub в `PromptAdmin`, read-only
  `AuditLogAdmin`, `AuditVerifyView`, `templates_dir`.
- `app/main.py` — watcher Pub/Sub в lifespan, `pro_ips`/
  `trust_forwarded_for` в `TokenBudgetMiddleware`.
- `app/config.py` — `prompt_pubsub_enabled`, `pro_ips`,
  `trust_forwarded_for`.
- `app/finops.py` — тир только по IP из `PRO_IPS`; `X-User-Tier` и
  `X-User-Id` больше не читаются; XFF — только при доверии прокси.
- `app/chat/service.py`, `app/chat/streaming.py` — sanitize + запрет
  раскрывать системные инструкции в `CHAT_SYSTEM_PROMPT`.
- `app/api/v1/chat.py` — 422 `prompt_injection`, аудит, sanitized input.
- `app/commentary/service.py`, `app/api/v1/commentary.py` — аудит
  `PATCH /v1/commentaries/{id}` в той же транзакции.
- `app/errors.py` — `PromptInjectionError` (422).
- `tests/conftest.py` — `PROMPT_PUBSUB_ENABLED=false` для TestClient.
- `tests/test_models.py`, `test_token_budget.py`, `test_admin.py`,
  `test_api_chat.py`, `test_api_commentaries_patch.py`.
- `docs/KNOWN_ISSUES.md`, `TODO.md`, `SPEC.md`, `ARCHITECTURE.md`,
  `DESIGN_ADMIN_AND_OPS.md`, `CHANGELOG.md`, `README.md`, `RELEASES.md`.

## 2. Покрытие

| Область | Тесты | Что проверяют |
| :--- | :--- | :--- |
| Guardrails | `tests/unit/test_guardrails.py` (7) | RU/EN-инъекции, role/jailbreak, sanitize, control/zero-width, лимит 2000 |
| Audit | `tests/integration/test_audit.py` (5) | детерминированный hash, валидная цепочка, битая строка, `UPDATE`/`TRUNCATE` отклоняются |
| Prompt Pub/Sub | `tests/unit/test_prompt_pubsub.py` (6) | publish, ошибка Redis, `handle` одного/всех, watcher, общий кэш |
| Token budget | `test_token_budget.py` (+1) | `X-User-Tier` игнорируется; `pro` — только IP из whitelist |
| Chat | `test_api_chat.py` (+1) | 422 `prompt_injection`, аудит без сырого текста |
| Admin | `test_admin.py` (+1) | read-only `audit-log/list`, страница проверки с `OK:` |
| Схема | `test_models.py` (+1) | `audit_logs` в метаданных, NOT NULL хешей |

Итого: **+22 теста** к 235 → **257 passed, 1 xfailed**.

## 3. Результаты

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Lint | `ruff check .` | **0** ошибок |
| Types | `mypy app` | **0** ошибок, 56 файлов |
| Tests | `pytest -q` | **257 passed, 1 xfailed** (177.23 с) |
| Build | backend | **0/0** (ruff/mypy); фронтенд не менялся |
| Migration | `alembic upgrade head` | `e3f4a5b6c7d8 → f4a5b6c7d8e9` |
| Drift | `alembic check` | «No new upgrade operations detected» |

Таблиц в БД: **10** (добавлена `audit_logs`). Head: `f4a5b6c7d8e9`.

## 4. Smoke

### Инъекция, аудит и защита от подмены

Фактический вывод — `docs/development/reports/v0.1.0/ITERATION-18_injection_smoke.log`:

```text
POST /v1/chat [injection] -> 422 {'code': 'prompt_injection'}
GET /admin/audit/verify -> 200, OK=True
latest audit: action=prompt_injection.blocked, actor=anonymous:testclient
details keys=['message_len', 'message_sha256', 'rule_ids', 'surface'],
  has_raw_text=False
chain before tamper: ok=True, checked=10
UPDATE blocked: audit_logs is append-only
chain after tamper attempt: ok=True, checked=10, actor_unchanged=True
```

### Правка промпта: аудит + Pub/Sub

Фактический вывод — `docs/development/reports/v0.1.0/ITERATION-18_prompt_audit_smoke.log`:

```text
prompt_updates_subscribed channel=prompts_updates
POST /admin/prompt/edit?pk=2 -> 303
GET /admin/audit/verify -> 200, OK=True
after DB: version=2 (+1), marker=True
cache after pub/sub: marker=True
audit: action=prompt.update, actor=admin:admin
audit details keys=['level', 'new_content_sha256', 'new_len',
  'new_version', 'old_content_sha256', 'old_version']
chain: ok=True, checked=5, errors=0
```

Проверка хешей доступна в админке: пункт меню «Проверить
целостность» → страница с `OK: проверено N записей` либо списком
расхождений. Сырые тексты в аудит не пишутся — только sha256/длины.

Список аудита в админке — `docs/development/reports/v0.1.0/ITERATION-18_audit_admin_smoke.log`:

```text
GET /admin/audit-log/list -> 200
has prompt.update: True
has prompt_injection.blocked: True
GET /admin/audit/verify -> 200, OK=True
```

## 5. Что не так

- **T-033 не полный.** Нет LLM-классификатора, обёртки
  `<user_question>…</user_question>`, защиты поля `style` в
  комментарии, выходного фильтра на утечку системного промпта,
  таблицы `injection_events`, корпуса ≥20 атак и метрик
  detection/FP. Regex — дешёвый первый барьер, возможны ложные
  срабатывания («act as» в легальном вопросе) и обходы
  перефразированием (`T-033b`).
- **T-035 не полный.** Нет отдельных `actor_ip`/`user_agent`/
  `request_id`/`trace_id`, аудита login/logout, бюджетов, экспорта и
  CRUD RAG, retention/SIEM, роли `auditor` и фильтров в UI (`T-035b`).
- **Атомарность аудита промпта.** Правка промпта коммитится до
  `after_edit_committed`; аудит пишется отдельной транзакцией
  best-effort. При сбое аудита правка не откатывается (`KI-033`).
  Для `PATCH` комментария аудит атомарен (та же транзакция).
- **Приватность аудита.** В `details` нет сырого текста (только
  sha256/длины), поэтому по журналу нельзя увидеть сам diff — только
  факт и хеши. Это осознанный размен; для разбора нужен доступ к
  `edits`/`prompts`.
- **Тир `pro`.** Пока это статический `PRO_IPS`, а не JWT; клиент за
  доверенным прокси с `TRUST_FORWARDED_FOR=true` определяется по XFF.
  JWT/роли — T-028/T-031b.
- **Pub/Sub без TTL-fallback.** Потерянное сообщение Redis оставит
  другой воркер со старым кэшем до рестарта; TTL 60 с — T-029b.
- **Аудит и суперпользователь.** Триггер запрещает UPDATE/DELETE/
  TRUNCATE, но владелец БД может `DROP TABLE`; hash-chain не спасает
  от удаления всей таблицы, только фиксирует разрыв при частичной
  порче.
- **CI.** GitHub Actions не запускался (нет push); локально `ruff`,
  `mypy`, `pytest`, `alembic check` зелёные.

## 6. Новые KI

- **KI-031** — Closed: тир больше не берётся из заголовка; `pro` только
  по IP из `PRO_IPS`.
- **KI-032** — Closed: Redis Pub/Sub `prompts_updates` + watcher.
- **KI-033** — Open: аудит правки промпта пишется после commit
  best-effort; сбой аудита не откатывает правку.

## 7. Обоснование

- **Схема хеширования.** `sha256(prev_hash + timestamp + actor + action
  + target_id + canonical_json(details))` — простая, детерминированная
  и проверяемая без внешних ключей. Canonical JSON (sorted keys, без
  пробелов) исключает расхождение из-за порядка полей; timestamp
  фиксируется в Python до вставки, чтобы хеш не зависел от `now()` БД.
  `pg_advisory_xact_lock` сериализует чтение `prev_hash` и вставку:
  без него два параллельных писателя создали бы развилку.
- **Append-only.** Row-trigger на `UPDATE/DELETE` + statement-trigger
  на `TRUNCATE` отклоняют мутации на уровне БД, а не только в коде.
  `TRUNCATE` не проходит через row-trigger, поэтому отдельный
  statement-level trigger обязателен.
- **Redis Pub/Sub.** Правка промпта — редкое событие; Pub/Sub даёт
  мгновенную инвалидацию во всех воркерах без polling. Локальный
  `reset_prompt_cache` выполняется всегда, поэтому при недоступном
  Redis админка и генерация продолжают работать в однопроцессном
  режиме. TTL-fallback — следующий шаг, чтобы потерянное сообщение не
  оставляло воркер со старым промптом.
- **Guardrails как первый барьер.** Детерминированные regex дешевле
  LLM-классификатора, не добавляют задержку и легко тестируются.
  Блокировка возвращает 422 до вызова LLM и пишет в аудит только
  `rule_ids`/sha256/длину — без сырого текста. Классификатор и корпус
  атак — T-033b, когда появится бюджет на дополнительный вызов.
- **Тир по IP.** До JWT единственный доверенный признак — адрес
  сокета; заголовки клиента можно подделать. `PRO_IPS` — статический
  белый список, `TRUST_FORWARDED_FOR` включается только за известным
  прокси. Это закрывает конкретную KI-031, не изображая полноценный
  RBAC.
