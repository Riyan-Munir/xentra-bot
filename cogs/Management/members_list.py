import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.pagination import PaginationView
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, loading_embed
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.profile_mgmt')

class MembersListView(PaginationView):
    """Members list uses 0‑based page index — overrides prev/next/update_buttons."""
    def __init__(self, viewer_data, entries):
        self.entries = entries
        self.page_size = 5
        total_pages = max(1, (len(entries) + self.page_size - 1) // self.page_size)
        super().__init__(current_page=1, total_pages=total_pages, user_data=viewer_data)
        self._member_current = 0
        self.update_buttons()

    def update_buttons(self, embed: discord.Embed = None):
        self.clear_items()
        if self.total_pages > 1:
            prev = discord.ui.Button(
                label='Previous',
                style=discord.ButtonStyle.gray,
                disabled=self._member_current <= 0,
            )
            prev.callback = self.prev_page
            self.add_item(prev)

            nxt = discord.ui.Button(
                label='Next',
                style=discord.ButtonStyle.gray,
                disabled=self._member_current >= self.total_pages - 1,
            )
            nxt.callback = self.next_page
            self.add_item(nxt)

        close = discord.ui.Button(label='Close', style=discord.ButtonStyle.red)
        close.callback = self.close_view
        self.add_item(close)
        # Admin broadcast button — imported already via PaginationView base

    async def prev_page(self, interaction: discord.Interaction):
        self._member_current -= 1
        await self.update_message(interaction)

    async def next_page(self, interaction: discord.Interaction):
        self._member_current += 1
        await self.update_message(interaction)

    def build_embed(self):
        start = self._member_current * self.page_size
        page_items = self.entries[start:start + self.page_size]
        
        embed = create_embed(
            title="Server Members Registry",
            description=f"Viewing active members (Page {self._member_current + 1}/{self.total_pages})",
            color=BrandColor.PRIMARY,
            footer=f"Xentra • Total Members Listed: {len(self.entries)}"
        )
        
        current_heading = None
        for item in page_items:
            heading = item.get('heading')
            is_new_heading = heading != current_heading
            current_heading = heading
            
            if is_new_heading:
                embed.add_field(name='\u200b', value=' ', inline=False)
                name = f"**{heading}**"
                value = '\n' + item['text'] + '\n'
            else:
                name = '\u200b'
                value = item['text'] + '\n'
            
            embed.add_field(
                name=name,
                value=value,
                inline=False
            )
        return embed

class MembersList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="members_list", description="...")
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    async def members_list(self, interaction: discord.Interaction, client: bool | None = None, freelancer: bool | None = None, server_admin: bool | None = None):
        """
        members list command for server_admins. Parameters are optional booleans to filter roles.
        """
        
        async def members_callback(user_data):
            # Early enforcement: ensure command is run in a server
            if not interaction.guild_id:
                return error_embed(message="This command can only be run inside a server.")

            # Respect backend-provided executor allowance if present
            if user_data.get('is_allowed_executor') is False:
                return error_embed(message="This command is not available for your role. Run `/help` for details.")

            # Check if all parameters are explicitly false
            if client is False and freelancer is False and server_admin is False:
                return error_embed(message="List cannot be displayed with no response.")

            url = f"{BACKEND_URL}guilds/members/"
            params = {'guild_id': interaction.guild_id, 'discord_id': str(interaction.user.id)}
            
            # Determine what to show
            # If any param is explicitly true, show only those true ones
            has_any_true = client is True or freelancer is True or server_admin is True
            none_passed = client is None and freelancer is None and server_admin is None
            
            if none_passed:
                # No parameters given, show all
                params['client'] = '1'
                params['freelancer'] = '1'
                params['server_admin'] = '1'
            elif has_any_true:
                # Show only the ones explicitly set to true
                if client is True:
                    params['client'] = '1'
                if freelancer is True:
                    params['freelancer'] = '1'
                if server_admin is True:
                    params['server_admin'] = '1'
            else:
                # No true params, but some are false and some are None
                # Show all that are NOT explicitly false
                if client is not False:
                    params['client'] = '1'
                if freelancer is not False:
                    params['freelancer'] = '1'
                if server_admin is not False:
                    params['server_admin'] = '1'

            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            try:
                session = get_http_session()
                async with session.get(url, params=params, headers=headers) as resp:
                        if resp.status != 200:
                            data = await resp.json()
                            return error_embed(message=f"Could not fetch members: {data.get('error', 'Unknown error')}")
                        data = await resp.json()
            except Exception as e:
                logger.error(f"Error fetching members list: {e}")
                return error_embed(message="Something went wrong. Please try again.")

            # Build sequence based on what we're showing
            # If any param is true, show only true ones; if no true params, show non-false ones; if no params passed, show all
            sequence = []
            show_all = client is None and freelancer is None and server_admin is None
            has_any_true = client is True or freelancer is True or server_admin is True
            
            if server_admin is True or (show_all or (not has_any_true and server_admin is not False)):
                sequence.append(('Server Admins', data.get('server_admins', [])))
            if client is True or (show_all or (not has_any_true and client is not False)):
                sequence.append(('Clients', data.get('clients', [])))
            if freelancer is True or (show_all or (not has_any_true and freelancer is not False)):
                sequence.append(('Freelancers', data.get('freelancers', [])))

            # Flatten into display entries with role headings
            entries = []
            for heading, items in sequence:
                if not items:
                    continue
                for it in items:
                    if heading == 'Server Admins':
                        tags = []
                        if it.get('is_owner'): tags.append('Owner')
                        if it.get('is_mod'): tags.append('Moderator')
                        tag_str = ' • '.join(tags) if tags else 'Member'
                        
                        exec_allowed = it.get('is_allowed_executor', True)
                        exec_status = 'Allowed' if exec_allowed else 'Restricted'
                        
                        details = (
                            f"> **Name**: `{it.get('display_name')}`\n"
                            f"> **ID**: `{it.get('id')}`\n"
                            f"> **Role**: {tag_str}\n"
                            f"> **Execution**: {exec_status}"
                        )
                        entries.append({'type': 'row', 'heading': heading, 'text': details})
                    else:
                        exec_allowed = it.get('is_allowed_executor', True)
                        exec_status = 'Allowed' if exec_allowed else 'Restricted'
                        points = it.get('points_contribution', 0)
                        
                        details = (
                            f"> **Name**: `{it.get('display_name')}`\n"
                            f"> **ID**: `{it.get('id')}` • **Points**: `{points}`\n"
                            f"> **Execution**: {exec_status}"
                        )
                        entries.append({'type': 'row', 'heading': heading, 'text': details})

            # Pagination: 5 items per page
            page_size = 5
            total = len(entries)
            total_pages = max(1, (total + page_size - 1) // page_size)

            view = MembersListView(user_data, entries)
            view.author_id = interaction.user.id
            embed = view.build_embed()
            return (embed, view)

        await validate_and_respond(interaction, members_callback)

async def setup(bot):
    await bot.add_cog(MembersList(bot))
