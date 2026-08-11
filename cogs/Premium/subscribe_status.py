import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import BrandColor, create_embed, error_embed
from utils.http import get_http_session

logger = logging.getLogger(__name__)


class SubscribeStatus(commands.Cog):
    """``/subscribe status``, Check subscription status."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="subscribe_status", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def subscribe_status(self, interaction: discord.Interaction):
        async def callback(user_data):
            # discord_id isn't echoed by the backend, so inject it here
            user_data['discord_id'] = str(interaction.user.id)
            url = f"{BACKEND_URL}premium/bot/active/"
            params = {'discord_id': user_data['discord_id']}
            # Only show status for the user's active role
            role = user_data.get('active_role')
            if role in ('freelancer', 'client'):
                params['role'] = role
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            session = get_http_session()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        try:
                            err = await resp.json()
                            msg = err.get('error', 'Could not load subscription status.')
                        except Exception:
                            msg = 'Could not load subscription status.'
                        return error_embed(message=msg)
                    data = await resp.json()
            except Exception:
                logger.exception("Could not load premium status")
                return error_embed(message="Could not load subscription status.")

            has_premium = data.get('has_active_premium', False)
            tier = data.get('tier')
            expires_at = data.get('expires_at')
            billing_interval = data.get('billing_interval')
            is_gifted = data.get('is_gifted', False)

            if not has_premium or not tier:
                embed = create_embed(
                    title="Subscription Status",
                    description=(
                        "> ***Subscription Status** — free tier*\n"
                        "**Plan:** `Free`\n"
                        "\n"
                        "> __Browse the available plans and subscribe to unlock Premium features.__"
                    ),
                    color=BrandColor.PRIMARY,
                    footer="Xentra • Premium",
                )
                return embed

            # Premium subscription
            tier_label = tier.replace('_', ' ').title()

            interval_label = (
                billing_interval.replace('_', ' ').title()
                if billing_interval
                else '\u2014'
            )

            remaining_str = 'Lifetime'
            expires_display = '\u2014'
            if expires_at:
                try:
                    expiry = datetime.fromisoformat(
                        expires_at.replace('Z', '+00:00')
                    )
                    now = datetime.now(timezone.utc)
                    remaining = expiry - now
                    if remaining.total_seconds() <= 0:
                        remaining_str = 'Expired'
                    else:
                        days = remaining.days
                        hours = remaining.seconds // 3600
                        parts = []
                        if days > 0:
                            parts.append(f'{days}d')
                        if hours > 0:
                            parts.append(f'{hours}h')
                        remaining_str = ' '.join(parts) if parts else '<1h'
                    expires_display = (
                        f"<t:{int(expiry.timestamp())}:R>"
                    )
                except Exception:
                    remaining_str = expires_at
                    expires_display = expires_at

            embed = create_embed(
                title='Premium Subscription',
                description=(
                    '> ***Premium Subscription** — active*\n'
                    f'**Tier:** `{tier_label}`\n'
                    f'**Billing:** `{interval_label}`\n'
                    f'**Remaining:** `{remaining_str}`\n'
                    f'**Expires:** `{expires_display}`'
                    + (f'\n**Type:** `Gifted`' if is_gifted else '')
                    + '\n\n'
                    '> __Premium features are active across all Xentra services.__'
                ),
                color=BrandColor.SUCCESS,
                footer='Xentra • Premium',
            )
            return embed

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(SubscribeStatus(bot))
