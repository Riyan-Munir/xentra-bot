# ===========================================================================
# Xentra Bot — Railway / HF Spaces Dockerfile
# ===========================================================================
# Build args ────────────────────────────────────────────────────────────────
# All env configuration is done via Railway Variables or HF Space Secrets.
# Railway automatically sets PORT — the entrypoint maps it to WEBHOOK_PORT.
# ===========================================================================

# Stage 1: Build / install dependencies
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels -r requirements.txt


# Stage 2: Runtime image
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system bot \
    && adduser --system --ingroup bot bot

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache /wheels/* \
    && rm -rf /wheels

COPY . .

# Give the bot user write access so logging.FileHandler('Logs.txt') works
RUN mkdir -p /app/data \
    && chown -R bot:bot /app

USER bot

EXPOSE 7860

# Health check — polls the aiohttp /health endpoint on WEBHOOK_PORT
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.getenv('WEBHOOK_PORT','7860'); resp=urllib.request.urlopen(f'http://localhost:{port}/health'); exit(0 if resp.status == 200 else 1)" \
    || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────
# entrypoint.sh handles PORT→WEBHOOK_PORT mapping (Railway) and then
# starts the bot.
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
