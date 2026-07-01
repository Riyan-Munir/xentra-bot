import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import create_embed, error_embed, BrandColor, loading_embed

logger = logging.getLogger('bot.profile.show_userid')


class ShowUserID(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="show_userid", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def show_userid(self, interaction: discord.Interaction, username: str = None):
        
        async def build_userid_embed(user_data):
            if not user_data.get('registered', False):
                msg = f"User **{username}** is not registered." if username else "Your identity is not registered."
                return error_embed(message=msg)

            role_ids = user_data.get('role_ids', {})
            if not role_ids:
                return error_embed(message="No profiles found for this user.")

            target_user = username if username else interaction.user.name
            embed = create_embed(
                title="Identity Registry",
                description=f"Registered roles for **{target_user}**:",
                color=BrandColor.PRIMARY
            )
            embed.set_footer(text='Xentra • Identity Registry')
            
            role_map = {
                'freelancer': 'Freelancer ID',
                'client': 'Client ID',
                'server_admin': 'Admin ID'
            }
            
            for role, label in role_map.items():
                if role in role_ids:
                    data = role_ids[role]
                    # Handle both structured and legacy data formats
                    rid = data.get('id') if isinstance(data, dict) else data
                    is_premium = data.get('is_premium', False) if isinstance(data, dict) else False

                    if is_premium:
                        embed.add_field(
                            name=label,
                            value=f"> **`{rid}`**",
                            inline=False
                        )
                    else:
                        embed.add_field(
                            name=label,
                            value=f"> `{rid}`",
                            inline=False
                        )
            
            return embed

        params = {'username': username} if username else None
        await validate_and_respond(interaction, build_userid_embed, additional_params=params)


async def setup(bot):
    await bot.add_cog(ShowUserID(bot))