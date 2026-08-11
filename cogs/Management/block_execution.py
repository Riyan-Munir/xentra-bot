import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.retry import validation_fail
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, info_embed, throttled_embed, loading_embed
from utils.userid_resolver import resolve_user_id
from utils.analytics_collector import AnalyticsCollector
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.block_execution')


class BlockExecutionSelect(discord.ui.Select):
    def __init__(self, identifier):
        self.identifier = identifier
        self._all_options = [
            discord.SelectOption(label="Freelancer", value="freelancer", description="Block as Freelancer"),
            discord.SelectOption(label="Client", value="client", description="Block as Client")
        ]
        super().__init__(placeholder="Select the target role.", options=self._all_options)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        self.view.selected_role = self.values[0]
        for option in self.options:
            option.default = (option.value == self.values[0])
        # Mirror selection in dropdown placeholder
        selected_label = next(
            (opt.label for opt in self._all_options if opt.value == self.values[0]),
            self.values[0],
        )
        self.placeholder = f"✓ {selected_label}"
        await interaction.response.edit_message(view=self.view)


class BlockExecutionView(discord.ui.View):
    def __init__(self, identifier, callback_func, user_data):
        super().__init__(timeout=60)
        self.author_id: int | None = None
        self.user_data = user_data
        self.identifier = identifier
        self.callback_func = callback_func
        self.selected_role = None
        self._done = False
        self.add_item(BlockExecutionSelect(identifier))

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label="Proceed", style=discord.ButtonStyle.success)
    async def send_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        if self._done:
            return
        if not self.selected_role:
            await validation_fail(interaction, message="Select a role first.")
            return
        self._done = True
        await self.callback_func(interaction, self.selected_role, self.identifier, self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        self.stop()
        err = info_embed(
            message=(
                "> ***Block execution has been cancelled.***\n"
                "> __Nothing was changed. No one was blocked.__"
            )
        )
        await interaction.response.edit_message(content=None, embed=err, view=None)


class BlockExecution(commands.Cog):
    """``/block_execution``, Revoke bot command execution permission from a user."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="block_execution", description="...")
    @app_commands.checks.cooldown(1, 10, key=lambda i: i.user.id)
    async def block_execution(self, interaction: discord.Interaction, user_id: str):

        async def premium_role_callback(inter, role, identifier, view):
            resolve_url = f"{BACKEND_URL}users/resolve-id/"
            packet = BotPacketFactory.create_packet(
                packet_type="user_resolve_id",
                data={'raw_id': f"{role}:{identifier}"},
                provider="bot"
            )
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            async with session.post(resolve_url, json=packet.to_dict(), headers=headers) as resp:
                    if resp.status in (200, 201):
                        res = await resp.json()
                        embed = await do_block(inter, res['role'], res['canonical_id'])
                        await inter.response.edit_message(content=None, embed=embed, view=None)
                    else:
                        err = await resp.json()
                        err_embed = error_embed(message=err.get('error', 'Could not block this user. Provided ID is not valid for the selected role.'))
                        await inter.response.edit_message(content=None, embed=err_embed, view=None)

        async def do_block(inter, role, canonical_id):
            if not inter.guild_id:
                return error_embed(message="Could not proceed. This command can only be run inside a server.")

            url = f"{BACKEND_URL}guilds/block-execution/"
            packet = BotPacketFactory.create_packet(
                packet_type="guild_block_execution",
                data={
                    'guild_id': str(inter.guild_id),
                    'guild_name': str(inter.guild.name),
                    'profile_id': canonical_id,
                    'role': role,
                    'executer_discord_id': str(inter.user.id)
                },
                provider="bot"
            )
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        # Fire-and-forget analytics event for block action
                        AnalyticsCollector.log_admin_action(
                            interaction=inter,
                            target_info={
                                'target_role': data.get('target_role', role),
                                'target_display_name': data.get('target_display_name', ''),
                                'target_display_id': data.get('target_display_id', canonical_id),
                            },
                            event_type="guild_block_execution",
                            admin_profile_id='',
                        )
                        return success_embed(message=data.get('message', 'Execution has been blocked.'))
                    elif resp.status == 429:
                        err = await resp.json()
                        return throttled_embed(err.get('retry_after', 10))
                    else:
                        err = await resp.json()
                        return error_embed(message=err.get('error', 'Could not block this user.'))

        async def block_callback(user_data):
            if not interaction.guild_id:
                return error_embed(message="Could not proceed. This command can only be run inside a server.")

            result = resolve_user_id(user_id)
            if result.is_system:
                resolve_url = f"{BACKEND_URL}users/resolve-id/"
                packet = BotPacketFactory.create_packet(
                    packet_type="user_resolve_id",
                    data={'raw_id': result.normalized},
                    provider="bot"
                )
                headers = {'X-Webhook-Token': WEBHOOK_SECRET}

                session = get_http_session()
                async with session.post(resolve_url, json=packet.to_dict(), headers=headers) as resp:
                        if resp.status in (200, 201):
                            res = await resp.json()
                            return await do_block(interaction, res['role'], res['canonical_id'])
                        else:
                            err = await resp.json()
                            return error_embed(message=err.get('error', 'Could not block this user. No user found with provided ID.'))
            else:
                # Premium ID, show role selection dropdown
                view = BlockExecutionView(result.normalized, premium_role_callback, user_data)
                view.author_id = interaction.user.id
                embed = create_embed(
                    title="Role Selection Required",
                    description=(
                        f"> ***Select the role perspective for this Premium ID.***\n"
                        f"**ID:** `{result.original}` — custom Premium ID\n"
                        "\n"
                        "> __Use the dropdown to pick a role, then click Proceed.__"
                    ),
                    color=BrandColor.ACCENT,
                    footer="Xentra • Management"
                )
                return embed, view

        await validate_and_respond(interaction, block_callback)


async def setup(bot):
    await bot.add_cog(BlockExecution(bot))
