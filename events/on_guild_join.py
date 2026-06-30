import discord
from discord.ext import commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from packet_templates.factory import BotPacketFactory
from utils.analytics_collector import AnalyticsCollector

logger = logging.getLogger('bot.events')


class GuildJoinCog(commands.Cog):
    """Handle guild join events — Cog pattern for proper listener registration."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """
        Triggered when the bot joins a new guild.
        Activates/verifies the guild on the backend.
        """
        logger.info(f"Bot joined new guild: {guild.name} ({guild.id})")

        url = f"{BACKEND_URL}guilds/bot-management/"
        packet = BotPacketFactory.create_packet(
            packet_type="guild_status_update",
            data={
                'guild_id': guild.id,
                'guild_name': guild.name,
                'is_active': True,
                'admin_discord_id': str(guild.owner_id)
            },
            provider="bot"
        )
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        session = get_http_session()
        try:
            async with session.patch(url, json=packet.to_dict(), headers=headers) as resp:
                if resp.status == 200:
                    logger.info(f"Successfully activated/verified guild {guild.id} on backend.")
                else:
                    err_text = await resp.text()
                    logger.warning(f"Note: Guild {guild.id} activation status: {err_text}")
        except Exception as e:
            logger.error(f"Error notifying backend of guild join: {e}")

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
            event_type="guild_bot_join",
            actor=actor,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildJoinCog(bot))
