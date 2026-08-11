import discord
from discord.ext import commands
from discord import app_commands
from config import FRONTEND_URL
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import create_embed, BrandColor

class WalletRegister(commands.Cog):
    """``/wallet register``, Register a new wallet address."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_register", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_register(self, interaction: discord.Interaction) -> None:

        async def callback(user_data):
            embed = create_embed(
                title="Register a Wallet",
                description=(
                    "> ***Wallet registration now happens on the Xentra web dashboard.***\n"
                    "`1.` Click the button below to open the **Wallets** section.\n"
                    "`2.` Add and verify your wallet there to enable escrow payments.\n"
                    "\n"
                    "> __Wallet management is handled on the Xentra web dashboard.__"
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Wallets",
            )
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(
                    label="Open Wallets",
                    style=discord.ButtonStyle.link,
                    url=f"{FRONTEND_URL}/dashboard?section=wallets",
                )
            )
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(WalletRegister(bot))
