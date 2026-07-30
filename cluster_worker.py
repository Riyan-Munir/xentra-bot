"""
ClusterWorker, bot-side cluster management.

Sends periodic heartbeats to the backend, registers/deregisters this
node, and (in later phases) polls the webhook queue and participates in
shard claiming.

**All functionality is feature-gated behind ``CLUSTER_ENABLED``** (default
``False``).  When disabled, the ``ClusterWorker`` is a no-op stub so that
:class:`Xentra` can safely instantiate it without conditional branches.

All cluster API calls are signed with HMAC-SHA256 headers via
:func:`utils.request_signing.sign_request` so that the backend's
:class:`~cluster.auth.ClusterSignatureRequired` permission class can
validate them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp

from config import BACKEND_URL
from utils.http import get_http_session
from utils.request_signing import sign_request

logger = logging.getLogger("bot.cluster_worker")

# ── Environment ────────────────────────────────────────────────────────
CLUSTER_ENABLED = os.getenv("CLUSTER_ENABLED", "False").lower() in ("1", "true", "yes")

#: Static node ID (set once on first start, persisted across restarts).
#: If not set, a random UUID is generated each time (dev fallback).
CLUSTER_NODE_ID = os.getenv("CLUSTER_NODE_ID", "")

#: Override the base URL for cluster API (defaults to BACKEND_URL).
CLUSTER_API_URL = os.getenv("CLUSTER_API_URL", BACKEND_URL)

#: Heartbeat interval in seconds.
HEARTBEAT_INTERVAL = int(os.getenv("CLUSTER_HEARTBEAT_INTERVAL", "30"))

#: Shard count hint (used in registration; real count comes from Discord).
SHARD_COUNT = int(os.getenv("SHARD_COUNT", "1"))

#: Optional dedicated signing secret for cluster API calls.
#: Falls back to REQUEST_SIGNING_SECRET (from config) if not set.
CLUSTER_SIGNING_SECRET = os.getenv("CLUSTER_SIGNING_SECRET", "")


class ClusterWorker:
    """Bot-side cluster coordination.

    Parameters
    ----------
    node_id:
        Optional explicit node ID.  If not provided, attempts to read
        from ``CLUSTER_NODE_ID`` env var, or generates a random UUID.
    host:
        Optional hostname for registration.
    port:
        Optional port for registration.
    """

    def __init__(
        self,
        node_id: Optional[str] = None,
        host: str = "",
        port: int = 0,
    ) -> None:
        self._node_id = node_id or CLUSTER_NODE_ID or str(uuid.uuid4())
        self._host = host or os.getenv("HOSTNAME", "")
        self._port = port
        self._registered = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        if not CLUSTER_ENABLED:
            logger.debug("ClusterWorker disabled (CLUSTER_ENABLED=False)")

    # ── Properties ───────────────────────────────────────────────────

    @property
    def node_id(self) -> str:
        """This node's unique identifier."""
        return self._node_id

    @property
    def registered(self) -> bool:
        """Whether the node has registered with the backend."""
        return self._registered

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Register the node and start the heartbeat loop.

        Safe to call even when clustering is disabled, it is a no-op.
        """
        if not CLUSTER_ENABLED:
            logger.info("ClusterWorker disabled, skipping start")
            return

        await self._register()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "ClusterWorker started, node_id=%s heartbeat_interval=%ds",
            self._node_id,
            HEARTBEAT_INTERVAL,
        )

    async def stop(self) -> None:
        """Deregister the node and stop the heartbeat loop."""
        if not CLUSTER_ENABLED or not self._registered:
            return

        self._stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self._deregister()
        logger.info("ClusterWorker stopped, node_id=%s deregistered", self._node_id)

    # ── Registration ─────────────────────────────────────────────────

    async def _register(self) -> None:
        """Register this node with the backend cluster service."""
        url = self._url("cluster/register/")
        body_bytes = json.dumps({
            "node_id": self._node_id,
            "host": self._host,
            "port": self._port,
        }).encode("utf-8")
        headers = self._signed_headers("POST", url, body_bytes)

        session = get_http_session()
        try:
            async with session.post(
                url,
                json={
                    "node_id": self._node_id,
                    "host": self._host,
                    "port": self._port,
                },
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    self._registered = True
                    logger.info(
                        "Cluster node registered, id=%s status=%s",
                        self._node_id,
                        data.get("status"),
                    )
                else:
                    logger.warning(
                        "Cluster registration failed, status=%s body=%s",
                        resp.status,
                        await resp.text(),
                    )
        except Exception as exc:
            logger.warning("Cluster registration error: %s", exc)

    async def _deregister(self) -> None:
        """Deregister this node from the backend cluster service."""
        url = self._url("cluster/deregister/")
        body_bytes = json.dumps({"node_id": self._node_id}).encode("utf-8")
        headers = self._signed_headers("POST", url, body_bytes)

        session = get_http_session()
        try:
            async with session.post(
                url,
                json={"node_id": self._node_id},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    self._registered = False
                    logger.info("Cluster node deregistered, id=%s", self._node_id)
                else:
                    logger.warning(
                        "Cluster deregistration failed, status=%s",
                        resp.status,
                    )
        except Exception as exc:
            logger.warning("Cluster deregistration error: %s", exc)

    # ── Heartbeat ────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Periodically send heartbeats to the backend."""
        while not self._stop_event.is_set():
            await self._send_heartbeat()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=HEARTBEAT_INTERVAL,
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                continue  # next heartbeat

    async def _send_heartbeat(self) -> None:
        """Send a single heartbeat to the backend."""
        url = self._url("cluster/heartbeat/")
        body_bytes = json.dumps({"node_id": self._node_id}).encode("utf-8")
        headers = self._signed_headers("PATCH", url, body_bytes)

        session = get_http_session()
        try:
            async with session.patch(
                url,
                json={"node_id": self._node_id},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Heartbeat failed, status=%s", resp.status
                    )
        except Exception as exc:
            logger.warning("Heartbeat error: %s", exc)

    # ── Webhook polling (to be used in Phase 5) ──────────────────────

    async def poll_webhooks(self, limit: int = 10) -> list[Dict[str, Any]]:
        """Poll the backend for unclaimed webhook events.

        Returns a list of event dicts (empty if none available or if
        clustering is disabled).
        """
        if not CLUSTER_ENABLED:
            return []

        url = self._url("cluster/webhooks/poll/")
        body_bytes = b""  # GET request, no body
        headers = self._signed_headers("GET", url, body_bytes)

        session = get_http_session()
        try:
            async with session.get(
                url,
                params={"node_id": self._node_id, "limit": limit},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning(
                    "Webhook poll failed, status=%s", resp.status
                )
        except Exception as exc:
            logger.warning("Webhook poll error: %s", exc)
        return []

    async def claim_webhooks(self, event_ids: list[int]) -> int:
        """Claim a batch of webhook events for processing.

        Returns the number of successfully claimed events.
        """
        if not CLUSTER_ENABLED:
            return 0

        url = self._url("cluster/webhooks/claim/")
        body_bytes = json.dumps({
            "node_id": self._node_id,
            "event_ids": event_ids,
        }).encode("utf-8")
        headers = self._signed_headers("POST", url, body_bytes)

        session = get_http_session()
        try:
            async with session.post(
                url,
                json={"node_id": self._node_id, "event_ids": event_ids},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("claimed", 0)
        except Exception as exc:
            logger.warning("Webhook claim error: %s", exc)
        return 0

    async def complete_webhooks(self, event_ids: list[int]) -> int:
        """Mark webhook events as processed.

        Returns the number of events marked as processed.
        """
        if not CLUSTER_ENABLED:
            return 0

        url = self._url("cluster/webhooks/complete/")
        body_bytes = json.dumps({"event_ids": event_ids}).encode("utf-8")
        headers = self._signed_headers("POST", url, body_bytes)

        session = get_http_session()
        try:
            async with session.post(
                url,
                json={"event_ids": event_ids},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("processed", 0)
        except Exception as exc:
            logger.warning("Webhook complete error: %s", exc)
        return 0

    # ── Shard config & claiming ──────────────────────────────────────

    async def get_shard_config(self) -> Dict[str, Any]:
        """Fetch the shard configuration from the backend.

        Returns ``{"total_shards": N, "assigned_shard_ids": [...],
        "node_id": "..."}`` or an error dict on failure.

        The bot calls this **before** constructing the discord client so
        it can pass ``shard_count`` and ``shard_ids`` to ``commands.Bot()``.
        """
        if not CLUSTER_ENABLED:
            return {"total_shards": 1, "assigned_shard_ids": [], "node_id": ""}

        url = self._url("cluster/shard-config/")
        body_bytes = b""  # GET request
        headers = self._signed_headers("GET", url, body_bytes)

        session = get_http_session()
        try:
            async with session.get(
                url,
                params={"node_id": self._node_id},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning(
                    "Shard config fetch failed, status=%s body=%s",
                    resp.status,
                    await resp.text(),
                )
                return {"error": f"HTTP {resp.status}"}
        except Exception as exc:
            logger.warning("Shard config fetch error: %s", exc)
            return {"error": str(exc)}

    async def claim_shards(
        self,
        shard_count: int = 0,
        only_unassigned: bool = False,
    ) -> Dict[str, Any]:
        """Claim shards for this node from the backend.

        Returns the API response dict (or an error dict on failure).
        The response now includes ``shard_ids`` (list of ints).
        """
        if not CLUSTER_ENABLED:
            return {"claimed_from_dead": 0, "claimed_unassigned": 0, "total": 0, "shard_ids": []}

        url = self._url("cluster/claim-shards/")
        payload: Dict[str, Any] = {"node_id": self._node_id}
        if shard_count:
            payload["shard_count"] = shard_count
        if only_unassigned:
            payload["only_unassigned"] = True

        body_bytes = json.dumps(payload).encode("utf-8")
        headers = self._signed_headers("POST", url, body_bytes)

        session = get_http_session()
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return await resp.json()
        except Exception as exc:
            logger.warning("Shard claim error: %s", exc)
            return {"error": str(exc), "shard_ids": []}

    # ── Internal helpers ─────────────────────────────────────────────

    def _signed_headers(self, method: str, url: str, body: bytes) -> Dict[str, str]:
        """Build HMAC-signed headers for a cluster API request.

        Extracts just the URL path (not the full URL) for signing,
        which is what the backend's ``ClusterSignatureRequired``
        permission class expects from ``request.path``.

        Uses ``CLUSTER_SIGNING_SECRET`` if set, otherwise falls back
        to ``REQUEST_SIGNING_SECRET`` (from bot config) via
        ``sign_request()`` default behaviour.
        """
        parsed = urlparse(url)
        path = parsed.path
        secret: bytes | None = None
        if CLUSTER_SIGNING_SECRET:
            secret = CLUSTER_SIGNING_SECRET.encode("utf-8")
        return sign_request(method, path, body, secret=secret)

    @staticmethod
    def _url(path: str) -> str:
        """Build an absolute URL from *path* relative to CLUSTER_API_URL."""
        base = CLUSTER_API_URL.rstrip("/")
        return f"{base}/{path.lstrip('/')}"
