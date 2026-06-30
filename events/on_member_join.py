import discord
from discord.ext import commands
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.analytics_collector import AnalyticsCollector
from utils.http import get_http_session

logger = logging.getLogger('bot.events.join')

class MemberJoinCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle member join: sync roles/status with backend."""
        if member.bot:
            return

        logger.info(f"Member joined: {member.name} in {member.guild.name}")

        url = f"{BACKEND_URL}users/bot/{member.id}/"
        params = {
            'guild_id': member.guild.id,
            'guild_name': member.guild.name,
            'should_sync': 'true',
            'is_owner': 'true' if member.id == member.guild.owner_id else 'false',
            'is_mod': 'true' if (member.guild_permissions.manage_guild or member.guild_permissions.administrator) else 'false'
        }
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        synced_profiles = []
        try:
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    synced_profiles = data.get('synced_profiles', [])
                    logger.info(f"Successfully synced member join for {member.name}, profiles: {synced_profiles}")
        except Exception as e:
            logger.error(f"Error in on_member_join sync: {e}")

        # Fire-and-forget analytics event for member join
        AnalyticsCollector.log_guild_event(
            guild=member.guild,
            event_type='guild_member_join',
            metadata={
                'member_discord_id': member.id,
                'member_name': member.name,
                'is_owner': member.id == member.guild.owner_id,
                'is_admin': bool(member.guild_permissions.manage_guild or member.guild_permissions.administrator),
                'synced_profiles': synced_profiles,
            },
        )

async def setup(bot):
    await bot.add_cog(MemberJoinCog(bot))
