# Backend image for api + worker + migrations. Python 3.12 (not 3.14) on purpose.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps + package (hatchling maps backend/app -> importable `app`).
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install -e .
# Semantic chunking (tree-sitter) is best-effort: if wheels are unavailable the
# app falls back to line-window chunking, so never fail the build on it.
RUN pip install "tree-sitter-language-pack" || echo "tree-sitter unavailable; using line-window chunking"

# alembic.ini + migrations live under backend/; run everything from there.
WORKDIR /app/backend
