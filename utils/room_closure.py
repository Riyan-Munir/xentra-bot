"""
Room Closure & Transcript Delivery
===================================

Unified function that handles the complete room closure sequence:
closure notification → transcript generation (viewer-specific PDFs) →
transcript delivery → finalise closure.

Both the **agreement** and **leave** flows call this single function so
that transcript formatting, embed messages, and delivery logic are
identical regardless of how the room was closed.

Usage:

    from utils.room_closure import send_room_closure_and_transcript

    # From agreement flow (after signed PDF delivered to both)
    await send_room_closure_and_transcript(
        room_id='...',
        bot=interaction.client,
        headers=headers,
        closure_type='agreement',
        agreement_id='...',
    )

    # From leave flow (after BotRoomLeaveView persisted + closed)
    await send_room_closure_and_transcript(
        room_id='...',
        bot=interaction.client,
        headers=headers,
        closure_type='leave',
        leave_reason=reason_text,
        left_by='client' | 'freelancer',
    )
"""

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

from config import BACKEND_URL
from utils.embeds import create_embed, BrandColor
from utils.http import get_http_session
from utils.pdf_service import create_pdf_task, build_transcript_parts
from utils.failed_delivery import log_failed_delivery
from system_messages.room_closure import build_embed as build_closure_embed

logger = logging.getLogger('bot.utils.room_closure')

# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


async def send_room_closure_and_transcript(
    room_id: str,
    bot: commands.Bot,
    headers: dict,
    closure_type: str = 'agreement',
    agreement_id: str = '',
    leave_reason: str = '',
    left_by: str = '',
) -> bool:
    """Execute the complete room-closure + transcript-delivery sequence.

    Parameters
    ----------
    room_id:
        The interview room to close.
    bot:
        The Discord bot client (needed to resolve User objects for DM).
    headers:
        HTTP headers for backend API calls (must contain X-Webhook-Token).
    closure_type:
        ``'agreement'`` (default) or ``'leave'``. Controls the system-message
        type logged and which finalisation endpoint is called.
    agreement_id:
        Required when ``closure_type='agreement'``. Passed to the
        ``finalize-closure/`` backend endpoint.
    leave_reason:
        The reason provided by the user who left (leave flow only).
    left_by:
        ``'client'`` or ``'freelancer'``, who initiated the leave
        (leave flow only).

    Returns
    -------
    ``True`` if the full sequence completed successfully.
    ``False`` if a critical step (e.g. transcript generation) failed.
    """
    session = get_http_session()

    # ── 1. Log system message FIRST (appears in transcript PDF) ──────
    from cogs.Rooms.create_rooms import CreateRooms  # lazy import to avoid cycles

    sys_msg_type = 'closure'
    # Build closure body_text for transcript logging (no room headers)
    closure_build_data = {
        'room_id': room_id,
        'closure_type': closure_type,
        'leave_reason': leave_reason,
        'left_by': left_by,
    }
    _, closure_body_text = build_closure_embed(closure_build_data)
    await CreateRooms._log_system_message(room_id, sys_msg_type, {}, msg_text=closure_body_text, show_to='both')

    # ── 1b. Log "Room Transcript" BEFORE fetching data ──────────────
    # Ensures this message appears in the session JSON used for PDF generation
    transcript_label = 'A transcript of this room is being generated and will be delivered to both parties.'
    await CreateRooms._log_system_message(room_id, 'Room Transcript', {}, msg_text=transcript_label, show_to='both')

    # ── 2. Fetch transcript data from backend ────────────────────────
    transcript_data = await _fetch_transcript_data(room_id, headers, session)
    if not transcript_data:
        logger.error('Cannot generate transcripts, no transcript data for room %s', room_id)
        return False

    # ── 3. Determine participant info ────────────────────────────────
    client_name = transcript_data.get('client_name', 'Client')
    freelancer_name = transcript_data.get('freelancer_name', 'Freelancer')
    client_discord_id = transcript_data.get('client_discord_id', '')
    freelancer_discord_id = transcript_data.get('freelancer_discord_id', '')
    client_avatar_url = transcript_data.get('client_avatar_url')
    freelancer_avatar_url = transcript_data.get('freelancer_avatar_url')

    # ── 4. Send closure notification to both parties ─────────────────
    closure_payload = {
        'room_id': room_id,
        'closure_type': closure_type,
        'agreement_id': agreement_id,
        'leave_reason': leave_reason,
        'left_by': left_by,
        'client_name': client_name,
        'freelancer_name': freelancer_name,
    }
    closure_embed = build_closure_embed(closure_payload)

    for did, display_name in [
        (client_discord_id, client_name),
        (freelancer_discord_id, freelancer_name),
    ]:
        await _send_dm(bot, did, display_name, embed=closure_embed)

    # ── 5. Create transcript PDF task (fire-and-forget) ─────────────
    parts = build_transcript_parts(
        client_discord_id=client_discord_id,
        client_name=client_name,
        freelancer_discord_id=freelancer_discord_id,
        freelancer_name=freelancer_name,
        room_id=room_id,
    )

    task_id = await create_pdf_task(
        task_type='transcript',
        room_id=room_id,
        requester_discord_id=client_discord_id or freelancer_discord_id,
        parts=parts,
    )

    if not task_id:
        logger.error('Failed to create transcript PDF task for room %s', room_id)
        return False

    logger.info('Transcript task %s created for room %s', task_id, room_id)

    # ── 6. Call backend to finalise closure (agreement only) ─────────
    if closure_type == 'agreement':
        finalize_url = f'{BACKEND_URL}rooms/bot/finalize-closure/'
        finalize_payload = {
            'room_id': room_id,
            'agreement_id': agreement_id or '',
        }
        try:
            async with session.post(
                finalize_url,
                json=finalize_payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    logger.info(
                        'Room %s finalised: %s', room_id,
                        result.get('message', ''),
                    )

                    # ── 6b. Process system-closed rooms ──────────────
                    system_closed = result.get('system_closed_rooms', [])
                    if system_closed:
                        closure_reason = result.get('closure_reason', '')
                        await _process_system_closed_rooms(
                            system_closed, closure_reason,
                            bot, headers, session,
                        )

                    # ── 6c. Mark winning room closure completed ──────
                    await _mark_closure_completed(room_id, headers, session)
                else:
                    logger.warning(
                        'Finalize closure returned %s for room %s',
                        resp.status, room_id,
                    )
        except Exception:
            logger.exception('Failed to finalize closure for room %s', room_id)

    # ── 7. Mark closure process completed for leave rooms ────────────
    if closure_type == 'leave':
        await _mark_closure_completed(room_id, headers, session)

    return True


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────


async def _fetch_transcript_data(
    room_id: str,
    headers: dict,
    session: aiohttp.ClientSession,
) -> dict | None:
    """Fetch participant info from the backend ``fetch-transcript-data/`` endpoint.

    Used to obtain client/freelancer names and Discord IDs for building
    transcript parts and closure notifications.  The actual PDF generation
    is handled by the remote PDF Generator service.
    """
    url = f'{BACKEND_URL}rooms/bot/fetch-transcript-data/'
    try:
        async with session.get(
            url,
            params={'room_id': room_id},
            headers=headers,
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
    return None


async def _send_dm(
    bot: commands.Bot,
    discord_id: str,
    display_name: str,
    embed: discord.Embed,
    file: discord.File | None = None,
) -> bool:
    """Send an embed (and optional file) to a user via DM.

    Returns ``True`` if the DM was sent successfully, ``False`` otherwise.
    Logs failures but does **not** raise.
    """
    if not discord_id:
        return False

    try:
        user = bot.get_user(int(discord_id))
        if not user:
            user = await bot.fetch_user(int(discord_id))
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
        return True
    except discord.Forbidden:
        logger.warning(
            'Cannot DM %s (%s), DMs may be disabled.',
            display_name, discord_id,
        )
    except Exception:
        logger.exception(
            'Failed to send DM to %s (%s)',
            display_name, discord_id,
        )
    return False


# ──────────────────────────────────────────────────────────────────────
# Closure helpers
# ──────────────────────────────────────────────────────────────────────


async def _mark_closure_completed(
    room_id: str,
    headers: dict,
    session: aiohttp.ClientSession,
) -> None:
    """Call ``/bot/system-close/`` to set ``closure_process_completed=True``."""
    try:
        system_close_url = f'{BACKEND_URL}rooms/bot/system-close/'
        async with session.post(
            system_close_url,
            json={'room_id': room_id},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                logger.info(
                    'Room %s: closure process completed', room_id,
                )
            else:
                logger.warning(
                    'system-close returned %s for room %s',
                    resp.status, room_id,
                )
    except Exception:
        logger.exception(
            'Failed to mark closure completed for room %s', room_id,
        )


async def _process_system_closed_rooms(
    system_closed_rooms: list[dict],
    closure_reason: str,
    bot: commands.Bot,
    headers: dict,
    session: aiohttp.ClientSession,
) -> None:
    """Deliver closure notification + transcript to each system-closed room,
    then mark ``closure_process_completed=True`` via ``/bot/system-close/``."""
    for closed_room in system_closed_rooms:
        closed_room_id = closed_room.get('room_id', '')
        if not closed_room_id:
            continue

        # Build closure embed for system closure
        closure_payload = {
            'room_id': closed_room_id,
            'closure_type': 'system',
            'leave_reason': closure_reason,
            'client_name': closed_room.get('client_name', 'Client'),
            'freelancer_name': closed_room.get('freelancer_name', 'Freelancer'),
        }
        closure_embed = build_closure_embed(closure_payload)

        # Send closure DM to both parties
        for did, display_name in [
            (closed_room.get('client_discord_id', ''),
             closed_room.get('client_name', 'Client')),
            (closed_room.get('freelancer_discord_id', ''),
             closed_room.get('freelancer_name', 'Freelancer')),
        ]:
            await _send_dm(bot, did, display_name, embed=closure_embed)

        # Create transcript PDF task via PDF service (fire-and-forget)
        client_id = closed_room.get('client_discord_id', '')
        freelancer_id = closed_room.get('freelancer_discord_id', '')
        client_nm = closed_room.get('client_name', 'Client')
        freelancer_nm = closed_room.get('freelancer_name', 'Freelancer')

        parts = build_transcript_parts(
            client_discord_id=client_id,
            client_name=client_nm,
            freelancer_discord_id=freelancer_id,
            freelancer_name=freelancer_nm,
            room_id=closed_room_id,
        )

        task_id = await create_pdf_task(
            task_type='transcript',
            room_id=closed_room_id,
            requester_discord_id=client_id or freelancer_id,
            parts=parts,
        )

        if task_id:
            logger.info(
                'System-closed room %s: transcript task %s created',
                closed_room_id, task_id,
            )
        else:
            logger.error(
                'System-closed room %s: failed to create transcript task',
                closed_room_id,
            )

        # Mark closure process completed
        await _mark_closure_completed(
            closed_room_id, headers, session,
        )
