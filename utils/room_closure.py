"""
Room Closure & Transcript Delivery
===================================

Unified function that handles the complete room closure sequence for a
list of closure records:

    closure details fetch → closure notification (per recipient) →
    transcript entry logging (per recipient) → transcript PDF task
    (fire-and-forget) → mark closure completed (session + closure model).

Both the **agreement** and **leave** flows collect their closure id(s)
from the backend response and hand them to this single function, so the
closure + transcript handling is identical regardless of how a room was
closed.

Usage:

    from utils.room_closure import send_room_closure_and_transcript

    # From agreement flow (after signed PDF delivered to both)
    closure_ids = [winning_closure_id, *system_closure_ids]
    await send_room_closure_and_transcript(
        closure_ids=closure_ids,
        bot=interaction.client,
        headers=headers,
    )

    # From leave flow (after BotRoomLeaveView persisted + closed)
    await send_room_closure_and_transcript(
        closure_ids=[closure_id],
        bot=interaction.client,
        headers=headers,
    )
"""

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

from config import BACKEND_URL
from utils.http import get_http_session
from utils.pdf_service import create_pdf_task, build_transcript_parts
from utils.system_message_handler import handle_system_message

logger = logging.getLogger('bot.utils.room_closure')


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


async def send_room_closure_and_transcript(
    closure_ids: list[str],
    bot: commands.Bot,
    headers: dict,
) -> bool:
    """Execute the complete closure + transcript-delivery sequence.

    For each closure id:
      1. Fetch the closure details from ``GET /rooms/bot/closure-details/``
      2. Send the closure notification to both recipients (client, freelancer)
         via ``handle_system_message('interview_room_closure', ...)`` and log
         a transcript entry per recipient (sender=system, receiver=role).
      3. Create the transcript PDF task (fire-and-forget) with the exact
         parts the PDFTask model accepts.
      4. Mark the closure as completed — session ``closure_process_completed``
         and closure record ``closure_completed`` via ``POST /bot/system-close/``.

    Parameters
    ----------
    closure_ids:
        Closure record id(s) (``IRC_...``) to process.  The winning room's
        id comes first (from ``finalize-closure/``), followed by any
        system-closed room ids.
    bot:
        The Discord bot client (needed to resolve User objects for DM).
    headers:
        HTTP headers for backend API calls (must contain X-Webhook-Token).

    Returns
    -------
    ``True`` if every closure was processed successfully.
    ``False`` if any closure could not be completed.
    """
    session = get_http_session()
    all_ok = True

    for closure_id in closure_ids or []:
        ok = await _process_single_closure(closure_id, bot, headers, session)
        if not ok:
            all_ok = False

    return all_ok


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────


async def _process_single_closure(
    closure_id: str,
    bot: commands.Bot,
    headers: dict,
    session: aiohttp.ClientSession,
) -> bool:
    """Process one closure record end-to-end."""
    if not closure_id:
        return False

    # ── 1. Fetch closure details (closure → room → application → ...) ──
    details = await _fetch_closure_details(closure_id, headers, session)
    if not details:
        logger.error('Cannot process closure %s: no closure details.', closure_id)
        return False

    room_id = details.get('room_id', '')
    closure_type = details.get('closure_type', 'agreement')

    # ── 2. Closure notification + transcript entry per recipient ───────
    for role_key, name_key, discord_key in (
        ('client', 'client_name', 'client_discord_id'),
        ('freelancer', 'freelancer_name', 'freelancer_discord_id'),
    ):
        recipient_id = details.get(discord_key, '')
        if not recipient_id:
            logger.warning(
                'Closure %s: no %s discord id, skipping notification.',
                closure_id, role_key,
            )
            continue

        payload = {
            'discord_id': recipient_id,
            'closure_id': closure_id,
            'closure_type': closure_type,
            'closed_by': details.get('closed_by', ''),
            'reason': details.get('reason', ''),
            'room_id': room_id,
            'job_title': details.get('job_title', ''),
            'client_name': details.get('client_name', 'Client'),
            'freelancer_name': details.get('freelancer_name', 'Freelancer'),
        }

        # Build the embed + body text so we can log the exact text sent.
        from system_messages.interview_room_closure import (
            build_embed as build_closure_embed,
        )
        _, body_text, closure_title = build_closure_embed(payload)
        # Bold the title so the persisted transcript record renders it as a
        # styled title line in the PDF (the DM embed was already sent with
        # the plain title).
        closure_msg_text = (
            f"**{closure_title}**\n\n{body_text}"
            if body_text
            else f"**{closure_title}**"
        )

        # 2a. Send the closure DM (built + delivered via the handler).
        delivered = await handle_system_message(
            'interview_room_closure',
            payload,
            bot,
        )

        # 2b. Save the system message + transcript entry per recipient.
        await _log_closure_message(
            room_id=room_id,
            receiver=role_key,
            msg_text=closure_msg_text,
        )

        if not delivered:
            logger.warning(
                'Closure %s: DM delivery to %s (%s) failed.',
                closure_id, role_key, recipient_id,
            )

    # ── 3. Create transcript PDF task (fire-and-forget) ───────────────
    await _request_transcript(
        room_id=room_id,
        client_discord_id=details.get('client_discord_id', ''),
        freelancer_discord_id=details.get('freelancer_discord_id', ''),
    )

    # ── 4. Mark closure completed (session + closure record) ───────────
    await _mark_closure_completed(closure_id, room_id, headers, session)

    return True


async def _fetch_closure_details(
    closure_id: str,
    headers: dict,
    session: aiohttp.ClientSession,
) -> dict | None:
    """Fetch closure details from the backend ``closure-details/`` endpoint.

    Recipients are resolved backend-side through strict model traversal
    (closure → room → application → freelancer/job → profiles → users).
    """
    url = f'{BACKEND_URL}rooms/bot/closure-details/'
    try:
        async with session.get(
            url,
            params={'closure_id': closure_id},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    return data
                logger.warning('closure-details returned: %s', data.get('error', ''))
            else:
                logger.warning(
                    'Fetch closure details returned %s for closure %s',
                    resp.status, closure_id,
                )
    except Exception:
        logger.exception('Failed to fetch closure details for %s', closure_id)
    return None


async def _log_closure_message(
    room_id: str,
    receiver: str,
    msg_text: str,
) -> None:
    """Fire-and-forget log of the closure system message delivery.

    Reuses ``CreateRooms._log_system_message`` (POST /bot/log-system-message/)
    so the record is created via ``InterviewRoomMsg.create_and_log`` with
    sender='system', receiver set per recipient, and the transcript entry
    appended server-side.
    """
    from cogs.Rooms.create_rooms import CreateRooms  # lazy import to avoid cycles
    try:
        await CreateRooms._log_system_message(
            room_id=room_id,
            msg_type='closure',
            flags={},
            msg_text=msg_text,
            receiver=receiver,
        )
    except Exception:
        logger.exception('Failed to log closure message for room %s', room_id)


async def _request_transcript(
    room_id: str,
    client_discord_id: str,
    freelancer_discord_id: str,
) -> None:
    """Create the transcript PDF task (fire-and-forget).

    Parts hold only the fields the PDFTask model accepts: ``part_id``,
    ``viewer_role``, ``recipient_discord_id``, ``filename``.
    """
    parts = build_transcript_parts(
        client_discord_id=client_discord_id,
        freelancer_discord_id=freelancer_discord_id,
    )

    task_id = await create_pdf_task(
        task_type='transcript',
        room_id=room_id,
        requester_discord_id=client_discord_id or freelancer_discord_id,
        parts=parts,
    )

    if task_id:
        logger.info('Transcript task %s created for room %s', task_id, room_id)
    else:
        logger.error('Failed to create transcript PDF task for room %s', room_id)


async def _mark_closure_completed(
    closure_id: str,
    room_id: str,
    headers: dict,
    session: aiohttp.ClientSession,
) -> None:
    """Call ``/bot/system-close/`` to set ``closure_process_completed`` on the
    session and ``closure_completed`` on the closure record."""
    try:
        system_close_url = f'{BACKEND_URL}rooms/bot/system-close/'
        async with session.post(
            system_close_url,
            json={'room_id': room_id, 'closure_id': closure_id},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                logger.info(
                    'Closure %s (room %s): closure process completed',
                    closure_id, room_id,
                )
            else:
                logger.warning(
                    'system-close returned %s for closure %s (room %s)',
                    resp.status, closure_id, room_id,
                )
    except Exception:
        logger.exception(
            'Failed to mark closure completed for %s (room %s)',
            closure_id, room_id,
        )
