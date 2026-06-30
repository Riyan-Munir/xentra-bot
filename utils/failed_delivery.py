"""
Singleton utility for logging failed DM deliveries.

Every command that sends a DM notification calls ``log_failed_delivery()``
instead of duplicating the HTTP POST to ``log-failed-delivery/``.

Usage
-----
    from utils.failed_delivery import log_failed_delivery

    delivery_ok = await handle_system_message(...)
    if not delivery_ok:
        await log_failed_delivery(
            room_id=room_id,
            message_type='notification',
            target_discord_id=other_discord_id,
            msg_id=msg_id,          # from backend response
            session=session,        # optional — falls back to shared session
            headers=headers,        # optional — falls back to webhook-secret
        )
"""

import logging

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.http import get_http_session

logger = logging.getLogger('bot.utils.failed_delivery')


async def log_failed_delivery(
    room_id: str,
    message_type: str,
    target_discord_id: str,
    msg_id: str = '',
    msg_name: str = '',
    complain_id: str = '',
    session=None,
    headers: dict | None = None,
) -> bool:
    """POST a failed-delivery record to the backend.

    Accepts the same fields as ``InterviewFailedDelivery``:

    +------------------------+----------------------------------------------+
    | Parameter              | Required when                                |
    +------------------------+----------------------------------------------+
    | ``room_id``            | always                                       |
    | ``message_type``       | always — one of ``interview_message``,       |
    |                       | ``notification``, ``system_message``         |
    | ``target_discord_id``  | always                                       |
    | ``msg_id``             | ``interview_message`` or ``notification``    |
    |                       | (unless ``complain_id`` is used)             |
    | ``msg_name``           | ``system_message`` (e.g. ``"rules"``)        |
    | ``complain_id``        | ``notification`` when ``msg_id`` is empty    |
    +------------------------+----------------------------------------------+

    Returns ``True`` if the backend accepted the record.
    """
    if not room_id or not message_type or not target_discord_id:
        logger.error(
            'log_failed_delivery called with missing required fields: '
            'room_id=%s message_type=%s target_discord_id=%s',
            room_id, message_type, target_discord_id,
        )
        return False

    url = f'{BACKEND_URL}rooms/bot/log-failed-delivery/'
    body = {
        'room_id': room_id,
        'message_type': message_type,
        'target_discord_id': target_discord_id,
    }
    if msg_id:
        body['msg_id'] = msg_id
    if msg_name:
        body['msg_name'] = msg_name
    if complain_id:
        body['complain_id'] = complain_id

    # Resolve session / headers with fallbacks
    http_session = session or get_http_session()
    http_headers = headers or {'X-Webhook-Token': WEBHOOK_SECRET}

    try:
        async with http_session.post(url, json=body, headers=http_headers) as resp:
            if resp.status != 200:
                logger.warning(
                    'Failed to log failed delivery (type=%s, target=%s, room=%s): %s',
                    message_type, target_discord_id, room_id, await resp.text(),
                )
                return False
            logger.info(
                'Logged failed delivery (type=%s, target=%s, room=%s)',
                message_type, target_discord_id, room_id,
            )
            return True
    except Exception:
        logger.exception(
            'Exception logging failed delivery (type=%s, target=%s, room=%s)',
            message_type, target_discord_id, room_id,
        )
        return False
