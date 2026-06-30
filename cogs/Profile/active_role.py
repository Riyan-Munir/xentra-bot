import discord
from discord.ext import commands
from discord import app_commands
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, loading_embed
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.profile.active_role')

class ActiveRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="active_role", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def active_role(self, interaction: discord.Interaction):
        
        async def build_active_embed(user_data):
            active = user_data.get('active_role', 'Not set')
            role_label = active.replace('_', ' ').title()
            
            embed = create_embed(
                title="Active Identity",
                color=BrandColor.PRIMARY,
                thumbnail=interaction.user.display_avatar.url
            )
            
            embed.add_field(
                name="Status",
                value=f"> **{role_label}**",
                inline=True
            )
            
            embed.set_footer(text='Xentra •')
            return embed
        
        await validate_and_respond(interaction, build_active_embed)

async def setup(bot):
    await bot.add_cog(ActiveRole(bot))