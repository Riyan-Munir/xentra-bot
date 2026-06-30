import discord
from discord.ext import commands
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.http import get_http_session

logger = logging.getLogger('bot.events.update')

class MemberUpdateCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Handle member update: Sync role/permission changes with backend."""
        if after.bot:
            return

        # Only sync if admin status changed (owner/mod permissions)
        before_owner = before.id == before.guild.owner_id
        after_owner = after.id == after.guild.owner_id
        before_admin = before.guild_permissions.manage_guild or before.guild_permissions.administrator
        after_admin = after.guild_permissions.manage_guild or after.guild_permissions.administrator

        if before_owner != after_owner or before_admin != after_admin:
            url = f"{BACKEND_URL}users/bot/{after.id}/"
            params = {
                'guild_id': after.guild.id,
                'should_sync': 'true',
                'is_mod': 'true' if after_admin else 'false',
                'is_owner': 'true' if after_owner else 'false'
            }
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            try:
                session = get_http_session()
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        logger.info(f"Successfully synced permissions for {after.name}")
            except Exception as e:
                logger.error(f"Error in on_member_update: {e}")

async def setup(bot):
    await bot.add_cog(MemberUpdateCog(bot))
