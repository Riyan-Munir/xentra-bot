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


class SubscribeStatusCommand(commands.Cog):
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
                            msg = err.get('error', 'Failed to fetch subscription status.')
                        except Exception:
                            msg = 'Failed to fetch subscription status.'
                        return error_embed(msg)
                    data = await resp.json()
            except Exception:
                logger.exception("Failed to fetch premium status")
                return error_embed("An unexpected error occurred. Please try again later.")

            has_premium = data.get('has_active_premium', False)
            tier = data.get('tier')
            expires_at = data.get('expires_at')
            billing_interval = data.get('billing_interval')
            is_gifted = data.get('is_gifted', False)

            if not has_premium or not tier:
                embed = create_embed(
                    title="Subscription Status",
                    description="You are currently on the **Free Tier**.",
                    color=BrandColor.PRIMARY,
                    footer="Xentra \u2022 Subscription",
                )
                embed.add_field(name="Plan", value="Free", inline=True)
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
                description='You have an active **Premium** subscription!',
                color=BrandColor.SUCCESS,
                footer='Xentra \u2022 Premium',
            )
            embed.add_field(name='Tier', value=tier_label, inline=True)
            embed.add_field(name='Billing', value=interval_label, inline=True)
            embed.add_field(name='Remaining', value=remaining_str, inline=True)
            embed.add_field(name='Expires', value=expires_display, inline=True)
            if is_gifted:
                embed.add_field(name='Type', value='Gifted', inline=True)
            return embed

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(SubscribeStatusCommand(bot))
