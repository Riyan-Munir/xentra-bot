import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import create_embed, BrandColor, error_embed

logger = logging.getLogger('bot.wallets.list')

class WalletList(commands.Cog):
    """``/wallet list``, View all your registered wallets."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_list", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_list(self, interaction: discord.Interaction) -> None:

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

                        embed = create_embed(
                            title=f"Registered Wallets",
                            description=f"**{len(wallets)}** wallet(s) found.",
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Wallets",
                        )

                        for w in wallets:
                            status_text = 'Verified' if w['is_verified'] else 'Pending'
                            default_tag = ' **(Default)**' if w['is_default'] else ''
                            label = w.get('label', '') or f"Wallet {w['id'][:8]}..."
                            address = w['address']
                            short_addr = f"`{address[:8]}...{address[-4:]}`"

                            value = (
                                f"> **ID**: `{w['id']}`{default_tag}\n"
                                f"> **Status**: `{w['status'].title()}` {status_text}\n"
                                f"> **Address**: {short_addr}\n"
                                f"> **Provider**: `{w.get('provider', 'N/A')}`"
                            )

                            embed.add_field(
                                name=label,
                                value=value,
                                inline=False,
                            )

                        return embed
                    else:
                        err_data = await resp.json()
                        return error_embed(
                            message=err_data.get('error', 'Could not load wallets.')
                        )
            except Exception:
                logger.exception("Failed to fetch wallet list")
                return error_embed(
                    message='The service is temporarily unavailable.'
                )

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(WalletList(bot))
