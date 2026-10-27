# STEP-02 — Инфраструктура Docker Compose (T-002)

**Дата:** 2026-10-28
**Итерация:** 1 — Bootstrap (T-001…T-006)
**Статус:** ✅ выполнен (с отклонением по порту Redis — KI-001)

---

## 1. Что сделано

1. `docker compose up -d postgres redis` — подняты два сервиса на чистом
   окружении (созданы volume'ы `pg_data`, `redis_data`, сеть
   `ai-chess-annotator_default`).
2. **Обнаружен конфликт хостовых портов:** `127.0.0.1:6380` уже занят
   контейнером `ticket-redis` (другой проект). Docker не смог
   опубликовать порт (`bind: Only one usage of each socket address`).
3. Порт Redis в `docker-compose.yml` сделан переопределяемым:
   `"127.0.0.1:${REDIS_PORT:-6380}:6379"` — каноническое значение 6380
   сохранено по умолчанию (CI, prod, другие машины), локально задан 6381.
4. `.env.example` дополнен переменной `REDIS_PORT` с пояснением;
   локальный `.env` (gitignored) содержит `REDIS_PORT=6381` и
   `REDIS_URL=redis://localhost:6381/0`.
5. Заведён **KI-001** (Open, Low) — конфликт порта 6380 на dev-машине.

## 2. Проверки

```
docker compose ps
SERVICE    STATUS                    PORTS
postgres   Up 33 seconds (healthy)   127.0.0.1:5434->5432/tcp
redis      Up 8 seconds (healthy)    127.0.0.1:6381->6379/tcp
```

| Проверка | Результат |
| :--- | :--- |
| `postgres` healthy | ✅ `healthy` |
| `redis` healthy | ✅ `healthy` |
| Слушатель 5434 на loopback | ✅ `127.0.0.1:5434` |
| Слушатель 6381 на loopback | ✅ `127.0.0.1:6381` (вместо 6380 — KI-001) |
| `redis-cli ping` | `PONG` |
| PostgreSQL | `17.11 (Debian 17.11-1.pgdg12+2)` |
| pgvector доступен | `vector 0.8.7` (в `pg_available_extensions`) |
| Расширение установлено | пока нет (`plpgsql` только) — создаётся миграцией в шаге 4 |

## 3. Файлы

| Файл | Что |
| :--- | :--- |
| `docker-compose.yml` | `ports` redis → `${REDIS_PORT:-6380}` |
| `.env.example` | + `REDIS_PORT` с комментарием |
| `.env` (не в git) | `REDIS_PORT=6381`, `REDIS_URL=…:6381/0` |

## 4. Почему не «просто занять 6380»

Убивать контейнер `ticket-redis` чужого проекта или публиковать порт в
обход Docker — недопустимо: это ломает соседнее окружение и не
воспроизводится на других машинах. Переменная с дефолтом 6380 оставляет
поведение по спецификации для CI/чистых хостов и даёт явный,
документированный обход для занятого порта. Альтернатива «использовать
чужой Redis» отвергнута: общий кэш/очереди между проектами — источник
трудноуловимых багов и утечки ключей.

## 5. Что дальше

Шаг 3 — venv, `pip install -e ".[dev]"`, `.env`, `alembic current` (T-003/T-004).
