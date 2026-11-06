# Итерация 59 — Reverse SSH GPU Tunnel Docs & Config (T-123, T-126)

**Дата:** 2026-11-06
**Статус:** ✅ завершена — настройки GPU-туннеля вынесены в `Settings`
(`LLM_LOCAL_BACKEND_URL`, `LLM_LOCAL_TIMEOUT_S`, `LLM_ROUTER_COOLDOWN_S`),
написан пошаговый гайд `docs/development/DEPLOYMENT_GPU_TUNNEL.md`;
`pytest`/`ruff`/`mypy` зелёные; GitHub Actions завершил прогон итерации
**success** (`42201745114`).
**Формат:** RULES §2.18 (7 разделов).

> **Поправка итерации 60.** Дефолт `LLM_LOCAL_TIMEOUT_S` здесь описан как
> 2 с (fail-fast). Это оказалось ошибкой: 2 с короче генерации 7B, и живой
> GPU считался недоступным. В итерации 60 дефолт поднят до 120 с — см.
> [`ITERATION-60.md`](ITERATION-60.md).

**Исходное состояние.** `main` = `a79dede` (итерация 58: замороженный
eval-набор, `BackendRouter` local-first → DeepSeek, CI зелёный). По T-123
(cloud-деплой через reverse SSH-туннель до домашней RTX 3060) оставалась
инфраструктурная часть: настройки туннеля в `Settings` и операционный гайд,
по которому VPS Selectel (Ubuntu + Docker) сможет ходить в LM Studio на
Windows-машине через обратный SSH-порт. Задача итерации — закрыть эту часть
(T-123/T-126) без изменения схемы БД и без реального VPS.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `docs/development/DEPLOYMENT_GPU_TUNNEL.md` | Гайд (907 строк, 12 разделов): Windows + LM Studio → Selectel VPS, `ssh -N -R`, GatewayPorts, проверка из контейнера, `.env`, fallback при выключении ПК, безопасность, troubleshooting, rollback |
| `docs/development/reports/v1.5.0/ITERATION-59.md` | Этот отчёт (7 разделов) |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/config.py` | `llm_local_backend_url: str \| None = None`, `llm_local_timeout_s: float = 2.0` (вместо `llm_router_local_timeout_s`), `llm_router_cooldown_s: float = 60.0`; read-only свойство `local_llm_base_url` (приоритет новой настройки, fallback на legacy `vllm_base_url`, нормализация `/`) |
| `app/llm/factory.py` | `_local_client(..., base_url=None)` по умолчанию берёт `cfg.local_llm_base_url`; `club` — `LLM_LOCAL_BACKEND_URL` + `LLM_LOCAL_TIMEOUT_S` + `max_retries=0`; `beginner` — тоже `local_llm_base_url` (на VPS иначе локальной модели не видно); `gm` без изменений; docstring-таблица маршрутизации |
| `app/llm/router.py` | Публичное свойство `cooldown_s` (для тестов/ops-наблюдаемости) |
| `.env.example` | `LLM_LOCAL_BACKEND_URL=http://127.0.0.1:8034/v1`, `LLM_LOCAL_TIMEOUT_S=2` (вместо `LLM_ROUTER_LOCAL_TIMEOUT_S`), `LLM_ROUTER_COOLDOWN_S=60` + комментарии про VPS/туннель |
| `tests/unit/test_llm.py` | +6 тестов: дефолт, env-override, приоритет/fallback URL, factory `club` с туннельными URL/таймаутом/cooldown, factory `beginner` через туннель |
| `TODO.md` | +T-126 (**[x]**), обновлён статус T-123 (конфиг/гайд готовы, живой деплой остаётся), актуальные имена настроек в T-125 |
| `docs/CHANGELOG.md` | `[Unreleased] → Added`: настройки туннеля + гайд; пометка о переименовании в записи итерации 58 |
| `docs/development/RELEASES.md` | Раздел «Итерация 59», пометка о переименовании в разделе 58 |
| `docs/development/reports/v1.5.0/ITERATION-58.md` | Пометка о переименовании `LLM_ROUTER_LOCAL_TIMEOUT_S` → `LLM_LOCAL_TIMEOUT_S` |
| `docs/README.md` | Гайд добавлен в навигационный индекс |
| `README.md` | Бейдж/счётчик backend-тестов (1362), строка итерации 59 |

---

## 2. Покрытие

| Область | Тест | Что покрыто |
| :--- | :--- | :--- |
| Дефолты `Settings` | `test_local_llm_base_url_defaults_to_lmstudio` | `local_llm_base_url == http://127.0.0.1:8034/v1`, timeout 2.0, cooldown 60.0 |
| Env-настройки VPS | `test_tunnel_settings_are_read_from_env` | `LLM_LOCAL_BACKEND_URL`/`LLM_LOCAL_TIMEOUT_S`/`LLM_ROUTER_COOLDOWN_S` из окружения; хвостовой `/` убирается |
| Приоритет URL | `test_local_llm_base_url_prefers_tunnel_over_legacy_vllm` | Новая переменная побеждает legacy `VLLM_BASE_URL` |
| Совместимость | `test_local_llm_base_url_falls_back_to_legacy_vllm` | Пустой `LLM_LOCAL_BACKEND_URL` → `VLLM_BASE_URL` (dev-окружения не ломаются) |
| Factory `club` | `test_factory_club_uses_tunnel_settings` | `BackendRouter.primary.endpoint` = туннельный URL, `primary.timeout_s` = override, `cooldown_s` = override, fallback = DeepSeek; запрос реально уходит на туннельный хост (`MockTransport`) |
| Factory `beginner` | `test_factory_beginner_uses_tunnel_settings` | Прямой локальный клиент тоже ходит на `LLM_LOCAL_BACKEND_URL` |
| Регресс | `tests/unit/test_llm_router.py` (24), `test_lora_config.py` (13), `test_warmup.py` (4) | Роутер/фабрика/LoRA/прогрев не изменили поведения; тесты роутера не правились |
| Полная сюита | `pytest -q` (CI-подобно) | **1362 passed, 15 skipped, 1 deselected, 1 xfailed** |

Новые тесты: **6** (`tests/unit/test_llm.py`: 28 → 34).

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** **1362 passed, 15 skipped, 1 deselected, 1 xfailed** (155.03 с,
exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Полная сюита (CI-подобно) | `pytest -q --deselect tests/integration/test_llm_live.py::test_live_gm_uses_deepseek` при живых PostgreSQL `5437`/Redis `6385` | **1362 passed, 15 skipped, 1 deselected, 1 xfailed in 155.03 s** (exit 0) |
| LLM/фабрика/роутер/LoRA/warmup | `pytest -q tests/unit/test_llm.py tests/unit/test_llm_router.py tests/unit/test_lora_config.py tests/unit/test_warmup.py` | **75 passed** |
| `test_llm.py` (файл итерации) | `pytest -q tests/unit/test_llm.py` | **34 passed** (+6) |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 91 source files` |
| Миграции | `alembic check` | `No new upgrade operations detected.` |
| CI | GitHub Actions после push | ✅ **success** — run [`42201745114`](https://github.com/iilmchat/ai-chess-annotator/actions/runs/42201745114): backend — **1366 passed, 12 skipped, 1 xfailed** (107.25 с), frontend / e2e / docker — success |

**Skips (15):** 9 — live LM Studio/эмбеддинги (нет GPU в среде); 6 —
`test_token_budget_api` с env-зависимыми портами (в CI проходят).
**Deselected (1):** live DeepSeek-тест (в CI скипается без ключа).

---

## 4. Smoke

| Сценарий | Команда | Результат |
| :--- | :--- | :--- |
| Дефолт без env | `python -c` (см. ниже) | `local_llm_base_url = http://127.0.0.1:8034/v1`, timeout 2.0, cooldown 60.0 |
| Туннельный конфиг | `Settings(llm_local_backend_url="http://172.17.0.1:8034/v1/", llm_local_timeout_s=30, deepseek_api_key="test-key")` | `club`: `BackendRouter`, provider `lmstudio`, primary `http://172.17.0.1:8034/v1/chat/completions`, timeout **30.0**, cooldown **60.0**, fallback `https://api.deepseek.com/chat/completions` |
| `beginner` через туннель | тот же Settings | `OpenAICompatibleClient`, endpoint `http://172.17.0.1:8034/v1/chat/completions` |
| Конфиг-файл | `grep LLM_LOCAL .env.example` | `LLM_LOCAL_BACKEND_URL=http://127.0.0.1:8034/v1`, `LLM_LOCAL_TIMEOUT_S=2`, `LLM_ROUTER_COOLDOWN_S=60` |
| Гайд: сверка с кодом | ревью Lead + агента | Команды `ssh -N -R`, `ss -ltnp`, `curl` из контейнера, `docker compose ... up -d app`, `GatewayPorts clientspecified` сверены с `app/config.py`, `app/llm/factory.py`, `docker-compose.prod.yml`; путь `GETTING_STARTED.md` §7 существует |
| Живой туннель | — | Не выполнен: нет VPS/домашней GPU в среде; это шаг стенда по гайду (T-123) |

Фактический вывод smoke-скрипта:

```text
default local_llm_base_url: http://127.0.0.1:8034/v1
default timeout/cooldown: 2.0 60.0
club: BackendRouter | provider: lmstudio | primary: http://172.17.0.1:8034/v1/chat/completions
club timeout: 30.0 | cooldown: 60.0
club fallback: https://api.deepseek.com/chat/completions
beginner: OpenAICompatibleClient http://172.17.0.1:8034/v1/chat/completions timeout: 120.0
```

---

## 5. Что не так

- **Живой туннель и деплой на Selectel не проверены.** В среде нет VPS и
  домашней GPU; гайд — процедура, сверенная с кодом и compose, а не прогнанная
  на стенде. Live-проверка (`ss`/`curl` из контейнера, `provider` в
  `llm_calls` при выключенном ПК) остаётся за T-123.
- **Переименование `LLM_ROUTER_LOCAL_TIMEOUT_S` → `LLM_LOCAL_TIMEOUT_S` —
  breaking для уже развёрнутого `.env`.** Неизвестный ключ игнорируется,
  значение молча откатится к дефолту 2 с. В гайде §7 и CHANGELOG есть
  пометка; при деплое VPS — задать новое имя.
- **Честная особенность таймаута:** `LLM_LOCAL_TIMEOUT_S` — это таймаут
  HTTP-запроса локального `club`-клиента (`max_retries=0`), а не «пинг».
  Не-стримовая генерация дольше 2 с будет считаться недоступностью и уйдёт
  в DeepSeek **даже при живом GPU**. Гайд §8.2 прямо это описывает и
  рекомендует 30–90 с после замеров `latency_ms`; в постановке ожидание
  «2 с = fail-fast» сохраняется как дефолт.
- **Docker-gateway VPS заранее неизвестен** (`172.17.0.1` default bridge vs
  `172.18.0.1` compose-сеть): гайд не хардкодит адрес, а показывает
  discovery через `docker inspect .NetworkSettings.Networks` и требует, чтобы bind-IP
  в `ssh -R` совпадал с `LLM_LOCAL_BACKEND_URL`.
- **`beginner` теперь зависит от `LLM_LOCAL_BACKEND_URL`.** На dev-машине
  поведение не изменилось (свойство падает назад в `VLLM_BASE_URL`), но в
  VPS-деплое без туннеля `beginner` честно отдаёт ошибку (облако для него
  запрещено) — это by design, отражено в гайде и SPEC §10.
- Исторические документы итерации 58 сохранены, но в них добавлена пометка
  о переименовании — чтобы не было двух «истин» об имени настройки.

---

## 6. Новые KI

**Нет.** Ограничения выше — либо внешние (нет VPS/GPU в среде), либо
следствие принятого дизайна (fail-fast таймаут, privacy `beginner`), либо
документированная миграция имени настройки. Для них не нужен отдельный
реестровый KI: поведение покрыто тестами, а операционные шаги — гайдом.
T-123 остаётся открытой задачей на живой деплой.

---

## 7. Обоснование

- **`LLM_LOCAL_BACKEND_URL` как nullable-настройка, а не новый дефолт.**
  Прямой дефолт перебил бы существующие dev-окружения с кастомным
  `VLLM_BASE_URL`. Свойство `local_llm_base_url` даёт один источник истины:
  новая переменная приоритетна, пустая — legacy fallback, хвостовой `/`
  нормализуется. В `.env.example` при этом прописан явный дефолт
  `http://127.0.0.1:8034/v1`.
- **Почему `beginner` тоже переведён на `LLM_LOCAL_BACKEND_URL`.** На
  облачном VPS `127.0.0.1` — это сам контейнер; без туннельного адреса
  локальная модель недоступна ни для `club`, ни для `beginner`. При пустой
  настройке дефолт совпадает с прежним `VLLM_BASE_URL`, поэтому dev-путь
  не меняется.
- **Почему имя `LLM_LOCAL_TIMEOUT_S`, а не `LLM_ROUTER_LOCAL_TIMEOUT_S`.**
  Переменная описывает локальный бэкенд, а не только роутер: её использует
  и `beginner`-клиент. Короткое имя ближе к `LLM_TIMEOUT_S` и лучше читается
  в VPS-`.env`; переименование зафиксировано в CHANGELOG и гайде.
- **`BackendRouter.cooldown_s` — публичное свойство.** Ops-наблюдаемость и
  тесты фабрики должны проверять фактическое значение без доступа к
  приватному `_cooldown_s`; это расширение API без изменения поведения.
- **Гайд как runbook, а не пересказ кода.** Деплой ломается на мелочах
  (`GatewayPorts no`, loopback-listener, docker-gateway не той сети,
  `restart` вместо пересоздания контейнера). Поэтому в документе —
  диагностика (`ss`, `curl` из контейнера, `journalctl`), 16-строчный
  troubleshooting, rollback и security-периметр; таймауты описаны честно,
  с рекомендацией подобрать значение по реальным замерам.
- **Тесты через `MockTransport`/`Settings`-инъекции.** Сеть и VPS в CI нет,
  но вся цепочка «env → Settings → property → factory → endpoint/timeout/
  cooldown» проверяется детерминированно, без реальных SSH-соединений.
