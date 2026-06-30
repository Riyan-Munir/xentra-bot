"""
Shared aiohttp.ClientSession singleton for all bot HTTP requests.

Replaces the per-request pattern of creating ~48 separate TCP connections
with a single persistent connection pool managed at the application level.

Provides automatic HMAC request signing for bot→backend communication.
"""

import aiohttp
import logging
from typing import Optional

from utils.request_signing import sign_request, _get_signing_secret

logger = logging.getLogger('bot.http')

_http_session: aiohttp.ClientSession | None = None


def init_http_session(session: aiohttp.ClientSession) -> None:
    """Register the shared session (called once from main.py)."""
    global _http_session
    _http_session = session
    logger.debug("Shared aiohttp session registered.")


def get_http_session() -> aiohttp.ClientSession:
    """Return the shared aiohttp.ClientSession.

    Raises RuntimeError if init_http_session() has not been called.
    """
    global _http_session
    if _http_session is None:
        raise RuntimeError(
            "Shared HTTP session not initialised. "
            "Call init_http_session() from main.py before using get_http_session()."
        )
    return _http_session


async def close_http_session() -> None:
    """Close the shared session during application shutdown."""
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()
        logger.debug("Shared aiohttp session closed.")
    _http_session = None


# ── Auto-signing helpers ──────────────────────────────────────────────

_BOT_PATH_PREFIXES = (
    "/api/v1/rooms/bot/",
    "/api/v1/jobs/bot/",
    "/api/v1/users/bot/",
)


def _extract_path(url: str) -> str:
    """Extract the path component from a full or partial URL.

    Handles both ``https://host.com/api/v1/...`` and ``/api/v1/...`` forms.
    """
    if url.startswith("http://") or url.startswith("https://"):
        from urllib.parse import urlparse
        return urlparse(url).path
    return url


def _should_sign(path: str) -> bool:
    """Return ``True`` if the path is a bot-specific endpoint that needs signing."""
    return path.startswith(_BOT_PATH_PREFIXES)


def _merge_signing_headers(
    headers: Optional[dict],
    method: str,
    path: str,
    body: bytes = b"",
) -> dict:
    """Merge HMAC signing headers into the request headers.

    Only adds signing if:
    1. The path matches a bot endpoint prefix.
    2. REQUEST_SIGNING_SECRET is configured.

    Returns the (possibly updated) headers dict.
    """
    resolved_path = _extract_path(path)
    if not _should_sign(resolved_path):
        return headers or {}

    if _get_signing_secret() is None:
        # Secret not configured — log a warning and continue unsigned.
        logger.warning(
            "Request to %s requires signing, but REQUEST_SIGNING_SECRET is not set.",
            resolved_path,
        )
        return headers or {}

    sig_headers = sign_request(method, resolved_path, body)
    result = dict(headers or {})
    result.update(sig_headers)
    return result


async def signed_get(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a GET request with automatic HMAC signing for bot endpoints.

    All keyword arguments are forwarded to ``session.get()``.
    """
    session = get_http_session()
    path = _extract_path(url)
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "GET", path)
    return await session.get(url, headers=merged_headers, **kwargs)


async def signed_post(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a POST request with automatic HMAC signing for bot endpoints.

    All keyword arguments are forwarded to ``session.post()``.
    """
    session = get_http_session()
    path = _extract_path(url)
    body = kwargs.get("data") or kwargs.get("json") or b""
    if not isinstance(body, bytes):
        body = str(body).encode("utf-8") if not isinstance(body, (str, bytes)) else body
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "POST", path, body if isinstance(body, bytes) else b"")
    return await session.post(url, headers=merged_headers, **kwargs)


async def signed_patch(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a PATCH request with automatic HMAC signing for bot endpoints."""
    session = get_http_session()
    path = _extract_path(url)
    body = kwargs.get("data") or kwargs.get("json") or b""
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "PATCH", path, body if isinstance(body, bytes) else b"")
    return await session.patch(url, headers=merged_headers, **kwargs)


async def signed_delete(url: str, **kwargs) -> aiohttp.ClientResponse:
    """Perform a DELETE request with automatic HMAC signing for bot endpoints."""
    session = get_http_session()
    path = _extract_path(url)
    body = kwargs.get("data") or b""
    merged_headers = _merge_signing_headers(kwargs.pop("headers", None), "DELETE", path, body if isinstance(body, bytes) else b"")
    return await session.delete(url, headers=merged_headers, **kwargs)
