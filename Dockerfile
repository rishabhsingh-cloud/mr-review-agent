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

# alembic.ini + migrations live under backend/; run everything from there.
WORKDIR /app/backend
