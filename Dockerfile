# syntax=docker/dockerfile:1.7

# ── Frontend build (Vite/React) ────────────────────────────────────────
FROM node:22-alpine AS web-build

WORKDIR /web
RUN npm install --global pnpm@12.14.0

# T-027: SPA собирается в тот же origin, что API, поэтому VITE_API_BASE
# обычно пуст. Для отдельного frontend-домена его можно передать
# build-arg-ом (GHCR workflow берёт vars.VITE_API_BASE).
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}

COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build


# ── Backend runtime ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Stockfish — нужен для Engine Service (T-008).
# Debian кладёт бинарник в /usr/games, а конфиг по умолчанию ждёт
# /usr/bin/stockfish — делаем стабильную ссылку.
# fonts-dejavu-core — TTF с кириллицей для PDF-экспорта комментария (T-107):
# fpdf2 встраивает шрифт, системные пакеты в slim-образе отсутствуют.
# libcairo2 — нативная библиотека для cairosvg: диаграмма доски в PDF
# (T-107b); без неё PDF всё равно генерируется, но без картинки.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      stockfish curl ca-certificates fonts-dejavu-core libcairo2 \
 && if [ -x /usr/games/stockfish ]; then ln -sf /usr/games/stockfish /usr/bin/stockfish; fi \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY worker.py ./worker.py

RUN pip install --upgrade pip \
 && pip install .

COPY alembic.ini ./
COPY alembic ./alembic
# KI-054: ops-скрипты (retention llm_calls/export_jobs, агрегаты) доступны
# внутри контейнера для cron/systemd — `python scripts/prune_export_jobs.py`.
COPY scripts ./scripts
COPY --from=web-build /web/dist ./web/dist

RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
