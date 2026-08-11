import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import (
    BrandColor, create_embed, error_embed,
)

logger = logging.getLogger('bot.wallets.default')


class WalletDefault(commands.Cog):
    """``/wallet default``, View your default wallet."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_default", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_default(self, interaction: discord.Interaction) -> None:

        async def callback(user_data):
            url = f"{BACKEND_URL}wallets/bot/list/"
            params = {'discord_id': interaction.user.id}
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        wallets = data.get('wallets', [])

                        if not wallets:
                            return error_embed(
                                message='No registered wallets found. '
                                'Use `/wallet register` to add one.'
                            )

                        defaults = [w for w in wallets if w.get('is_default')]

                        if not defaults:
                            return error_embed(
                                message='No default wallet set. '
                                'Use `/wallet register` to add one on the dashboard.'
                            )

                        w = defaults[0]
                        label = w.get('label', '') or f"Wallet {w['id'][:8]}..."
                        address = w['address']
                        short_addr = f"`{address[:8]}...{address[-4:]}`"

                        embed = create_embed(
                            title="Default Wallet",
                            description=(
                                "> ***Your default wallet** — used for escrow payments.*\n"
                                "\n"
                                "> __Manage your wallets on the Xentra dashboard.__"
                            ),
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Wallets",
                        )

                        embed.add_field(
                            name=label,
                            value=(
                                f"> **ID**: `{w['id']}`\n"
                                f"> **Status**: `{w['status'].title()}`\n"
                                f"> **Address**: {short_addr}\n"
                                f"> **Provider**: `{w.get('provider', 'N/A')}`"
                            ),
                            inline=False,
                        )

                        return embed
                    else:
                        err_data = await resp.json()
                        return error_embed(
                            message=err_data.get('error', 'Could not load wallets.')
                        )
            except Exception:
                logger.exception("Failed to fetch default wallet")
                return error_embed(
                    message='The service is temporarily unavailable.'
                )

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(WalletDefault(bot))
