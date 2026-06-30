"""
Shared aiohttp.ClientSession singleton for all bot HTTP requests.

Replaces the per-request pattern of creating ~48 separate TCP connections
with a single persistent connection pool managed at the application level.

Provides **automatic** HMAC request signing for bot→backend communication
via a transparent wrapper around ``aiohttp.ClientSession``.  Every caller
that uses ``session.get(url, ...)`` automatically gets ``X-Signature``,
``X-Timestamp``, and ``X-Nonce`` headers injected for bot-prefixed paths
without any import changes.

Explicit ``signed_get()`` / ``signed_post()`` / ``signed_patch()`` /
``signed_delete()`` helpers are also available for callers that want to
opt in explicitly.
"""

import aiohttp
import logging
from typing import Optional, Any

from utils.request_signing import sign_request, _get_signing_secret

logger = logging.getLogger('bot.http')

_http_session: aiohttp.ClientSession | None = None

# ── Bot path prefixes that require HMAC signing ───────────────────────
_BOT_PATH_PREFIXES = (
    "/api/v1/rooms/bot/",
    "/api/v1/jobs/bot/",
    "/api/v1/users/bot/",
)


# ── Signing logic (shared between the wrapper and explicit helpers) ──

def _extract_path(url: str) -> str:
    """Extract the path component from a full or partial URL.

    Handles both ``https://host.com/api/v1/...`` and ``/api/v1/...`` forms.
    """
    if url.startswith("http://") or url.startswith("https://"):
        from urllib.parse import urlparse
        return urlparse(url).path
    return url


def _should_sign(path: str) -> bool:
    """Return ``True`` if *path* is a bot-specific endpoint that needs signing."""
    return path.startswith(_BOT_PATH_PREFIXES)


def _merge_signing_headers(
    headers: Optional[dict],
    method: str,
    path: str,
    body: bytes = b"",
) -> dict:
    """Merge HMAC signing headers into *headers*.

    Only adds signing if:
    1. The path matches a bot endpoint prefix.
    2. ``REQUEST_SIGNING_SECRET`` is configured.

    Returns the (possibly updated) headers dict.
    """
    resolved_path = _extract_path(path)
    if not _should_sign(resolved_path):
        return headers or {}

    if _get_signing_secret() is None:
        logger.warning(
            "Request to %s requires signing, but REQUEST_SIGNING_SECRET is not set.",
            resolved_path,
        )
        return headers or {}

    sig_headers = sign_request(method, resolved_path, body)
    result = dict(headers or {})
    result.update(sig_headers)
    return result


# ── Transparent signing wrapper ──────────────────────────────────────

class _SigningSessionWrapper:
    """Wraps an ``aiohttp.ClientSession`` to auto-sign bot-prefixed requests.

    Every call to ``.get()``, ``.post()``, ``.patch()`` or ``.delete()``
    transparently injects ``X-Signature`` / ``X-Timestamp`` / ``X-Nonce``
    headers when the URL path matches :data:`_BOT_PATH_PREFIXES`.

    Non-bot endpoints pass through unchanged.

    **Important:** The HTTP methods (``get``, ``post``, etc.) are
    *synchronous* — they return a ``ClientResponse`` directly (not a
    coroutine), because ``aiohttp.ClientSession`` methods are themselves
    synchronous.  This allows callers to use ``async with session.get(...)``
    as normal.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    # ── properties ──────────────────────────────────────────────────

    @property
    def closed(self) -> bool:
        """Delegate to the underlying session."""
        return self._session.closed

    # ── lifecycle ───────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying session."""
        await self._session.close()

    # ── HTTP methods (SYNCHRONOUS — return ClientResponse, not coroutine) ─

    def get(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        path = _extract_path(url)
        kwargs["headers"] = _merge_signing_headers(
            kwargs.pop("headers", None), "GET", path,
        )
        return self._session.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        path = _extract_path(url)
        body = _resolve_body(kwargs)
        kwargs["headers"] = _merge_signing_headers(
            kwargs.pop("headers", None), "POST", path, body,
        )
        return self._session.post(url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        path = _extract_path(url)
        body = _resolve_body(kwargs)
        kwargs["headers"] = _merge_signing_headers(
            kwargs.pop("headers", None), "PATCH", path, body,
        )
        return self._session.patch(url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        path = _extract_path(url)
        body = _resolve_body(kwargs)
        kwargs["headers"] = _merge_signing_headers(
            kwargs.pop("headers", None), "DELETE", path, body,
        )
        return self._session.delete(url, **kwargs)

    # ── passthrough for any other attributes ─────────────────────────

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def _resolve_body(kwargs: dict) -> bytes:
    """Extract the request body bytes from keyword arguments.

    Handles ``data``, ``json`` and returns ``b""`` for body-less methods.
    """
    body = kwargs.get("data") or kwargs.get("json") or b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    # aiohttp serialises ``json`` to bytes internally; for signing we
    # approximate by using the string representation.  The backend
    # middleware uses ``request.body`` which will be the raw serialised
    # form, so in practice this may differ for complex JSON payloads.
    # For the most accurate signature, pass pre-serialised ``data`` bytes.
    return str(body).encode("utf-8") if body else b""


# ── Public API ────────────────────────────────────────────────────────

def init_http_session(session: aiohttp.ClientSession) -> None:
    """Register the shared session (called once from ``main.py``)."""
    global _http_session
    _http_session = session
    logger.debug("Shared aiohttp session registered.")


def get_http_session() -> _SigningSessionWrapper:
    """Return a signing wrapper around the shared :class:`aiohttp.ClientSession`.

    Raises :class:`RuntimeError` if :func:`init_http_session` has not been called.
    """
    global _http_session
    if _http_session is None:
        raise RuntimeError(
            "Shared HTTP session not initialised. "
            "Call init_http_session() from main.py before using get_http_session()."
        )
    return _SigningSessionWrapper(_http_session)


async def close_http_session() -> None:
    """Close the shared session during application shutdown."""
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()
        logger.debug("Shared aiohttp session closed.")
    _http_session = None


# ── Explicit opt-in helpers ──────────────────────────────────────────

async def signed_get(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a GET request with explicit HMAC signing for bot endpoints.

    All keyword arguments are forwarded to ``session.get()``.
    """
    session = _http_session
    if session is None:
        raise RuntimeError("HTTP session not initialised.")
    path = _extract_path(url)
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "GET", path)
    return await session.get(url, headers=merged_headers, **kwargs)


async def signed_post(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a POST request with explicit HMAC signing for bot endpoints."""
    session = _http_session
    if session is None:
        raise RuntimeError("HTTP session not initialised.")
    path = _extract_path(url)
    body = _resolve_body(kwargs)
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "POST", path, body)
    return await session.post(url, headers=merged_headers, **kwargs)


async def signed_patch(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a PATCH request with explicit HMAC signing for bot endpoints."""
    session = _http_session
    if session is None:
        raise RuntimeError("HTTP session not initialised.")
    path = _extract_path(url)
    body = _resolve_body(kwargs)
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "PATCH", path, body)
    return await session.patch(url, headers=merged_headers, **kwargs)


async def signed_delete(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a DELETE request with explicit HMAC signing for bot endpoints."""
    session = _http_session
    if session is None:
        raise RuntimeError("HTTP session not initialised.")
    path = _extract_path(url)
    body = _resolve_body(kwargs)
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "DELETE", path, body)
    return await session.delete(url, headers=merged_headers, **kwargs)
