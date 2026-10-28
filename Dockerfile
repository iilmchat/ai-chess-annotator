# syntax=docker/dockerfile:1.7

# ── Frontend build (Vite/React) ────────────────────────────────────────
FROM node:22-alpine AS web-build

WORKDIR /web
RUN npm install --global pnpm@12.14.0

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
RUN apt-get update \
 && apt-get install -y --no-install-recommends stockfish curl ca-certificates \
 && if [ -x /usr/games/stockfish ]; then ln -sf /usr/games/stockfish /usr/bin/stockfish; fi \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip \
 && pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY --from=web-build /web/dist ./web/dist

RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
