# syntax=docker/dockerfile:1.7
# ----------------------------------------------------------------------------
# Token Saver Router — production image.
# ----------------------------------------------------------------------------
# Two-stage build keeps the runtime image lean:
#   - builder : installs build deps + the package in editable-ish mode
#   - runtime : copies the installed site-packages + source, drops build deps
#
# Python 3.12 matches the project baseline (>=3.10). Pin to 3.12-slim for
# reproducible builds; bump deliberately when pyproject.requires-python moves.
# ----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# System deps for cryptography / argon2-cffi wheels.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
 && pip install ".[dev]"


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TOKEN_SAVER_HOST=0.0.0.0 \
    TOKEN_SAVER_PORT=8787

WORKDIR /app

# Non-root user — defence in depth, avoids surprise root-in-container issues.
RUN groupadd --system token_saver \
 && useradd --system --gid token_saver --uid 10001 --home /app token_saver

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/src /app/src
COPY pyproject.toml README.md /app/

ENV PYTHONPATH=/app/src

USER token_saver

EXPOSE 8787

# Healthcheck hits the placeholder /healthz endpoint — TASK-002-2 moves it
# under /admin/health with RBAC; revisit when that lands.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz').read()" || exit 1

CMD ["token-saver", "serve", "--host", "0.0.0.0", "--port", "8787"]