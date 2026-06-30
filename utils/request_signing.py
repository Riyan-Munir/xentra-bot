"""
HMAC-SHA256 request signing for bot-to-backend communication.

Mirrors the algorithm in ``backend/apps/common/signing.py`` so that the
backend's ``RequestSigningMiddleware`` can validate bot requests.

Usage::

    from utils.request_signing import sign_request

    headers = sign_request("POST", "/api/v1/rooms/bot/quota-check/", b'{"key":"val"}')
    # headers == {"X-Timestamp": "...", "X-Nonce": "...", "X-Signature": "..."}
"""

import hashlib
import hmac
import logging
import time
from typing import Optional

logger = logging.getLogger("bot.request_signing")


def _get_signing_secret() -> Optional[bytes]:
    """Return the HMAC signing key, or None if not configured."""
    try:
        from config import REQUEST_SIGNING_SECRET
        if not REQUEST_SIGNING_SECRET:
            return None
        if isinstance(REQUEST_SIGNING_SECRET, str):
            return REQUEST_SIGNING_SECRET.encode("utf-8")
        return REQUEST_SIGNING_SECRET
    except (ImportError, AttributeError):
        return None


def _compute_signature(
    method: str,
    path: str,
    body: bytes,
    timestamp: str,
    nonce: str,
    *,
    secret: Optional[bytes] = None,
) -> str:
    """
    Compute HMAC-SHA256 signature — identical algorithm to backend's
    ``_compute_signature()`` in ``common/signing.py``.

    Canonical form::

        HMAC-SHA256(secret, method | '\\n' | path | '\\n' | body_hash | '\\n' | timestamp | '\\n' | nonce)

    where ``body_hash`` is SHA-256 of the raw request body.
    """
    if secret is None:
        secret = _get_signing_secret()
        if secret is None:
            raise RuntimeError(
                "REQUEST_SIGNING_SECRET is not configured. "
                "Set it in the bot environment."
            )

    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join([method.upper(), path, body_hash, timestamp, nonce])

    return hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_request(
    method: str,
    path: str,
    body: bytes = b"",
    *,
    secret: Optional[bytes] = None,
) -> dict:
    """
    Build HMAC signing headers for an outgoing request.

    Parameters
    ----------
    method : str
        HTTP method (GET, POST, PATCH, DELETE, …).
    path : str
        URL **path** only (e.g. ``/api/v1/rooms/bot/quota-check/``).
        Do NOT include the scheme/host — the backend validates only ``request.path``.
    body : bytes
        Raw request body bytes (default ``b""`` for GET requests).
    secret : bytes, optional
        Override the signing secret (defaults to ``config.REQUEST_SIGNING_SECRET``).

    Returns
    -------
    dict
        Headers to merge into the request:
        ``{"X-Timestamp": str, "X-Nonce": str, "X-Signature": str}``
    """
    timestamp = str(int(time.time()))
    nonce = hashlib.sha256(
        f"{timestamp}:{path}:{time.monotonic_ns()}".encode()
    ).hexdigest()[:32]

    signature = _compute_signature(
        method, path, body, timestamp, nonce, secret=secret
    )

    return {
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


__all__ = [
    "sign_request",
    "_compute_signature",
    "_get_signing_secret",
]
