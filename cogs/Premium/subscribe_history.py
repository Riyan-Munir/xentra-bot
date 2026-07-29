import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import BrandColor, create_embed, error_embed, info_embed
from utils.http import get_http_session
from utils.pagination import PaginationView

logger = logging.getLogger(__name__)

HISTORY_PAGE_SIZE = 10


class HistoryPaginationView(PaginationView):
    """Paginated view for subscription history records."""

    def __init__(self, user_data, current_page, total_pages, total_count):
        super().__init__(current_page, total_pages, user_data)
        self.author_id = None
        self.total_count = total_count
        self._page_data: list[dict] = []

    async def change_page(self, interaction: discord.Interaction, new_page: int):
        if not is_author(interaction, self):
            return
        await interaction.response.defer()

        url = f"{BACKEND_URL}premium/bot/history/"
        params = {
            'discord_id': self.user_data['discord_id'],
            'page': new_page,
            'page_size': HISTORY_PAGE_SIZE,
        }
        role = self.user_data.get('active_role')
        if role in ('freelancer', 'client'):
            params['role'] = role
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        session = get_http_session()
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                else:
                    try:
                        err = await resp.json()
                        msg = err.get('error', 'Could not fetch history.')
                    except Exception:
                        msg = 'Could not fetch history.'
                    await interaction.edit_original_response(
                        embed=error_embed(msg), view=self,
                    )
                    return
        except Exception:
            logger.exception("Failed to fetch premium history page %s", new_page)
            await interaction.edit_original_response(
                embed=error_embed("Could not load this page."), view=self,
            )
            return

        self.current_page = new_page
        self.total_pages = max(1, (data.get('count', 0) + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)
        self._page_data = data.get('results', [])
        embed = self.build_embed()
        self.update_buttons(embed)
        await interaction.edit_original_response(embed=embed, view=self)

    def build_embed(self) -> discord.Embed:
        embed = create_embed(
            title='Subscription History',
            description=(
                f'Page {self.current_page} of {self.total_pages} '
                f'(\u2022 {self.total_count} total record(s))'
            ),
            color=BrandColor.PRIMARY,
            footer='Xentra \u2022 Subscription History',
        )
        if not self._page_data:
            embed.add_field(
                name='No Records',
                value='No subscription purchases found.',
                inline=False,
            )
            return embed

        for record in self._page_data:
            plan_name = record.get('plan_name') or '\u2014'
            interval = record.get('billing_interval_display') or record.get('billing_interval') or '\u2014'
            payment_type = record.get('payment_type_display') or record.get('payment_type') or '\u2014'
            activated = record.get('activated_at') or '\u2014'
            expires = record.get('expires_at') or '\u2014'

            gift_text = ''
            if record.get('gift_message'):
                gift_text = ' (Gifted)'

            embed.add_field(
                name=f'{plan_name}{gift_text}',
                value=(
                    f'> **Interval**: `{interval}`\n'
                    f'> **Type**: `{payment_type}`\n'
                    f'> **Activated**: `{activated}`\n'
                    f'> **Expires**: `{expires}`'
                ),
                inline=False,
            )

        return embed


class SubscribeHistory(commands.Cog):
    """``/subscribe history``, View subscription history."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="subscribe_history", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def subscribe_history(self, interaction: discord.Interaction):
        async def callback(user_data):
            # discord_id isn't echoed by the backend, so inject it here
            user_data['discord_id'] = str(interaction.user.id)
            url = f"{BACKEND_URL}premium/bot/history/"
            params = {
                'discord_id': user_data['discord_id'],
                'page': 1,
                'page_size': HISTORY_PAGE_SIZE,
            }
            # Only show history for the user's active role
            role = user_data.get('active_role')
            if role in ('freelancer', 'client'):
                params['role'] = role
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            session = get_http_session()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                    else:
                        try:
                            err = await resp.json()
                            msg = err.get('error', 'Could not fetch history.')
                        except Exception:
                            msg = 'Could not fetch history.'
                        return error_embed(msg)
            except Exception:
                logger.exception("Could not fetch premium history")
                return error_embed("Could not fetch history.")

            total_count = data.get('count', 0)
            total_pages = max(1, (total_count + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)
            results = data.get('results', [])

            view = HistoryPaginationView(
                user_data=user_data,
                current_page=1,
                total_pages=total_pages,
                total_count=total_count,
            )
            view.author_id = interaction.user.id
            view._page_data = results
            embed = view.build_embed()

            if total_pages <= 1 and len(results) == 0:
                if total_count == 0:
                    return info_embed('No subscription purchases found.')
                return embed

            view.update_buttons(embed)
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(SubscribeHistory(bot))
