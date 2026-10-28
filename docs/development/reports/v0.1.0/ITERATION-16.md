# Итерация 16 — Admin Panel, KI-030, OTel (Фаза 7, частично)

**Дата:** 2026-10-29
**Статус:** ⚠️ частично: T-028 и T-030 закрыты в объёме итерации, KI-030
остаётся Open (4.40/10 < 7/10).

## 1. Файлы (+/-)

### Созданы

- `app/admin.py` — `starlette-admin` на `/admin`: токен/пароль, 401/403,
  read-only views `AnnotatedGame`, `Commentary`, `Edit`, нормализация
  `/admin` → `/admin/`.
- `app/observability.py` — `configure_telemetry`, `shutdown_telemetry`,
  `get_tracer`; экспорт `console`/`otlp` при `OTEL_ENABLED=true`.
- `tests/integration/test_admin.py` — 6 тестов админки.
- `tests/unit/test_observability.py` — 3 теста OTel-спанов.
- `docs/development/reports/v0.1.0/ITERATION-16.md` — этот отчёт.
- `docs/development/reports/v0.1.0/admin_smoke.log` — лог входа в `/admin`.
- `docs/development/reports/v0.1.0/otel_span_example.log` — пример спана.
- `docs/development/reports/LLM_EVAL_CLUB_ITER16*.md` и
  `llm_eval_club_iter16*.json` — сырые отчёты V1/V2/V3 и rejudge.

### Изменены

- `app/main.py` — монтирование админки, `AdminPathNormalizer`, настройка
  и остановка OTel в `lifespan`.
- `app/config.py` — `admin_username`, `admin_password`,
  `admin_session_secret`, `admin_session_ttl_min`, цены LLM, параметры
  OTel.
- `app/llm/openai_client.py` — спаны `llm_generate`/`llm_stream`,
  атрибуты `llm.model`, `llm.level`, `llm.tokens_in/out`, `llm.cost_usd`,
  `llm.status`; цена в конструкторе.
- `app/llm/base.py` — `set_level()` для атрибута `llm.level`.
- `app/llm/factory.py` — передача цен в клиент.
- `app/commentary/service.py`, `app/commentary/streaming.py`,
  `app/chat/service.py`, `app/chat/streaming.py` — `llm.set_level(level)`.
- `app/llm/prompts.py` — `club`: топ-3 момента, без PV; однозначные
  заголовки «Ход N белых/чёрных», «Сыграно», «Оценка за белых».
- `app/prompts/club.txt` — короткий промпт, few-shot без SAN, правила
  стороны/оценки/рекомендации, self-check.
- `app/engine/validator.py` — одиночная клетка, нелегальная во всех
  позициях, больше не считается ходом (ложные 502 на «король на e1»).
- `tests/unit/test_prompts.py` — +3 теста, обновлены ожидания PV/примеров.
- `tests/unit/test_validator.py` — +1 тест на клетку.
- `pyproject.toml` — `starlette-admin`, `itsdangerous`, `tzdata`.
- `.env.example` — `ADMIN_*`, `OTEL_EXPORTER`, `LLM_PRICE_*`.
- `docs/development/RULES.md` — §2.18 «Стандарт отчётов по фазе».
- `TODO.md` — T-028 и T-030 переведены в `[~]` с остатком.
- `docs/KNOWN_ISSUES.md` — KI-030 (прогресс V3), KI-023 (клетки).
- `docs/development/reports/LLM_EVAL.md` — раздел «Итерация 16».
- `docs/development/RELEASES.md`, `README.md`, `docs/CHANGELOG.md` —
  статус, метрики, changelog.

## 2. Покрытие

| Область | Тесты | Что проверяют |
| :--- | :--- | :--- |
| Admin | `tests/integration/test_admin.py` (6) | 401 без токена, 403 на неверный, 200 на верный, публичный `/admin/login`, вход по форме + `admin_session`, три view-страницы |
| OTel | `tests/unit/test_observability.py` (3) | успешный спан с токенами/стоимостью, ошибка со статусом ERROR, `OTEL_ENABLED=false` → `None` |
| Промпты/факты | `tests/unit/test_prompts.py` (+3, 2 обновлены) | `club` = топ-3 без PV, `gm` с PV, few-shot без SAN, self-check |
| Валидатор | `tests/unit/test_validator.py` (+1) | `e1` как поле не даёт ложный нелегальный ход |

Итого: **+13 тестов** к 205 → **218 passed, 1 xfailed**.

## 3. Результаты

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Lint | `ruff check .` | **0** ошибок |
| Types | `mypy app` | **0** ошибок, 51 файл |
| Tests | `pytest -q` | **218 passed, 1 xfailed** (182.06 с) |
| Build | backend | **0/0** (ruff/mypy); фронтенд не менялся |
| Admin smoke | `admin_smoke.log` | 401 / 403 / 200, login 303 + cookie, 3 списка 200 |
| OTel smoke | `otel_span_example.log` | спан `llm_generate`, status OK, 2.47 с |
| LLM eval | `evaluate_llm.py` + `rejudge_llm.py` | **4.40/10** (было 3.83) |

### LLM-as-judge, `club` (5 партий, полный пакет фактов)

| Вариант | Факт. точность | Полезность | Стиль | Средняя | Оценено | 5xx |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| До (итерация 14) | 3.25 | 4.00 | 4.25 | **3.83** | 4/5 | 1 |
| После V3 (итерация 16) | 4.00 | 4.40 | 4.80 | **4.40** | 5/5 | 0 |

Прирост: **+0.57** общей, **+0.75** фактической точности. Prompt-токены
≈ 3.4k → 1.9k. Промежуточные V1 (конкретные few-shot) — 3.00 и 1/5
`502`; V2 (few-shot без SAN) — 3.80. Порог 7/10 не достигнут.

## 4. Smoke

### Админка

Сценарий: `/admin` без токена → 401; с неверным → 403; с
`X-Admin-Token` → 200; форма `/admin/login` → 303 и cookie
`admin_session`; после входа открываются списки RAG/комментариев/правок.

Фактический вывод — `docs/development/reports/v0.1.0/admin_smoke.log`:

```text
GET /admin [no token] -> 401
GET /admin [wrong token] -> 403
GET /admin [valid token] -> 200
GET /admin/login -> 200, csrf=True
POST /admin/login -> 303, location=http://testserver/admin/,
  cookies=['admin_session']
GET /admin [session cookie] -> 200
GET /admin/annotated-game/list [session cookie] -> 200
GET /admin/commentary/list [session cookie] -> 200
GET /admin/edit/list [session cookie] -> 200
```

### OTel

Реальный вызов LM Studio с `OTEL_ENABLED=true`, `OTEL_EXPORTER=console`
(`docs/development/reports/v0.1.0/otel_span_example.log`):

```json
{
  "name": "llm_generate",
  "status": {"status_code": "OK"},
  "attributes": {
    "llm.provider": "lmstudio",
    "llm.model": "qwen/qwen3-4b-2507",
    "llm.level": "club",
    "llm.tokens_in": 45,
    "llm.tokens_out": 32,
    "llm.cost_usd": 0.0,
    "llm.status": "ok",
    "llm.latency_ms": 2471
  }
}
```

### Промпт и генерация

`club.txt` сокращён, few-shot без SAN; пакет — топ-3 момента без PV.
Прогон `scripts/evaluate_llm.py --games 5 --levels club` + повторная
оценка `scripts/rejudge_llm.py` дали 4.40/10. Правка промпта выполнялась
в файле, **не** через админку: Prompt Editor — T-029.

## 5. Что не так

- **Админка read-only.** Нет ролей `user/admin/auditor`, `users.role`/
  `password_hash`, мягкого удаления RAG, восстановления, аудита мутаций,
  Prompt Editor, Export и Audit Log. Сессия — подписанная cookie, не
  Redis (`T-028`, `T-029`, `T-034`, `T-035`).
- **OTel частичный.** Нет W3C-`traceparent`, `trace_id` в ответах,
  durable-таблицы `llm_calls`, спанов `http.request`/`validator`/
  `rag.retrieve`, дашбордов. Цена задаётся глобально, не по модели
  (`T-030`, KI-021).
- **Качество LLM.** 4.40/10 < 7/10. 4B-модель путает стороны/полуходы и
  приписывает оценки; конкретные few-shot примеры она копирует в ответ
  (V1: 3.00, `Qxg5`/`Bg5`/`c6`). Семантическая проверка атрибуции не
  сделана (KI-023).
- **Выборка eval.** 5 коротких партий, один судья DeepSeek, bias
  LLM-as-judge; слепой тест T-024 не закрыт.
- **Валидатор.** Клетка, нелегальная во всех позициях, теперь не
  считается ходом; это убирает ложные 502, но может пропустить
  выдуманный пешечный ход без взятия.
- **Windows/tzdata.** `starlette-admin` конвертирует время через
  `ZoneInfo`; на Windows нужен пакет `tzdata` (добавлен).

## 6. Новые KI

**Нет.** Новых блокеров не обнаружено. Обновлены:

- **KI-030** — Open: V3 4.40/10, остаток — атрибуция 4B-модели.
- **KI-023** — Open: добавлено пояснение про клетки; семантика не
  проверяется.

## 7. Обоснование

- **`starlette-admin`** — по `DESIGN_ADMIN_AND_OPS.md`: нативно работает с
  `AsyncEngine`/`async_sessionmaker`, монтируется в FastAPI, не требует
  второго фреймворка. Views сделаны read-only, пока нет аудита (T-035):
  любая запись без аудита нарушает принцип append-only.
- **Токен + форма.** `X-Admin-Token` переиспользует `ADMIN_API_KEY` и
  даёт 401/403 для API/smoke; cookie-сессия нужна браузеру. Отдельный
  `AuthProvider` и `SessionMiddleware` — минимальная замена серверных
  сессий в Redis до T-028-полного.
- **OTel в клиенте.** Все провайдеры идут через
  `OpenAICompatibleClient`, поэтому один спан покрывает LM Studio и
  DeepSeek. `set_level()` выбран вместо изменения сигнатуры `complete`,
  чтобы не ломать фейки и публичный интерфейс.
- **Топ-3 без PV.** Прямое требование итерации; V3 добавил однозначные
  заголовки «Ход N белых/чёрных» и правило «оценка всегда за белых»,
  потому что судья чаще всего ловил именно путаницу сторон/номеров.
- **Few-shot без SAN.** Эмпирика V1 показала, что 4B-модель копирует
  конкретные ходы из примеров; поэтому примеры оставлены как образец
  стиля, а факты — только из пакета.
- **Rejudge, а не только `evaluate_llm.py`.** Судья должен видеть тот же
  пакет фактов, что генератор; иначе рекомендации Policy и мотивы
  выглядят «выдуманными». `rejudge_llm.py` уже был в проекте и применён
  для честного сравнения «до/после».
