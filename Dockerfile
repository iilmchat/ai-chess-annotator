# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Stockfish 17 — нужен для Engine Service (T-008)
RUN apt-get update \
 && apt-get install -y --no-install-recommends stockfish curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip \
 && pip install -e .

COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]