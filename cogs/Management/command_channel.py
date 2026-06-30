import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import re
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import success_embed, error_embed, loading_embed
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.command_channel')

class CommandChannelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="command_channel", description="...")
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def command_channel(self, interaction: discord.Interaction, channel_id: str):
        
        async def build_embed(user_data):
            # 1. Clean the channel_id to extract digits
            digits = re.findall(r"\d+", channel_id)
            if not digits:
                return error_embed(message="Invalid channel ID. Use a channel ID or mention (e.g. #channel).")
            
            clean_id = int(digits[0])
            
            # 2. Verify channel exists in this guild
            target_channel = interaction.guild.get_channel(clean_id)
            if not target_channel:
                return error_embed(message="The channel provided does not exist in this server.")
                
            if not isinstance(target_channel, discord.TextChannel):
                return error_embed(message="The command channel must be a text channel.")

            # 3. Call backend to update configuration
            url = f"{BACKEND_URL}guilds/bot-management/"
            packet = BotPacketFactory.create_packet(
                packet_type="guild_configure_channel",
                data={
                    'guild_id': interaction.guild.id,
                    'channel_id': str(clean_id)
                },
                provider="bot"
            )
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            try:
                session = get_http_session()
                async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                        if resp.status == 200:
                            return success_embed(
                                message=f"Command channel successfully configured to {target_channel.mention}."
                            )
                        else:
                            res_data = await resp.json()
                            return error_embed(message=res_data.get('error', "Failed to configure command channel."))
            except Exception as e:
                logger.error(f"Error configuring command channel: {e}")
                return error_embed(message="Something went wrong. Please try again.")

        await validate_and_respond(interaction, build_embed)

async def setup(bot):
    await bot.add_cog(CommandChannelCog(bot))