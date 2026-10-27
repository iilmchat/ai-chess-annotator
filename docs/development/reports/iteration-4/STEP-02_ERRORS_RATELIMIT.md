# STEP-02 — Обработчик ошибок и rate limiting

**Дата:** 2026-10-28
**Итерация:** 4 — API `/v1/analyze` (T-009)
**Статус:** ✅ выполнен

---

## 1. Маппинг доменных ошибок в HTTP

Обработчик зарегистрирован на базовый `AppError` (`app/main.py`), поэтому
ловит все наследники, включая будущие. Тело ответа единообразно:

```json
{"detail": "PGN не содержит ходов…", "code": "invalid_pgn"}
```

| Исключение | HTTP | `detail` |
| :--- | ---: | :--- |
| `InvalidPgnError` | 400 | текст ошибки разбора |
| `InvalidFenError` | 400 | текст ошибки FEN |
| `InvalidLevelError` | 422 | неизвестный уровень |
| `EngineError` / `EngineNotFoundError` | **500** | `Engine unavailable` |
| `EngineTimeoutError` | 504 | текст таймаута |

**Изменение относительно итерации 3:** движковые ошибки отдавали 503/504;
по контракту T-009 `EngineError`/`EngineNotFoundError` → **500**. Тексты
движковых ошибок не показываются клиенту (`Engine unavailable`) — детали
внутренние (пути к бинарнику, UCI-команды).

`mypy`-нюанс: `add_exception_handler` ждёт `Callable[[Request, Exception],
Response]`, поэтому обработчик принимает `Exception` и делает
`isinstance(exc, AppError)` — иначе strict-режим ругается на
контравариацию.

## 2. Rate limiting

`app/api/ratelimit.py`:

```python
current = INCR ratelimit:POST:/v1/analyze:<ip>
if current == 1: EXPIRE key 60
allowed = current <= 10
```

* **Middleware, а не dependency** — считаются все запросы к пути, включая
  те, что упадут на валидации тела (иначе флуд невалидным JSON бесплатен).
* **Только `POST /v1/analyze`** — `/v1/health` не защищается, чтобы
  мониторинг не упирался в лимит.
* **429** с `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`.
* **IP** — `X-Forwarded-For` (первый адрес) либо адрес сокета.
* **Fail-open**: при ошибке Redis запрос пропускается, в лог пишется
  `rate_limit_unavailable` (KI-015). Иначе падение кэша роняло бы анализ.
* Лимитер берётся из `app.state` (создаётся в lifespan), поэтому middleware
  не привязан к конкретному экземпляру Redis и легко тестируется фейком.

Порядок middleware: `RateLimitMiddleware` добавлен **до** `CORSMiddleware`,
значит CORS — внешний, и браузер увидит 429 с CORS-заголовками.

## 3. Тесты (14)

`tests/unit/test_rate_limit.py` — 7:
лимит и `remaining`, `EXPIRE` только на первом хите, валидация
аргументов, 429 + заголовки, незащищённый путь не считается, fail-open
при упавшем Redis, разделение клиентов по `X-Forwarded-For`, работа без
лимитера.

`tests/unit/test_engine_pool.py` — 7 (см. STEP-01).

## 4. Почему не `slowapi`

Одна зависимость ради двух Redis-вызовов не нужна; поведение должно быть
прозрачным и тестируемым без поднятия HTTP-стека. `slowapi` тянет
`limits` и свой механизм ключей — для одного эндпоинта это лишняя
поверхность.

## 5. Что дальше

Шаг 3 — эндпоинт `/v1/analyze` и запись в БД.
