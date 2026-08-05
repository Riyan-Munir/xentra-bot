"""
Shared components for Rooms commands.

Provides:
- :class:`RoomTypeSelect` — canonical room-type dropdown for every Rooms cog.
- :func:`record_and_notify` — persist a command message record and send a DM
  notification to the other party, logging a failed-delivery on DM failure.

Usage
-----
    from ._shared import RoomTypeSelect

    class MyView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__()
            self.room_type: str | None = None
            self.add_item(RoomTypeSelect())

    from ._shared import record_and_notify

    await record_and_notify(
        room_id=room_id,
        sender_role='client',
        msg_data='Final budget set to $500.',
        command_name='interview_budget',
        bot=interaction.client,
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery
from utils.pdf_service import (
    create_pdf_task,
    build_transcript_parts,
    build_single_transcript_parts,
    build_agreement_parts,
    build_single_agreement_parts,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger('bot.rooms.shared')


# ---------------------------------------------------------------------------
# Room Type Dropdown
# ---------------------------------------------------------------------------


class RoomTypeSelect(discord.ui.Select):
    """Canonical room-type dropdown for every Rooms cog.

    Expects ``self.view.room_type`` (a ``str | None``) to exist on
    the parent view.  When the user makes a selection the value is
    stored there and the interaction is deferred.
    """

    def __init__(self, placeholder: str = "Select room type") -> None:
        options = [
            discord.SelectOption(
                label="Interview Room",
                value="interview",
                description="Interview a freelancer for a job application",
            ),
            discord.SelectOption(
                label="Job Room",
                value="job",
                description="Complete a job with an agreed freelancer",
            ),
        ]
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.room_type = self.values[0]
        await interaction.response.defer()


# ---------------------------------------------------------------------------
# Record & Notify
# ---------------------------------------------------------------------------


async def _fetch_room_details(room_id: str, session, headers: dict) -> dict:
    """GET ``/rooms/bot/room-details/`` by ``room_id``.

    Returns the room-details payload dict, or ``{}`` on any failure
    (logged, never raised).
    """
    url = f'{BACKEND_URL}rooms/bot/room-details/'
    params = {'room_id': room_id}
    try:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 200:
                body = await resp.json()
                return body if isinstance(body, dict) else {}
            logger.warning(
                'room-details fetch returned %s for room=%s',
                resp.status, room_id,
            )
            return {}
    except Exception:
        logger.exception('room-details fetch failed for room=%s', room_id)
        return {}


async def record_and_notify(
    *,
    room_id: str,
    sender_role: str,
    msg_data: str,
    command_name: str,
    bot: discord.Client,
    session=None,
    headers: dict | None = None,
) -> str:
    """Record a command message and DM-notify the other party.

    Single entry-point for every interview command.  The command only
    supplies the four core values:

        1. ``room_id``     — the active interview room id.
        2. ``sender_role`` — ``'client'`` or ``'freelancer'`` (the
           active role of the person who ran the command).
        3. ``msg_data``    — the exact message text shown to the executor
           (final success/error/info).
        4. ``command_name``— the command identifier, e.g. ``'interview_budget'``.

    Everything else is resolved from the backend:

        1. Fetches ``GET /rooms/bot/room-details/?room_id=...``.
        2. Derives the DM recipient (the **other** party) and the
           executor display name from ``sender_role``:

               freelancer ran → target=client_discord_id, executor=freelancer_name
               client ran     → target=freelancer_discord_id, executor=client_name

        3. POSTs to ``/bot/log-command-msg/`` to persist the transcript
           record and receives a ``msg_id``.
        4. Sends a DM notification to the target via
           ``handle_system_message('interview_room_message', ...)``.
        5. If the DM fails, logs a failed-delivery record with the
           ``msg_id`` so it can be retried via ``/interview delivery``.

    Parameters
    ----------
    room_id : str
        Interview room identifier.
    sender_role : str
        ``'client'`` or ``'freelancer'`` — who executed the command.
    msg_data : str
        Human-readable message (same text shown to the executor).
    command_name : str
        Command identifier, e.g. ``'interview_budget'``.
    bot : discord.Client
        The running bot instance (for ``handle_system_message``).
    session : optional
        Reusable ``aiohttp`` session.  Falls back to the shared session.
    headers : dict, optional
        Request headers.  Falls back to the webhook-secret header.

    Returns
    -------
    str
        The ``msg_id`` returned by the backend (empty string on failure).
    """
    http_session = session or get_http_session()
    http_headers = headers or {'X-Webhook-Token': WEBHOOK_SECRET}

    # ── 1. Record the message in the backend ───────────────────────────
    msg_id = await _record_msg(
        room_id=room_id,
        sender_role=sender_role,
        msg_data=msg_data,
        command_name=command_name,
        session=http_session,
        headers=http_headers,
    )

    # ── 2. Resolve room details for the DM notification ────────────────
    room_details = await _fetch_room_details(room_id, http_session, http_headers)
    if not room_details:
        # Cannot build the DM payload without room details.
        return msg_id

    if sender_role == 'client':
        target_discord_id = room_details.get('freelancer_discord_id', '')
        executor_name = room_details.get('client_name', 'Client')
    else:
        target_discord_id = room_details.get('client_discord_id', '')
        executor_name = room_details.get('freelancer_name', 'Freelancer')

    job_title = room_details.get('job_title', '')

    # ── 3. Send DM notification to the other party ─────────────────────
    if not target_discord_id:
        # No one to notify (e.g. system message to self).
        return msg_id

    notify_data = {
        'discord_id': target_discord_id,
        'room_id': room_id,
        'job_title': job_title,
        'command_name': command_name,
        'executor_name': executor_name,
        'msg_data': msg_data,
    }

    delivery_ok = await handle_system_message(
        message_type='interview_room_message',
        data=notify_data,
        bot=bot,
    )

    # ── 4. Log failed delivery if DM didn't go through ─────────────────
    if not delivery_ok:
        if msg_id:
            await log_failed_delivery(
                room_id=room_id,
                message_type='notification',
                target_discord_id=target_discord_id,
                msg_id=msg_id,
                session=http_session,
                headers=http_headers,
            )
        else:
            logger.warning(
                'record_and_notify: no msg_id from backend, '
                'cannot log failed delivery for %s in room %s',
                target_discord_id, room_id,
            )

    return msg_id


# ---------------------------------------------------------------------------
# Request PDF (fire-and-forget)
# ---------------------------------------------------------------------------


async def request_pdf(
    *,
    task_type: str,
    room_id: str,
    room_type: str,
    requester_discord_id: str,
    recipient_discord_id: str | None = None,
    viewer_role: str | None = None,
    client_discord_id: str | None = None,
    freelancer_discord_id: str | None = None,
) -> str | None:
    """Submit a PDF generation request to the backend + PDF service.

    Commands call this helper (fire-and-forget) instead of touching the
    PDF pipeline directly.  This helper builds the delivery ``parts`` list
    and hands everything to :func:`create_pdf_task`, which creates the task
    on the backend and dispatches it to the PDF Generator.

    Each part carries only: ``part_id``, ``viewer_role``,
    ``recipient_discord_id``, ``filename``.  The PDF Generator fetches all
    rendering data from the backend itself, so no payload is shared.

    Parameters
    ----------
    task_type : str
        ``'transcript'``, ``'agreement'``, or ``'signed_agreement'``.
    room_id : str
        Interview room identifier.
    room_type : str
        ``'interview'`` or ``'job'``.
    requester_discord_id : str
        Discord ID of the user who triggered the request.
    recipient_discord_id, viewer_role : str, optional
        Single-party delivery (on-demand transcript / agreement review).
    client_discord_id, freelancer_discord_id : str, optional
        Two-party delivery (signed agreement / shared transcript).

    Returns
    -------
    str | None
        The ``task_id`` (``PDF_XXXXXXXX``) on success, or ``None`` on failure.
    """
    if task_type == 'transcript':
        if recipient_discord_id:
            parts = build_single_transcript_parts(
                recipient_discord_id=recipient_discord_id,
                viewer_role=viewer_role or 'client',
            )
        else:
            parts = build_transcript_parts(
                client_discord_id=client_discord_id or '',
                freelancer_discord_id=freelancer_discord_id or '',
            )
    elif task_type == 'agreement':
        parts = build_single_agreement_parts(
            recipient_discord_id=recipient_discord_id or '',
            viewer_role=viewer_role or 'client',
        )
    elif task_type == 'signed_agreement':
        parts = build_agreement_parts(
            client_discord_id=client_discord_id or '',
            freelancer_discord_id=freelancer_discord_id or '',
        )
    else:
        logger.error('request_pdf: unsupported task_type=%s', task_type)
        return None

    return await create_pdf_task(
        task_type=task_type,
        room_id=room_id,
        room_type=room_type,
        requester_discord_id=requester_discord_id,
        parts=parts,
    )


async def _record_msg(
    *,
    room_id: str,
    sender_role: str,
    msg_data: str,
    command_name: str,
    session,
    headers: dict,
) -> str:
    """POST to ``/bot/log-command-msg/`` and return the ``msg_id``.

    Returns an empty string on any failure (logged, never raised).
    """
    url = f'{BACKEND_URL}rooms/bot/log-command-msg/'
    payload = {
        'room_id': room_id,
        'sender_role': sender_role,
        'msg_data': msg_data,
        'command_name': command_name,
    }

    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            body = await resp.json()
            if resp.status == 200 and body.get('success'):
                msg_id = body.get('msg_id', '')
                logger.info(
                    'record_and_notify: recorded cmd=%s room=%s msg_id=%s',
                    command_name, room_id, msg_id,
                )
                return msg_id

            logger.warning(
                'record_and_notify: backend returned %s for room=%s cmd=%s: %s',
                resp.status, room_id, command_name,
                body.get('error', 'unknown'),
            )
            return ''

    except Exception:
        logger.exception(
            'record_and_notify: exception recording msg for room=%s cmd=%s',
            room_id, command_name,
        )
        return ''
