import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
APPLICATION_ID = os.getenv('APPLICATION_ID')
GUILD_ID = os.getenv('GUILD_ID')
BACKEND_URL = os.getenv('BACKEND_URL', 'http://127.0.0.1:8000/api/v1/')
# REQUEST_SIGNING_SECRET is REQUIRED for bot→backend signed requests.
# Must match the backend's REQUEST_SIGNING_SECRET exactly.
REQUEST_SIGNING_SECRET = os.getenv('REQUEST_SIGNING_SECRET', '')
# FRONTEND_URL is REQUIRED in production.  Fails closed if missing.
FRONTEND_URL = os.getenv('FRONTEND_URL')
if not FRONTEND_URL:
    import logging
    logging.getLogger('bot.config').critical(
        "FRONTEND_URL is not set in environment! "
        "Set FRONTEND_URL in .env or export it before starting the bot."
    )
    raise SystemExit(
        "FATAL: FRONTEND_URL environment variable is required. "
        "Set it in bot/.env or the system environment."
    )
# Railway dynamically injects PORT.  If WEBHOOK_PORT is not set explicitly,
# fall back to Railway's PORT, then to 5000 (development default).
# The entrypoint.sh also performs this mapping, but this ensures the bot
# works even when started directly (e.g. debugging).
_DEFAULT_PORT = int(os.getenv('PORT', 5000))
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST', '0.0.0.0')
WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', _DEFAULT_PORT))
# WEBHOOK_SECRET is REQUIRED in production.  Fails closed if missing.
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
if not WEBHOOK_SECRET:
    import logging
    logging.getLogger('bot.config').critical(
        "WEBHOOK_SECRET is not set in environment! "
        "Set WEBHOOK_SECRET in .env or export it before starting the bot."
    )
    raise SystemExit(
        "FATAL: WEBHOOK_SECRET environment variable is required. "
        "Set it in bot/.env or the system environment."
    )

# ── Cluster / Sharding (Phase 3-4) ──────────────────────────────────
CLUSTER_ENABLED = os.getenv('CLUSTER_ENABLED', 'False').lower() in ('1', 'true', 'yes')
CLUSTER_NODE_ID = os.getenv('CLUSTER_NODE_ID', '')
CLUSTER_API_URL = os.getenv('CLUSTER_API_URL', BACKEND_URL)
SHARD_COUNT = int(os.getenv('SHARD_COUNT', '1'))
AUTO_SHARD = os.getenv('AUTO_SHARD', 'False').lower() in ('1', 'true', 'yes')
