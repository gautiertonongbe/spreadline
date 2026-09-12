# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tooling for the few wheels that need it, removed in the same layer so it
# does not ship in the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first: the layer is cached until pyproject.toml changes.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install "." && pip install ".[dev]"

COPY . .

# The application runs unprivileged.
RUN useradd --create-home --uid 10001 spreadline \
    && chown -R spreadline:spreadline /app
USER spreadline

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
