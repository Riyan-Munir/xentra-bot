import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import create_embed, BrandColor, error_embed

logger = logging.getLogger('bot.wallets.list')

class WalletListCommand(commands.Cog):
    """``/wallet list``, View all your registered wallets."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_list", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_list(self, interaction: discord.Interaction) -> None:

        async def callback(user_data):
            active_role = user_data.get('active_role')
            wallet_type = active_role if active_role in ('freelancer', 'client') else None

            url = f"{BACKEND_URL}wallets/bot/list/"
            params = {'discord_id': interaction.user.id}
            if wallet_type:
                params['type'] = wallet_type
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        wallets = data.get('wallets', [])

                        if not wallets:
                            return error_embed(
                                message='You have no registered wallets. '
                                'Use `/wallet register` to add one.'
                            )

                        embed = create_embed(
                            title=f"Your Wallets ({active_role.title()})",
                            description=f"You have **{len(wallets)}** wallet(s) registered.",
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Wallet List",
                        )

                        for w in wallets:
                            status_text = 'Verified' if w['is_verified'] else 'Pending'
                            default_tag = ' **(Default)**' if w['is_default'] else ''
                            label = w.get('label', '') or ''
                            label_line = f"\n> *{label}*" if label else ''
                            address = w['address']
                            short_addr = f"`{address[:8]}...{address[-4:]}`"

                            value = (
                                f"> **Status**: `{w['status'].title()}` {status_text}\n"
                                f"> **Address**: {short_addr}\n"
                                f"> **Provider**: `{w.get('provider', 'N/A')}`"
                                f"{label_line}"
                            )

                            embed.add_field(
                                name=f"{w['id']}{default_tag}",
                                value=value,
                                inline=False,
                            )

                        return embed
                    else:
                        err_data = await resp.json()
                        return error_embed(
                            message=err_data.get('error', 'Could not load wallets. Please try again.')
                        )
            except Exception:
                logger.exception("Failed to fetch wallet list")
                return error_embed(
                    message='Something went wrong. Please try again later.'
                )

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(WalletListCommand(bot))
