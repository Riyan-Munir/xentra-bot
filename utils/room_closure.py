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
import io
import logging
import os
import tempfile
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands

from config import BACKEND_URL
from utils.embeds import info_embed
from utils.http import get_http_session
from utils.transcript_generator import generate_transcript
from utils.pdf_compressor import compress_pdf
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
    await CreateRooms._log_system_message(room_id, sys_msg_type, {})

    # ── 1b. Log "Room Transcript" BEFORE fetching data ──────────────
    # Ensures this message appears in the session JSON used for PDF generation
    await CreateRooms._log_system_message(room_id, 'Room Transcript', {})

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

    # ── 5. Generate Room-Transcript.pdf (2 versions) ─────────────────
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M') + ' UTC'
    base_messages = transcript_data.get('freelancer_messages', [])

    # Freelancer view, freelancer msgs on right
    freelancer_pdf_data = {
        'transcript_id': transcript_data.get('transcript_id', f'XEN-TRX-{room_id}'),
        'room_id': room_id,
        'client_name': client_name,
        'freelancer_name': freelancer_name,
        'client_avatar_url': client_avatar_url,
        'freelancer_avatar_url': freelancer_avatar_url,
        'viewer_role': 'freelancer',
        'generated_on': now_str,
        'messages': base_messages,
        'watermark_b64': transcript_data.get('watermark_b64', ''),
        'logo_b64': transcript_data.get('logo_b64', ''),
    }

    # Client view, client msgs on right
    client_pdf_data = {
        'transcript_id': transcript_data.get('transcript_id', f'XEN-TRX-{room_id}'),
        'room_id': room_id,
        'client_name': client_name,
        'freelancer_name': freelancer_name,
        'client_avatar_url': client_avatar_url,
        'freelancer_avatar_url': freelancer_avatar_url,
        'viewer_role': 'client',
        'generated_on': now_str,
        'messages': base_messages,
        'watermark_b64': transcript_data.get('watermark_b64', ''),
        'logo_b64': transcript_data.get('logo_b64', ''),
    }

    transcript_paths = {}
    loop = asyncio.get_event_loop()
    try:
        with tempfile.NamedTemporaryFile(
            suffix='_freelancer.pdf', delete=False,
        ) as tmp_f:
            freelancer_path = tmp_f.name
        await loop.run_in_executor(
            None, generate_transcript, freelancer_pdf_data, freelancer_path
        )
        transcript_paths['freelancer'] = freelancer_path

        with tempfile.NamedTemporaryFile(
            suffix='_client.pdf', delete=False,
        ) as tmp_c:
            client_path = tmp_c.name
        await loop.run_in_executor(
            None, generate_transcript, client_pdf_data, client_path
        )
        transcript_paths['client'] = client_path
    except Exception:
        logger.exception('Transcript generation failed for room %s', room_id)
        for path in transcript_paths.values():
            try:
                os.unlink(path)
            except Exception:
                pass
        return False

    # ── 6. Send Room-Transcript.pdf to each party ────────────────────
    transcript_msg = (
        'Review the attached transcript of your '
        f'Interview Room **{room_id}**.\n\n'
        'This document records all correspondence exchanged '
        'during the interview phase.'
    )
    transcript_embed = info_embed(message=transcript_msg)

    targets = [
        (freelancer_discord_id, freelancer_name, 'freelancer'),
        (client_discord_id, client_name, 'client'),
    ]

    for did, display_name, viewer in targets:
        pdf_path = transcript_paths.get(viewer)
        if not pdf_path or not os.path.exists(pdf_path):
            logger.error('Transcript PDF not found for %s in room %s', viewer, room_id)
            continue

        try:
            with open(pdf_path, 'rb') as f:
                pdf_bytes = f.read()
            pdf_bytes = compress_pdf(pdf_bytes)

            await _send_dm(
                bot, did, display_name,
                embed=transcript_embed,
                file=discord.File(
                    io.BytesIO(pdf_bytes),
                    filename='Room-Transcript.pdf',
                ),
            )
            logger.info(
                'Transcript sent to %s (%s) for room %s',
                display_name, did, room_id,
            )
        except Exception:
            logger.exception(
                'Failed to send transcript to %s (%s)',
                display_name, did,
            )
            await log_failed_delivery(
                room_id=room_id,
                message_type='transcript',
                target_discord_id=did,
                session=session,
                headers=headers,
            )

    # Clean up temp files
    for path in transcript_paths.values():
        try:
            os.unlink(path)
        except Exception:
            pass

    # ── 7. Call backend to finalise closure (agreement only) ─────────
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

                    # ── 7b. Process system-closed rooms ──────────────
                    system_closed = result.get('system_closed_rooms', [])
                    if system_closed:
                        closure_reason = result.get('closure_reason', '')
                        await _process_system_closed_rooms(
                            system_closed, closure_reason,
                            bot, headers, session,
                        )

                    # ── 7c. Mark winning room closure completed ──────
                    await _mark_closure_completed(room_id, headers, session)
                else:
                    logger.warning(
                        'Finalize closure returned %s for room %s',
                        resp.status, room_id,
                    )
        except Exception:
            logger.exception('Failed to finalize closure for room %s', room_id)

    # ── 8. Mark closure process completed for leave rooms ────────────
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
    """Call ``fetch-transcript-data/`` and return the parsed JSON."""
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

        # Generate and deliver transcript (reuse same logic as main room)
        transcript_data = await _fetch_transcript_data(
            closed_room_id, headers, session,
        )
        if transcript_data:
            await _deliver_transcript_pdfs(
                transcript_data, closed_room_id,
                bot, headers, session,
            )

        # Mark closure process completed
        await _mark_closure_completed(
            closed_room_id, headers, session,
        )


async def _deliver_transcript_pdfs(
    transcript_data: dict,
    room_id: str,
    bot: commands.Bot,
    headers: dict,
    session: aiohttp.ClientSession,
) -> None:
    """Generate viewer-specific transcript PDFs and send to both parties."""
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M') + ' UTC'
    base_messages = transcript_data.get('freelancer_messages', [])

    client_name = transcript_data.get('client_name', 'Client')
    freelancer_name = transcript_data.get('freelancer_name', 'Freelancer')
    client_discord_id = transcript_data.get('client_discord_id', '')
    freelancer_discord_id = transcript_data.get('freelancer_discord_id', '')
    client_avatar_url = transcript_data.get('client_avatar_url')
    freelancer_avatar_url = transcript_data.get('freelancer_avatar_url')

    freelancer_pdf_data = {
        'transcript_id': transcript_data.get('transcript_id', f'XEN-TRX-{room_id}'),
        'room_id': room_id,
        'client_name': client_name,
        'freelancer_name': freelancer_name,
        'client_avatar_url': client_avatar_url,
        'freelancer_avatar_url': freelancer_avatar_url,
        'viewer_role': 'freelancer',
        'generated_on': now_str,
        'messages': base_messages,
        'watermark_b64': transcript_data.get('watermark_b64', ''),
        'logo_b64': transcript_data.get('logo_b64', ''),
    }

    client_pdf_data = {
        'transcript_id': transcript_data.get('transcript_id', f'XEN-TRX-{room_id}'),
        'room_id': room_id,
        'client_name': client_name,
        'freelancer_name': freelancer_name,
        'client_avatar_url': client_avatar_url,
        'freelancer_avatar_url': freelancer_avatar_url,
        'viewer_role': 'client',
        'generated_on': now_str,
        'messages': base_messages,
        'watermark_b64': transcript_data.get('watermark_b64', ''),
        'logo_b64': transcript_data.get('logo_b64', ''),
    }

    transcript_paths = {}
    loop = asyncio.get_event_loop()
    try:
        with tempfile.NamedTemporaryFile(
            suffix='_freelancer.pdf', delete=False,
        ) as tmp_f:
            freelancer_path = tmp_f.name
        await loop.run_in_executor(
            None, generate_transcript, freelancer_pdf_data, freelancer_path,
        )
        transcript_paths['freelancer'] = freelancer_path

        with tempfile.NamedTemporaryFile(
            suffix='_client.pdf', delete=False,
        ) as tmp_c:
            client_path = tmp_c.name
        await loop.run_in_executor(
            None, generate_transcript, client_pdf_data, client_path,
        )
        transcript_paths['client'] = client_path
    except Exception:
        logger.exception(
            'Transcript generation failed for system-closed room %s', room_id,
        )
        for path in transcript_paths.values():
            try:
                os.unlink(path)
            except Exception:
                pass
        return

    transcript_msg = (
        'Review the attached transcript of your '
        f'Interview Room **{room_id}**.\n\n'
        'This document records all correspondence exchanged '
        'during the interview phase.'
    )
    transcript_embed = info_embed(message=transcript_msg)

    for did, display_name, viewer in [
        (freelancer_discord_id, freelancer_name, 'freelancer'),
        (client_discord_id, client_name, 'client'),
    ]:
        pdf_path = transcript_paths.get(viewer)
        if not pdf_path or not os.path.exists(pdf_path):
            continue
        try:
            with open(pdf_path, 'rb') as f:
                pdf_bytes = f.read()
            pdf_bytes = compress_pdf(pdf_bytes)
            await _send_dm(
                bot, did, display_name,
                embed=transcript_embed,
                file=discord.File(
                    io.BytesIO(pdf_bytes),
                    filename='Room-Transcript.pdf',
                ),
            )
        except Exception:
            logger.exception(
                'Failed to send transcript to %s (%s)', display_name, did,
            )
            await log_failed_delivery(
                room_id=room_id,
                message_type='transcript',
                target_discord_id=did,
                session=session,
                headers=headers,
            )

    for path in transcript_paths.values():
        try:
            os.unlink(path)
        except Exception:
            pass
