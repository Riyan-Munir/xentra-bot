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
    payload: dict | None = None,
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
        List of delivery part dicts.  Each must contain at minimum:
        ``part_id``, ``viewer_role``, ``recipient_discord_id``,
        ``recipient_name``, ``embed_title``, ``embed_description``, ``filename``.
    payload:
        Agreement data (for agreement types).  ``None`` for transcripts.

    Returns
    -------
    The ``task_id`` string (``PDF_XXXXXXXX``) on success, or ``None`` on failure.
    """
    session = get_http_session()

    # ── 1. Create task on backend ─────────────────────────────────────
    create_url = f'{BACKEND_URL}pdf-tasks/bot/create/'
    create_body = {
        'task_type': task_type,
        'room_id': room_id,
        'requester_discord_id': requester_discord_id,
        'parts': parts,
        'payload': payload or {},
    }

    try:
        import aiohttp
        async with session.post(
            create_url,
            json=create_body,
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
    client_name: str,
    freelancer_discord_id: str,
    freelancer_name: str,
    room_id: str,
) -> list[dict]:
    """Build parts list for a transcript task (both parties get their own view)."""
    return [
        {
            'part_id': 'p1',
            'viewer_role': 'client',
            'recipient_discord_id': client_discord_id,
            'recipient_name': client_name,
            'embed_title': 'Room Transcript',
            'embed_description': (
                f'Review the attached transcript of your interview room '
                f'**{room_id}**.\n\n'
                'This document records all correspondence exchanged '
                'during the interview phase.'
            ),
            'filename': 'Room-Transcript.pdf',
        },
        {
            'part_id': 'p2',
            'viewer_role': 'freelancer',
            'recipient_discord_id': freelancer_discord_id,
            'recipient_name': freelancer_name,
            'embed_title': 'Room Transcript',
            'embed_description': (
                f'Review the attached transcript of your interview room '
                f'**{room_id}**.\n\n'
                'This document records all correspondence exchanged '
                'during the interview phase.'
            ),
            'filename': 'Room-Transcript.pdf',
        },
    ]


def build_single_transcript_parts(
    recipient_discord_id: str,
    recipient_name: str,
    viewer_role: str,
    room_id: str,
) -> list[dict]:
    """Build parts list for a single-party transcript (on-demand /interview transcript)."""
    return [
        {
            'part_id': 'p1',
            'viewer_role': viewer_role,
            'recipient_discord_id': recipient_discord_id,
            'recipient_name': recipient_name,
            'embed_title': 'Room Transcript',
            'embed_description': (
                f'Review the attached transcript of your interview room '
                f'**{room_id}**.\n\n'
                'This document records all correspondence exchanged '
                'during the interview phase.'
            ),
            'filename': 'Room-Transcript.pdf',
        },
    ]


def build_agreement_parts(
    client_discord_id: str,
    client_name: str,
    freelancer_discord_id: str,
    freelancer_name: str,
) -> list[dict]:
    """Build parts list for a signed agreement task (both parties get the same PDF)."""
    return [
        {
            'part_id': 'p1',
            'viewer_role': 'client',
            'recipient_discord_id': client_discord_id,
            'recipient_name': client_name,
            'embed_title': 'Job Agreement',
            'embed_description': (
                'Review the attached Job Agreement document.'
            ),
            'filename': 'Job-Agreement.pdf',
        },
        {
            'part_id': 'p2',
            'viewer_role': 'freelancer',
            'recipient_discord_id': freelancer_discord_id,
            'recipient_name': freelancer_name,
            'embed_title': 'Job Agreement',
            'embed_description': (
                'Review the attached Job Agreement document.'
            ),
            'filename': 'Job-Agreement.pdf',
        },
    ]


def build_single_agreement_parts(
    recipient_discord_id: str,
    recipient_name: str,
    viewer_role: str,
) -> list[dict]:
    """Build parts list for a single-party agreement review PDF."""
    return [
        {
            'part_id': 'p1',
            'viewer_role': viewer_role,
            'recipient_discord_id': recipient_discord_id,
            'recipient_name': recipient_name,
            'embed_title': 'Job Agreement',
            'embed_description': (
                'Review the attached Job Agreement document.'
            ),
            'filename': 'Job-Agreement.pdf',
        },
    ]


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════

async def _dispatch_to_generator(task_id: str) -> None:
    """Send task_id to the PDF Generator's /generate endpoint (fire-and-forget)."""
    if not PDF_SERVICE_URL:
        logger.warning('PDF_SERVICE_URL not configured, skipping generator dispatch')
        return

    session = get_http_session()
    generate_url = f'{PDF_SERVICE_URL.rstrip("/")}/generate'

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
                body = await resp.json()
                logger.warning(
                    'PDF Generator returned %s for task %s: %s',
                    resp.status, task_id, body,
                )
    except Exception:
        logger.exception(
            'Failed to reach PDF Generator for task %s', task_id,
        )
