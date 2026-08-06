"""
PDF Service — thin wrapper for fire-and-forget PDF task creation.

Replaces all local PDF generation (reportlab, pypdf, Pillow) with a call
to the remote PDF Generator microservice.  The bot never touches PDF bytes
anymore — it creates a task on the backend, hands the task_id to the
generator, and forgets.  The generator calls back to the bot's webhook
when PDFs are ready.

Flow
----
1. Build the parts list (who gets what PDF, with what viewer role).
2. POST to backend ``/api/v1/pdf-tasks/create/`` → get ``task_id``.
3. POST to PDF Generator ``/generate { task_id }`` → fire-and-forget.
4. Return ``task_id`` immediately.
"""

import logging
from typing import Any

from config import BACKEND_URL, PDF_SERVICE_URL, PDF_SERVICE_SECRET
from utils.http import get_http_session

logger = logging.getLogger('bot.pdf_service')

# ── Timeouts ──────────────────────────────────────────────────────────
_BACKEND_TIMEOUT = 15  # seconds for backend create call
_GENERATOR_TIMEOUT = 10  # seconds for PDF generator /generate call


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

async def create_pdf_task(
    task_type: str,
    room_id: str,
    requester_discord_id: str,
    parts: list[dict],
    room_type: str = 'interview',
) -> str | None:
    """
    Create a PDF task on the backend and hand it to the PDF Generator.

    Parameters
    ----------
    task_type:
        ``'transcript'``, ``'agreement'``, or ``'signed_agreement'``.
    room_id:
        Interview room identifier.
    requester_discord_id:
        Discord ID of the user who triggered the request.
    parts:
        List of 1-2 delivery part dicts.  Each part holds only:
        ``part_id``, ``viewer_role``, ``recipient_discord_id``, ``filename``.
    room_type:
        ``'interview'`` or ``'job'``.  Defaults to ``'interview'``.

    Returns
    -------
    The ``task_id`` string (``PDF_XXXXXXXX``) on success, or ``None`` on failure.
    """
    session = get_http_session()

    # ── 1. Create task on backend ─────────────────────────────────────
    create_url = f'{BACKEND_URL}pdf-tasks/bot/create/'
    create_body = {
        'task_type': task_type,
        'room_type': room_type,
        'room_id': room_id,
        'requester_discord_id': requester_discord_id,
        'parts': parts,
    }

    try:
        import aiohttp
        from config import WEBHOOK_SECRET
        async with session.post(
            create_url,
            json=create_body,
            headers={'X-Webhook-Token': WEBHOOK_SECRET},
            timeout=aiohttp.ClientTimeout(total=_BACKEND_TIMEOUT),
        ) as resp:
            if resp.status != 201:
                body = await resp.json()
                logger.error(
                    'PDF task creation failed: %s — %s',
                    resp.status, body.get('error', ''),
                )
                return None
            data = await resp.json()
            task_id = data.get('task_id', '')
            if not task_id:
                logger.error('PDF task creation returned no task_id')
                return None
    except Exception:
        logger.exception('Failed to reach backend for PDF task creation')
        return None

    # ── 2. Hand task_id to PDF Generator ──────────────────────────────
    await _dispatch_to_generator(task_id)

    logger.info(
        'PDF task %s created (type=%s, room=%s, parts=%d)',
        task_id, task_type, room_id, len(parts),
    )
    return task_id


# ═══════════════════════════════════════════════════════════════════════
# Parts builders
# ═══════════════════════════════════════════════════════════════════════

def build_transcript_parts(
    client_discord_id: str,
    freelancer_discord_id: str,
) -> list[dict]:
    """Build parts list for a 2-party transcript task (each with its own view)."""
    return [
        {
            'part_id': 'p1',
            'viewer_role': 'client',
            'recipient_discord_id': client_discord_id,
            'filename': 'Room-Transcript.pdf',
        },
        {
            'part_id': 'p2',
            'viewer_role': 'freelancer',
            'recipient_discord_id': freelancer_discord_id,
            'filename': 'Room-Transcript.pdf',
        },
    ]


def build_single_transcript_parts(
    recipient_discord_id: str,
    viewer_role: str,
) -> list[dict]:
    """Build parts list for a single-party transcript (on-demand /interview transcript)."""
    return [
        {
            'part_id': 'p1',
            'viewer_role': viewer_role,
            'recipient_discord_id': recipient_discord_id,
            'filename': 'Room-Transcript.pdf',
        },
    ]


def build_agreement_parts(
    client_discord_id: str,
    freelancer_discord_id: str,
) -> list[dict]:
    """
    Build parts list for a signed agreement task (2 recipients, 1 PDF).

    The PDF Generator generates a single PDF and duplicates it to satisfy
    both parts.
    """
    return [
        {
            'part_id': 'p1',
            'viewer_role': 'client',
            'recipient_discord_id': client_discord_id,
            'filename': 'Job-Agreement.pdf',
        },
        {
            'part_id': 'p2',
            'viewer_role': 'freelancer',
            'recipient_discord_id': freelancer_discord_id,
            'filename': 'Job-Agreement.pdf',
        },
    ]


def build_single_agreement_parts(
    recipient_discord_id: str,
    viewer_role: str,
) -> list[dict]:
    """Build parts list for a single-party agreement review PDF."""
    return [
        {
            'part_id': 'p1',
            'viewer_role': viewer_role,
            'recipient_discord_id': recipient_discord_id,
            'filename': 'Job-Agreement.pdf',
        },
    ]


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════

async def _dispatch_to_generator(task_id: str) -> None:
    """Send task_id to the PDF Generator's /generate endpoint (fire-and-forget).

    The PDF_SERVICE_URL must be the service root (e.g. https://host.hf.space),
    NOT a sub-path — /generate is appended automatically.
    """
    if not PDF_SERVICE_URL:
        logger.warning('PDF_SERVICE_URL not configured, skipping generator dispatch')
        return

    base = PDF_SERVICE_URL.rstrip('/')
    # Sanity-check: warn if the base URL already ends with a path component
    # that would make the final URL wrong (e.g. ".../api" → ".../api/generate").
    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(base)
    if _parsed.path not in ('', '/'):
        logger.warning(
            'PDF_SERVICE_URL appears to have a path component (%r). '
            'The /generate endpoint will be appended to the full URL: %s/generate. '
            'If requests fail with 404, clear the path from PDF_SERVICE_URL.',
            _parsed.path, base,
        )

    session = get_http_session()
    generate_url = f'{base}/generate'

    try:
        import aiohttp
        async with session.post(
            generate_url,
            json={'task_id': task_id},
            timeout=aiohttp.ClientTimeout(total=_GENERATOR_TIMEOUT),
        ) as resp:
            if resp.status == 202:
                logger.info('PDF Generator accepted task %s', task_id)
            else:
                try:
                    body = await resp.json()
                except Exception:
                    body = await resp.text()
                logger.warning(
                    'PDF Generator returned %s for task %s: %s '
                    '(URL: %s — verify PDF_SERVICE_URL points to the service root)',
                    resp.status, task_id, body, generate_url,
                )
    except Exception:
        logger.exception(
            'Failed to reach PDF Generator for task %s (URL: %s)',
            task_id, generate_url,
        )

