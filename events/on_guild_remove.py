from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from packet_templates.factory import BotPacketFactory
from utils.analytics_collector import AnalyticsCollector

logger = logging.getLogger('bot.events')

async def setup(bot):
    @bot.event
    async def on_guild_remove(guild):
        """
        Triggered when the bot is kicked from a guild.
        Updates the backend to freeze the guild's records.
        """
        logger.info(f"Bot was removed from guild: {guild.name} ({guild.id})")
        
        url = f"{BACKEND_URL}guilds/bot-management/"
        packet = BotPacketFactory.create_packet(
            packet_type="guild_status_update",
            data={
                'guild_id': guild.id,
                'guild_name': guild.name,
                'is_active': False
            },
            provider="bot"
        )
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        
        session = get_http_session()
        try:
            async with session.patch(url, json=packet.to_dict(), headers=headers) as resp:
                if resp.status == 200:
                    logger.info(f"Successfully froze guild {guild.id} on backend.")
                else:
                    err_text = await resp.text()
                    logger.error(f"Failed to freeze guild {guild.id}: {err_text}")
        except Exception as e:
            logger.error(f"Error notifying backend of guild removal: {e}")

        # Fire-and-forget analytics event
        actor = {
            'discord_id': str(guild.owner_id) if guild.owner else '',
            'display_name': guild.owner.name if guild.owner else 'Unknown',
            'profile_id': '',
            'role': 'system',
            'role_display_name': 'System',
        }
        AnalyticsCollector.log_guild_event(
            guild=guild,
            event_type="guild_bot_leave",
            actor=actor,
        )
