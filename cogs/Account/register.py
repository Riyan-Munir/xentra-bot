import discord
from discord.ext import commands
from discord import app_commands
from config import FRONTEND_URL
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import create_embed, BrandColor

class Register(commands.Cog):
    """``/register``, Register a new Xentra account."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="register", description="...")
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def register(self, interaction: discord.Interaction):
        
        async def build_register_embed(user_data):
            embed = create_embed(
                title="Account Registration",
                description=(
                    "> ***Xentra welcomes you — let's get you set up.***\n"
                    "`1.` Click the **Login** button below to open the login page.\n"
                    "`2.` Select a **Role**.\n"
                    "`3.` Enter a **Display Name** to access the dashboard.\n"
                    "\n"
                    "> __Click the button below to start.__"
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Account"
            )
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(
                    label="Login now",
                    style=discord.ButtonStyle.link,
                    url=FRONTEND_URL,
                )
            )
            return embed, view

        await validate_and_respond(interaction, build_register_embed)

async def setup(bot):
    await bot.add_cog(Register(bot))
