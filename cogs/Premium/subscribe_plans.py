import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import FRONTEND_URL
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import BrandColor, create_embed

logger = logging.getLogger(__name__)


class SubscribePlans(commands.Cog):
    """``/subscribe plans``, Browse subscription plans."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="subscribe_plans", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def subscribe_plans(self, interaction: discord.Interaction) -> None:

        async def callback(user_data):
            embed = create_embed(
                title="Subscription Plans",
                description=(
                    "> ***Browse and manage subscriptions on the Xentra web dashboard.***\n"
                    "`1.` Click the **Subscription** button below to open the Subscription section.\n"
                    "`2.` Choose a plan and complete payment on the web page.\n"
                    "\n"
                    "> __Subscription management is handled on the Xentra web dashboard.__"
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Premium",
            )
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(
                    label="Open Subscription",
                    style=discord.ButtonStyle.link,
                    url=f"{FRONTEND_URL}/dashboard?section=premium",
                )
            )
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(SubscribePlans(bot))
