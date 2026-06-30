import discord
from discord.ext import commands
from discord import app_commands
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import create_embed, BrandColor, loading_embed

class RegisterCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="register", description="...")
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def register(self, interaction: discord.Interaction):
        
        async def build_register_embed(user_data):
            from config import FRONTEND_URL
            embed = create_embed(
                title="Account Registration Gateway",
                description=(
                    f"**Gateway**: Initialize your digital identity via the link below.\n"
                    f"**Authorization**: Authenticate with your Discord identity to register.\n"
                    f"**Link**: [**Initialize Xentra Account**]({FRONTEND_URL})"
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Identity gateway"
            )
            return embed

        await validate_and_respond(interaction, build_register_embed)

async def setup(bot):
    await bot.add_cog(RegisterCommand(bot))