import discord
from discord.ext import commands
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.http import get_http_session
from utils.analytics_collector import AnalyticsCollector

logger = logging.getLogger('bot.events.remove')

class MemberRemoveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Handle member leave: Mark guild membership as inactive and log."""
        if member.bot:
            return

        logger.info(f"Member left: {member.name} from {member.guild.name}")
        
        # Call DELETE on user-bot detail to mark inactive
        url = f"{BACKEND_URL}users/bot/{member.id}/"
        params = {
            'guild_id': member.guild.id,
            'guild_name': member.guild.name
        }
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        deactivated_profiles = []
        discord_username = member.name
        try:
            session = get_http_session()
            async with session.delete(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        deactivated_profiles = data.get('deactivated_profiles', [])
                        discord_username = data.get('discord_username', member.name)
                        logger.info(f"Successfully marked member leave for {member.name}, deactivated: {deactivated_profiles}")
        except Exception as e:
            logger.error(f"Error in on_member_remove: {e}")

        # Fire-and-forget analytics events
        is_owner = member.id == member.guild.owner_id
        is_admin = member.guild_permissions.manage_guild or member.guild_permissions.administrator
        if is_owner:
            role_name = 'Server Owner'
        elif is_admin:
            role_name = 'Server Admin'
        else:
            role_name = 'Member'
        actor = {
            'discord_id': str(member.id),
            'display_name': member.name,
            'profile_id': '',
            'role': 'owner' if is_owner else ('mod' if is_admin else 'member'),
            'role_display_name': role_name,
        }

        # Log 1: User left the server (discord_username only)
        AnalyticsCollector.log_guild_event(
            guild=member.guild,
            event_type="guild_member_leave",
            actor=actor,
            discord_username=discord_username,
        )

        # Logs 2+: Deactivate events (one per deactivated profile)
        for profile_info in deactivated_profiles:
            AnalyticsCollector.log_guild_event(
                guild=member.guild,
                event_type="guild_member_deactivate",
                actor=actor,
                discord_username=discord_username,
                profile_role=profile_info.get('role', ''),
                profile_display_name=profile_info.get('display_name', ''),
                profile_role_id=profile_info.get('role_id', ''),
            )

async def setup(bot):
    await bot.add_cog(MemberRemoveCog(bot))
