import asyncio
import hmac
import json
import logging
import time
from typing import Optional
from urllib.parse import urlparse

from aiohttp import web, ClientTimeout

from config import CLUSTER_ENABLED
from utils.http import get_http_session

logger = logging.getLogger('bot.webhook')

WEBHOOK_POLL_INTERVAL = 30  # seconds

STATUS_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Xentra Bot · Status</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:2rem;max-width:480px;width:90%;text-align:center}}
  h1{{font-size:1.5rem;margin-bottom:0.5rem;color:#f0f6fc}}
  .status{{display:inline-block;padding:0.25rem 0.75rem;border-radius:999px;font-size:0.85rem;font-weight:600;background:#238636;color:#fff;margin-bottom:1.5rem}}
  .row{{display:flex;justify-content:space-between;padding:0.5rem 0;border-bottom:1px solid #21262d}}
  .row:last-child{{border-bottom:none}}
  .label{{color:#8b949e}}
  .value{{color:#e6edf3;font-family:'SF Mono','Cascadia Code','Courier New',monospace;font-size:0.9rem}}
  .offline{{background:#da3633}}
</style></head>
<body><div class="card">
<h1>🤖 Xentra Bot</h1>
<div class="status" id="status-badge">{status}</div>
<div class="row"><span class="label">Last Request</span><span class="value" id="last-req">{last_request}</span></div>
<div class="row"><span class="label">Bot Latency</span><span class="value" id="latency">{latency} ms</span></div>
<div class="row"><span class="label">Uptime</span><span class="value" id="uptime">{uptime}</span></div>
</div></body></html>"""

class WebhookServer:
    def __init__(self, bot):
        self.bot = bot
        self._last_request_time: Optional[float] = None
        self._start_time: float = time.time()
        self.app = web.Application()
        # Unauthenticated health endpoint — used by HF Spaces monitor ping
        self.app.router.add_route('GET', '/health', self.handle_health)
        # HTML status page — shows last request timestamp, bot latency, uptime
        self.app.router.add_route('GET', '/status', self.handle_status_page)
        # Accept POST webhooks and preflight OPTIONS from backend only
        self.app.router.add_route('POST', '/status', self.handle_status_update)
        self.app.router.add_route('OPTIONS', '/status', self.handle_status_update)
        # Discord API proxy — backend delegates Discord HTTPS calls through the bot
        # because the backend's urllib3/requests stack is blocked by Cloudflare.
        self.app.router.add_route('POST', '/proxy/discord', self.handle_discord_proxy)
        self.app.router.add_route('OPTIONS', '/proxy/discord', self.handle_discord_proxy)
        self.runner = None
        self._poll_task: Optional[asyncio.Task] = None

    async def handle_health(self, request):
        """Unauthenticated health check — returns 200 OK for monitor pings."""
        self._touch_last_request()
        return web.json_response({'status': 'healthy'})

    async def handle_status_page(self, request):
        """Unauthenticated HTML status page with last-request timestamp."""
        self._touch_last_request()
        uptime_secs = int(time.time() - self._start_time)
        days, remainder = divmod(uptime_secs, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"
        last_str = (
            time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(self._last_request_time))
            if self._last_request_time else 'N/A'
        )
        try:
            latency_ms = int(round(self.bot.latency * 1000))
        except Exception:
            latency_ms = 0
        is_online = self.bot.is_ready()
        html = STATUS_HTML.format(
            status='Online' if is_online else 'Offline',
            last_request=last_str,
            latency=str(latency_ms),
            uptime=uptime_str,
        )
        return web.Response(text=html, content_type='text/html')

    def _touch_last_request(self) -> None:
        """Record the current time as the most recent request."""
        self._last_request_time = time.time()

    async def handle_status_update(self, request):
        # Read secrets and backend origin
        token = request.headers.get('X-Webhook-Token')
        from config import WEBHOOK_SECRET, BACKEND_URL

        origin = request.headers.get('Origin') or request.headers.get('origin')
        try:
            parsed = urlparse(BACKEND_URL)
            backend_origin = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            backend_origin = None

        # Handle CORS preflight
        if request.method == 'OPTIONS':
            headers = {
                'Access-Control-Allow-Origin': backend_origin or '*',
                'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, X-Webhook-Token',
            }
            return web.Response(status=204, headers=headers)

        # Enforce origin check: only accept requests from configured backend origin
        if origin and backend_origin and origin != backend_origin:
            logger.warning(f"Rejected webhook from origin {origin}; expected {backend_origin}")
            resp = web.json_response({'status': 'forbidden'}, status=403)
            if backend_origin:
                resp.headers['Access-Control-Allow-Origin'] = backend_origin
            return resp

        # Timing-safe comparison for webhook secret
        if not token or not hmac.compare_digest(token, WEBHOOK_SECRET):
            logger.warning(f"Unauthorized webhook attempt from {request.remote}")
            resp = web.json_response({'status': 'unauthorized'}, status=401)
            if backend_origin:
                resp.headers['Access-Control-Allow-Origin'] = backend_origin
            return resp

        try:
            data = await request.json()
            packet_payload = data
            if isinstance(data, dict) and data.get('type') and 'data' in data:
                packet_payload = data['data']

            status_text = packet_payload.get('status')
            if status_text:
                logger.info(f"Received status update via webhook: {status_text}")

            # ── System message routing ──────────────────────────
            # The payload arrives as {"type": "security_bypass_attempt", "data": {...}}
            # packet_payload is the unwrapped inner `data` dict (all fields),
            # while the outer `type` field holds the message-type for routing.
            message_type = data.get('type', '')

            if message_type:
                from utils.system_message_handler import handle_system_message
                asyncio.ensure_future(
                    handle_system_message(
                        message_type=message_type,
                        data=packet_payload,
                        bot=self.bot,
                    )
                )

            response = web.json_response({'status': 'ok'})
            # Strict CORS header: only allow configured backend origin
            if backend_origin:
                response.headers['Access-Control-Allow-Origin'] = backend_origin
            return response
        except Exception as e:
            logger.error(f"Error handling webhook: {e}")
            resp = web.json_response({'status': 'error', 'message': str(e)}, status=400)
            if backend_origin:
                resp.headers['Access-Control-Allow-Origin'] = backend_origin
            return resp

    async def handle_discord_proxy(self, request):
        """Proxy an HTTP request to Discord's API through the bot's aiohttp session.

        The backend POSTs a JSON body::

            {
                "method": "POST",
                "url": "https://discord.com/api/oauth2/token",
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                "data": {"client_id": "...", ...}
            }

        Returns::

            {
                "status_code": 200,
                "body": { ... }  # parsed JSON response from Discord
            }

        Authentication: same ``X-Webhook-Token`` / ``WEBHOOK_SECRET`` as
        the existing ``/status`` endpoint.
        """
        # ── Authentication ────────────────────────────────────
        token = request.headers.get('X-Webhook-Token')
        from config import WEBHOOK_SECRET, BACKEND_URL

        origin = request.headers.get('Origin') or request.headers.get('origin')
        try:
            parsed = urlparse(BACKEND_URL)
            backend_origin = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            backend_origin = None

        # Handle CORS preflight
        if request.method == 'OPTIONS':
            headers = {
                'Access-Control-Allow-Origin': backend_origin or '*',
                'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, X-Webhook-Token',
            }
            return web.Response(status=204, headers=headers)

        # Enforce origin check
        if origin and backend_origin and origin != backend_origin:
            logger.warning(f"Rejected discord proxy from origin {origin}; expected {backend_origin}")
            resp = web.json_response({'status': 'forbidden'}, status=403)
            if backend_origin:
                resp.headers['Access-Control-Allow-Origin'] = backend_origin
            return resp

        # Timing-safe secret comparison
        if not token or not hmac.compare_digest(token, WEBHOOK_SECRET):
            logger.warning(f"Unauthorized discord proxy attempt from {request.remote}")
            resp = web.json_response({'status': 'unauthorized'}, status=401)
            if backend_origin:
                resp.headers['Access-Control-Allow-Origin'] = backend_origin
            return resp

        # ── Parse proxy payload ──────────────────────────────
        try:
            payload = await request.json()
        except Exception as e:
            logger.error(f"Discord proxy: invalid JSON from backend: {e}")
            return web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)

        method = payload.get('method', 'GET').upper()
        url = payload.get('url', '')
        headers = payload.get('headers', {}) or {}
        data = payload.get('data')
        params = payload.get('params')

        if not url:
            return web.json_response({'status': 'error', 'message': 'url is required'}, status=400)

        # ── Forward request via aiohttp ──────────────────────
        # Use the bot's internal discord.py HTTP session, which has
        # proper TLS configuration to bypass Cloudflare's JA3
        # fingerprint blocking.  The generic get_http_session() may
        # also be blocked when running on HF Spaces.
        try:
            dpy_session = self.bot.http._session
        except AttributeError:
            dpy_session = get_http_session()
        try:
            async with dpy_session.request(
                method,
                url,
                headers=headers,
                data=data,
                params=params,
                timeout=ClientTimeout(total=25),
            ) as resp:
                try:
                    body = await resp.json()
                except Exception:
                    text = await resp.text()
                    body = text

                result = {
                    'status_code': resp.status,
                    'body': body,
                }
                return web.json_response(result)

        except Exception as e:
            logger.error(f"Discord proxy error: {method} {url} — {e}")
            resp = web.json_response(
                {'status': 'error', 'message': str(e)},
                status=502,  # Bad Gateway — the bot couldn't reach Discord
            )
            if backend_origin:
                resp.headers['Access-Control-Allow-Origin'] = backend_origin
            return resp

    async def start(self, host=None, port=None):
        from config import WEBHOOK_HOST, WEBHOOK_PORT
        host = host or WEBHOOK_HOST or '0.0.0.0'
        port = port or WEBHOOK_PORT or 5000
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host, port)
        await site.start()
        logger.info(f"Webhook server started on {host}:{port}")

        # Start DB polling loop when clustering is enabled
        if CLUSTER_ENABLED and self.bot.cluster_worker:
            self._poll_task = asyncio.create_task(self._db_poll_loop())
            logger.info("Webhook DB polling started (interval=%ds)", WEBHOOK_POLL_INTERVAL)

    async def stop(self):
        # Stop DB polling
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            logger.info("Webhook DB polling stopped")

        if self.runner:
            await self.runner.cleanup()
            logger.info("Webhook server stopped")

    async def _db_poll_loop(self) -> None:
        """Periodically poll the backend :class:`WebhookEvent` queue.

        This is an alternative delivery path alongside the direct HTTP
        webhook server.  Events are claimed, processed via the system
        message handler, and marked as completed.
        """
        while True:
            try:
                await asyncio.sleep(WEBHOOK_POLL_INTERVAL)
                worker = self.bot.cluster_worker
                if worker is None:
                    continue

                events = await worker.poll_webhooks(limit=5)
                if not events:
                    continue

                event_ids = [e["id"] for e in events]

                # Claim the batch atomically
                claimed = await worker.claim_webhooks(event_ids)
                if claimed == 0:
                    continue

                from utils.system_message_handler import handle_system_message

                for event in events:
                    try:
                        asyncio.ensure_future(
                            handle_system_message(
                                message_type=event.get("event_type", ""),
                                data=event.get("payload", {}),
                                bot=self.bot,
                            )
                        )
                    except Exception as exc:
                        logger.error(
                            "Error processing polled webhook %s: %s",
                            event.get("id"),
                            exc,
                        )

                # Mark all as processed
                await worker.complete_webhooks(event_ids)
                logger.debug("Processed %d polled webhook event(s)", len(event_ids))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Webhook poll loop error: %s", exc)
