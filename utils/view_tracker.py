"""
Shared view-tracking utility for profile and stats views.

Extracted from duplicate implementations in user_profile.py and user_stats.py.
"""

import logging

from config import BACKEND_URL, WEBHOOK_SECRET
from packet_templates.factory import BotPacketFactory
from utils.http import get_http_session

logger = logging.getLogger('bot.view_tracker')


async def increment_profile_view(
    viewer_discord_id: int,
    role: str,
    profile_id: str,
) -> None:
    """Increment a profile view counter on the backend.

    Only freelancer and client profiles get counted — server_admin views are
    deliberately excluded to avoid inflating stats with administrative lookups.

    This is a fire-and-forget call; failures are logged but never propagated.

    Args:
        viewer_discord_id: The Discord user ID of the viewer.
        role:            ``"freelancer"`` or ``"client"``.
        profile_id:      The canonical profile ID being viewed.
    """
    if role == 'server_admin':
        return

    url = f"{BACKEND_URL}stats/view/"
    data = {
        'role': role,
        'viewer_discord_id': viewer_discord_id,
    }
    if role == 'freelancer':
        data['freelancer_id'] = profile_id
    else:
        data['client_id'] = profile_id

    packet = BotPacketFactory.create_packet(
        packet_type="stats_view_increment",
        data=data,
        provider="bot",
    )
    headers = {'X-Webhook-Token': WEBHOOK_SECRET}

    session = get_http_session()
    try:
        async with session.post(
            url, json=packet.to_dict(), headers=headers,
        ) as resp:
            if resp.status != 200:
                logger.error(
                    f"Failed to increment view: {await resp.text()}"
                )
    except Exception as e:
        logger.error(f"Error calling increment_profile_view: {e}")
