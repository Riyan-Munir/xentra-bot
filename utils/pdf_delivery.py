"""
PDF delivery utility for the bot webhook server.

Handles PDF-ready notifications from the PDF Generator (``POST
/webhook/pdf-result``).  The bot fetches the task detail (``task_type``,
``room_id``, ``parts``, ``results``) from the backend, then delivers each
generated PDF to the correct user via DM, attaching the matching room-system
message, and persisting an ``InterviewRoomMsg`` system record (plus the
transcript entry) through the shared ``/rooms/bot/log-system-message/``
backend endpoint.

Per-part processing
-------------------
A task carries 1 or 2 parts (``parts: [{part_id, viewer_role,
recipient_discord_id, filename}]``) with one generated PDF per part under
``results`` (``results: [{part_id, pdf_bytes}]``).  Each part is processed
**independently**:

* Non-DB work (receiver resolution, embed build, DM file send) runs in
  parallel across parts (``asyncio.gather``) because it touches no shared
  DB state.
* DB writes (``log-system-message``) are performed **serially, one part at
  a time**, to respect database atomicity.

Shared task data (``room_id``, ``task_type``, ``job_title``) is fetched
once and reused by every part.

Status handling
---------------
On success the task is marked ``completed`` on the backend.  On failure the
task is returned to ``generated`` (the stored results are kept, so the PDFs
can be re-delivered later without re-generation) — no retry_count is
incremented.
"""

import asyncio
import base64
import io
import logging

import discord

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.http import get_http_session

logger = logging.getLogger('bot.pdf_delivery')

# ═══════════════════════════════════════════════════════════════════════
# Task-type → system-message mapping
# ═══════════════════════════════════════════════════════════════════════


def _system_message_builder(task_type: str):
    """Return the ``build_embed`` callable for a PDF ``task_type``."""
    from system_messages.interview_room_transcript import (
        build_embed as build_transcript_embed,
    )
    from system_messages.interview_room_review_agreement import (
        build_embed as build_review_agreement_embed,
    )
    from system_messages.interview_room_signed_agreement import (
        build_embed as build_signed_agreement_embed,
    )

    return {
        'transcript': build_transcript_embed,
        'agreement': build_review_agreement_embed,
        'signed_agreement': build_signed_agreement_embed,
    }.get(task_type)


# Short label logged on the InterviewRoomMsg (msg_type column value).
_PDF_MSG_TYPES = {
    'transcript': 'transcript',
    'agreement': 'agreement_review',
    'signed_agreement': 'signed_agreement',
}


# ═══════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════


async def deliver_pdf_result(
    task_id: str,
    bot,
) -> None:
    """Deliver every generated PDF part of a task to its receiver.

    Fetches the task detail (``task_type``, ``room_id``, ``parts``,
    ``results``) from the backend, then delivers each part via DM.

    Parameters
    ----------
    task_id:
        The backend PDF task identifier (``PDF_XXXXXXXX``).
    bot:
        The Discord bot client (used to resolve ``User`` objects for DMs).

    On success the task is marked ``completed`` on the backend.  On failure
    the task is returned to ``generated`` so it can be re-delivered later
    (the stored results are kept); no retry_count is incremented.
    """
    session = get_http_session()

    # ── 1. Fetch shared task context (once) ────────────────────────────
    task_detail = await _fetch_task_detail(task_id, session)
    if not task_detail:
        logger.warning('Could not fetch task detail for %s — delivery skipped', task_id)
        return

    task_type = task_detail.get('task_type', '')
    room_id = task_detail.get('room_id', '')
    part_map = {p['part_id']: p for p in task_detail.get('parts', [])}

    # Generated PDFs arrive under "results"; "result" is kept as a legacy
    # fallback for older tasks.
    results = task_detail.get('results') or task_detail.get('result') or []
    if not results:
        logger.warning('No generated results for task %s — delivery skipped', task_id)
        return

    # Shared room context: job_title + fallback Discord IDs.  Single data
    # (one room) is fetched once and copied for every part.
    room_ctx = {}
    if room_id:
        room_ctx = await _fetch_room_context(room_id, session)

    # ── 2. Parallel non-DB work per part: resolve + build + send ───────
    # No shared DB state is touched here, so all parts run concurrently.
    outcomes = await asyncio.gather(
        *[
            _prepare_and_send_part(
                result,
                part_map.get(result.get('part_id')),
                task_type,
                room_id,
                room_ctx,
                bot,
            )
            for result in results
        ],
        return_exceptions=True,
    )

    all_succeeded = True
    prepared_parts = []
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            logger.exception('Unexpected error delivering a PDF part', exc_info=outcome)
            all_succeeded = False
            prepared_parts.append(None)
        elif outcome is None:
            all_succeeded = False
            prepared_parts.append(None)
        else:
            prepared_parts.append(outcome)

    # ── 3. Serial DB writes per part (one after another) ───────────────
    # log-system-message performs a DB write (InterviewRoomMsg + transcript
    # append); run strictly sequentially to respect database atomicity.
    for prepared in prepared_parts:
        if not prepared:
            continue
        await _log_pdf_delivery(room_id, task_type, prepared, session)

    # ── 4. Mark completed on success; return to generated on failure ──
    # On failure the stored results are kept and the task is moved back to
    # "generated" so a later cycle can re-deliver without re-generating.
    # No retry_count is incremented here.
    if all_succeeded:
        await _patch_task_status(task_id, 'completed')
        logger.info('Task %s marked completed', task_id)
    else:
        await _patch_task_status(task_id, 'generated')
        logger.warning('Task %s delivery failed — returned to generated', task_id)


# ═══════════════════════════════════════════════════════════════════════
# Per-part non-DB work (runs in parallel across parts)
# ═══════════════════════════════════════════════════════════════════════


async def _prepare_and_send_part(
    result: dict,
    part_info: dict | None,
    task_type: str,
    room_id: str,
    room_ctx: dict,
    bot,
) -> dict | None:
    """Resolve the receiver, build the system message, and send the DM.

    Returns a prepared dict ``{receiver_role, filename, title, body}`` on
    success, or ``None`` when this part cannot be delivered.
    """
    if not part_info:
        logger.warning(
            'Unknown part_id %s in task (room=%s)',
            result.get('part_id', ''), room_id,
        )
        return None

    viewer_role = part_info.get('viewer_role', '')
    filename = part_info.get('filename', 'Document.pdf')

    # ── 1. Resolve receiver Discord ID ─────────────────────────────────
    discord_id = part_info.get('recipient_discord_id', '') or ''
    if not discord_id:
        discord_id = room_ctx.get(f'{viewer_role}_discord_id', '') or ''
    if not discord_id:
        logger.warning(
            'No Discord ID for part %s (viewer_role=%s, room=%s); delivery skipped',
            result.get('part_id', ''), viewer_role, room_id,
        )
        return None

    # ── 2. Decode PDF bytes ────────────────────────────────────────────
    pdf_b64 = result.get('pdf_bytes', '')
    try:
        pdf_bytes = base64.b64decode(pdf_b64)
    except Exception:
        logger.error(
            'Invalid base64 PDF for part %s in room %s',
            result.get('part_id', ''), room_id,
        )
        return None

    # ── 3. Build the room-system message for this PDF type ─────────────
    built = _build_pdf_embed(task_type, discord_id, room_id, room_ctx.get('job_title', ''))
    if built is None:
        logger.warning(
            'No system message builder for task_type=%s (room=%s)',
            task_type, room_id,
        )
        return None

    embed, msg_title, msg_body = built

    # ── 4. Send the DM with the attached PDF ───────────────────────────
    sent = await _send_dm_with_file(bot, discord_id, filename, embed, pdf_bytes)
    if not sent:
        return None

    return {
        'receiver_role': viewer_role,
        'filename': filename,
        'title': msg_title,
        'body': msg_body,
    }


def _build_pdf_embed(
    task_type: str,
    discord_id: str,
    room_id: str,
    job_title: str,
) -> tuple[discord.Embed, str, str] | None:
    """Build the room-system embed matching the PDF ``task_type``.

    Returns ``(embed, title, headerless_body)`` or ``None`` when no
    builder exists for the task type.  The embed carries the Room / Job
    headers for the DM; the title + headerless body are returned so the
    caller can persist ``"Title\n\nBody"`` without the headers.
    """
    builder = _system_message_builder(task_type)
    if builder is None:
        return None

    data = {'discord_id': discord_id}
    if room_id:
        data['room_id'] = room_id
    if job_title:
        data['job_title'] = job_title
    built = builder(data)

    if isinstance(built, tuple) and len(built) >= 2:
        embed, title_or_body = built[0], built[1]
        if len(built) >= 3:
            return embed, built[2], built[1]
        # (embed, title) — embed-only builders now return the title; the
        # headerless body is recovered by removing the headers from the
        # embed description (they are prepended by create_room_embed).
        title = title_or_body
        body = _strip_room_headers(embed.description or '')
        return embed, title, body

    embed = built
    return embed, embed.title or '', _strip_room_headers(embed.description or '')


def _strip_room_headers(description: str) -> str:
    """Remove the Room / Job header block prepended by ``create_room_embed``.

    The headers are the leading ``> ***Room: ...***`` / ``> ***Job: ...***``
    lines followed by a blank line.  They are only for the DM — persisted
    transcript records must store the headerless body.
    """
    if not description:
        return ''
    lines = description.split('\n')
    idx = 0
    while idx < len(lines) and (
        lines[idx].startswith('> ***Room:') or lines[idx].startswith('> ***Job:')
    ):
        idx += 1
    # Skip the blank separator line if present
    if idx < len(lines) and lines[idx].strip() == '':
        idx += 1
    return '\n'.join(lines[idx:])


async def _send_dm_with_file(
    bot,
    discord_id: str,
    filename: str,
    embed: discord.Embed,
    pdf_bytes: bytes,
) -> bool:
    """Send the embed with the PDF attached via DM.

    Returns ``True`` on success; logs failures without raising.
    """
    try:
        user = bot.get_user(int(discord_id))
        if not user:
            user = await bot.fetch_user(int(discord_id))
        await user.send(
            embed=embed,
            file=discord.File(io.BytesIO(pdf_bytes), filename=filename),
        )
        logger.info('PDF delivered to (%s) filename=%s', discord_id, filename)
        return True
    except discord.Forbidden:
        logger.warning(
            'Cannot DM (%s), DMs may be disabled. filename=%s',
            discord_id, filename,
        )
    except Exception:
        logger.exception('Failed to send PDF to (%s) filename=%s', discord_id, filename)
    return False


# ═══════════════════════════════════════════════════════════════════════
# Serial DB write per part
# ═══════════════════════════════════════════════════════════════════════


async def _log_pdf_delivery(
    room_id: str,
    task_type: str,
    prepared: dict,
    session,
) -> None:
    """Persist the system message + transcript entry via the backend.

    This is a **single** DB write (InterviewRoomMsg create + transcript
    append) performed by the shared ``log-system-message`` endpoint — the
    same pattern the room's starting system messages use.
    """
    if not room_id:
        logger.warning('Cannot log PDF delivery without a room_id')
        return

    url = f'{BACKEND_URL}rooms/bot/log-system-message/'
    msg_type = _PDF_MSG_TYPES.get(task_type, task_type)
    title = prepared.get('title', '')
    body = prepared.get('body', '')
    # Discord embed titles are plain text, so the title only reaches the
    # transcript record here.  Bold it so the PDF parser renders it as a
    # styled title line (the DM embed was already built separately).
    styled_title = f'**{title}**' if title else ''
    msg_text = f'{styled_title}\n\n{body}'.strip() if body else styled_title

    payload = {
        'room_id': room_id,
        'msg_type': msg_type,
        'flags': {},
        'msg_text': msg_text,
        'receiver': prepared.get('receiver_role', ''),
        'attachment_metadata': prepared.get('filename', ''),
    }

    try:
        import aiohttp
        async with session.post(
            url,
            json=payload,
            headers={'X-Webhook-Token': WEBHOOK_SECRET},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                logger.warning(
                    'Failed to log %s delivery for room %s: %s',
                    msg_type, room_id, await resp.text(),
                )
            else:
                logger.info(
                    'Logged %s delivery for room %s (receiver=%s)',
                    msg_type, room_id, prepared.get('receiver_role', ''),
                )
    except Exception:
        logger.exception('Failed to log %s delivery for room %s', msg_type, room_id)


# ═══════════════════════════════════════════════════════════════════════
# Backend helpers
# ═══════════════════════════════════════════════════════════════════════


async def _fetch_task_detail(task_id: str, session) -> dict | None:
    """Fetch the PDF task detail (task_type, room_id, parts)."""
    url = f'{BACKEND_URL}pdf-tasks/bot/{task_id}/'
    try:
        import aiohttp
        async with session.get(
            url,
            headers={'X-Webhook-Token': WEBHOOK_SECRET},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.error('Failed to fetch task %s: %s', task_id, resp.status)
                return None
            return await resp.json()
    except Exception:
        logger.exception('Failed to reach backend for task %s', task_id)
        return None


async def _fetch_room_context(room_id: str, session) -> dict:
    """Fetch room participant info + job title from the backend.

    Provides the fallback Discord IDs (client/freelancer) and the job title
    used for the system-message headers.
    """
    url = f'{BACKEND_URL}rooms/bot/fetch-transcript-data/'
    try:
        import aiohttp
        async with session.get(
            url,
            params={'room_id': room_id},
            headers={'X-Webhook-Token': WEBHOOK_SECRET},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning(
                'Fetch transcript data returned %s for room %s',
                resp.status, room_id,
            )
    except Exception:
        logger.exception('Failed to fetch transcript data for room %s', room_id)
    return {}


async def _patch_task_status(
    task_id: str, status: str, error_message: str = '', session=None,
) -> None:
    """PATCH the PDF task status on the backend (fire-and-forget)."""
    url = f'{BACKEND_URL}pdf-tasks/bot/{task_id}/status/'
    payload = {'status': status}
    if error_message:
        payload['error_message'] = error_message

    try:
        import aiohttp
        session = session or get_http_session()
        async with session.patch(
            url,
            json=payload,
            headers={'X-Webhook-Token': WEBHOOK_SECRET},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning(
                    'PATCH status %s for task %s returned %s: %s',
                    status, task_id, resp.status, body[:500],
                )
    except Exception:
        logger.exception(
            'Failed to PATCH status %s for task %s', status, task_id,
        )
