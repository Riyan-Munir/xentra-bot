#!/bin/bash
# ===========================================================================
# Xentra Bot Entrypoint — Railway / HF Spaces
# ===========================================================================
# Railway assigns a dynamic port via the PORT environment variable.
# This entrypoint maps PORT -> WEBHOOK_PORT so the aiohttp webhook server
# listens on the port Railway expects.
#
# On HF Spaces, set WEBHOOK_PORT=7860 explicitly (or let it default) and
# the script leaves it untouched because PORT won't be set.
# ===========================================================================

set -e

# ── Map Railway's PORT to WEBHOOK_PORT ────────────────────────────────
# Railway injects $PORT dynamically.  If PORT is set AND WEBHOOK_PORT
# hasn't been explicitly overridden in the environment, use PORT as the
# webhook server port.
if [ -n "$PORT" ] && [ -z "${WEBHOOK_PORT_OVERRIDE:-}" ]; then
    echo "==> Railway detected — mapping PORT=$PORT to WEBHOOK_PORT"
    export WEBHOOK_PORT="$PORT"
fi

echo "==> WEBHOOK_PORT=${WEBHOOK_PORT:-7860}"

# ── Start bot ─────────────────────────────────────────────────────────
# main.py uses asyncio.run() internally; exec replaces the shell so the
# bot receives signals directly (SIGTERM, SIGINT, etc.).
echo "==> Starting Xentra Bot..."
exec python main.py
