"""
``/interview complain`` — Submit a complaint in the selected interview room.

Flow:
  1. Command handler validates mutual exclusivity of message_id / complain_id.
  2. Opens the complaint Modal directly (same pattern as interview_message).
  3. Modal collects complaint text; on submit it fetches user + room data from
     the backend, verifies any provided reference IDs, saves the complaint, and
     notifies the other party.
  4. If the other party has DMs disabled, logs a failed delivery record with
     message_type='notification' and complain_id set so it can be retried.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import sync_cog_commands, fetch_selected_room
from utils.embeds import error_embed, success_embed, dm_blocked_embed
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery

logger = logging.getLogger('bot.rooms.interview_complain')


class InterviewComplainModal(discord.ui.Modal, title='Submit Complaint'):
    """Modal that collects complaint text. Opens immediately on /interview complain."""

    complaint = discord.ui.TextInput(
        label='Complaint',
        style=discord.TextStyle.paragraph,
        placeholder='Describe your complaint here…',
        required=True,
        max_length=4000,
    )

    def __init__(
        self,
        message_id: str = '',
        complain_id: str = '',
    ) -> None:
        super().__init__(timeout=300)
        self.message_id = message_id
        self.complain_id = complain_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        complaint_text = self.complaint.value.strip()
        if not complaint_text:
            await interaction.response.send_message(
                embed=error_embed(message='Complaint text cannot be empty.'),
                ephemeral=True,
            )
            return

        is_dm = interaction.guild is None
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        # 1. Fetch user data
        try:
            async with session.get(
                f'{BACKEND_URL}users/bot/{interaction.user.id}/',
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    await interaction.response.send_message(
                        embed=error_embed(message='Authentication failed. Please try again.'),
                        ephemeral=not is_dm,
                    )
                    return
                user_data = await resp.json()
        except Exception:
            logger.exception('Failed to fetch user data')
            await interaction.response.send_message(
                embed=error_embed(message='System error. Please try again later.'),
                ephemeral=not is_dm,
            )
            return

        # 2. Security checks
        if user_data.get('has_pending_hacking'):
            from config import FRONTEND_URL
            await interaction.response.send_message(
                embed=error_embed(
                    message=(
                        f'A security notification requires your attention on the Xentra '
                        f'Dashboard.\n'
                        f'Visit **{FRONTEND_URL}** and acknowledge the alert '
                        f'to restore access to all bot commands.'
                    ),
                ),
                ephemeral=not is_dm,
            )
            return

        if not user_data.get('is_allowed_executor', True):
            await interaction.response.send_message(
                embed=error_embed(
                    message='You are not permitted to execute commands in this server. '
                    'Contact moderators for more information.'
                ),
                ephemeral=not is_dm,
            )
            return

        active_role = user_data.get('active_role', '')
        if not active_role or active_role == 'non_bot_user':
            await interaction.response.send_message(
                embed=error_embed(
                    message='You need a registered client or freelancer profile to submit '
                    'complaints. Visit the Xentra Dashboard to set up your account.'
                ),
                ephemeral=not is_dm,
            )
            return

        # 3. Channel restriction check (server only)
        if not is_dm:
            assigned_channel_id = user_data.get('assigned_channel_id')
            if assigned_channel_id and str(interaction.channel_id) != str(assigned_channel_id):
                target = interaction.guild.get_channel(int(assigned_channel_id))
                ch_name = target.mention if target else f'ID {assigned_channel_id}'
                await interaction.response.send_message(
                    embed=error_embed(
                        message=f'Commands are restricted to {ch_name}.'
                    ),
                    ephemeral=not is_dm,
                )
                return

        # 4. Fetch selected room via shared resolver
        room_data = await fetch_selected_room(
            discord_id=interaction.user.id,
            role=active_role,
            room_type='interview',
            headers=headers,
        )

        if room_data is None:
            await interaction.response.send_message(
                embed=error_embed(
                    message='No selected interview room found. '
                    'Use `/switch room` to select one.',
                ),
                ephemeral=not is_dm,
            )
            return

        # 5. If parameters provided, verify they exist in the room
        if self.message_id or self.complain_id:
            verify_payload = {'room_id': room_data.get('room_id', '')}
            if self.message_id:
                verify_payload['msg_id'] = self.message_id
            if self.complain_id:
                verify_payload['complain_id'] = self.complain_id

            try:
                async with session.post(
                    f'{BACKEND_URL}rooms/bot/verify-room-reference/',
                    json=verify_payload,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        err_data = await resp.json()
                        err_msg = err_data.get('error', 'Reference verification failed.')
                        await interaction.response.send_message(
                            embed=error_embed(message=err_msg),
                            ephemeral=not is_dm,
                        )
                        return
            except Exception:
                logger.exception('Failed to verify room reference')
                await interaction.response.send_message(
                    embed=error_embed(
                        message='Unable to verify the reference ID. Please try again later.'
                    ),
                    ephemeral=not is_dm,
                )
                return

        # 6. Save complaint via backend
        save_payload = {
            'discord_id': str(interaction.user.id),
            'role': active_role,
            'room_id': room_data.get('room_id', ''),
            'complaint_data': complaint_text,
        }
        if self.message_id:
            save_payload['target_msg_id'] = self.message_id
        if self.complain_id:
            save_payload['target_complain_id'] = self.complain_id

        try:
            async with session.post(
                f'{BACKEND_URL}rooms/bot/save-complain/',
                json=save_payload,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    err_data = await resp.json()
                    err_msg = err_data.get('error', 'Failed to save complaint.')
                    await interaction.response.send_message(
                        embed=error_embed(message=err_msg),
                        ephemeral=not is_dm,
                    )
                    return
                save_data = await resp.json()
                complaint_id = save_data.get('complaint_id', '')
                other_discord_id = save_data.get('other_discord_id', '')
        except Exception:
            logger.exception('Failed to save complaint to backend')
            await interaction.response.send_message(
                embed=error_embed(
                    message='Could not save the complaint due to a system error. '
                    'Please try again later.',
                ),
                ephemeral=not is_dm,
            )
            return

        # 7. Determine sender display name
        sender_name = (
            room_data.get('client_name')
            if active_role == 'client'
            else room_data.get('freelancer_name', 'Freelancer')
        )

        # 8. Send notification to other party
        if other_discord_id:
            notify_data = {
                'discord_id': other_discord_id,
                'room_id': room_data.get('room_id', ''),
                'job_title': room_data.get('job_title', ''),
                'command_name': 'interview_complain',
                'executor_name': sender_name,
                'complaint_id': complaint_id,
                'complaint_data': complaint_text,
            }
            if self.message_id:
                notify_data['target_msg_id'] = self.message_id
            if self.complain_id:
                notify_data['target_complain_id'] = self.complain_id

            delivery_ok = await handle_system_message(
                message_type='room_interview_message',
                data=notify_data,
                bot=interaction.client,
            )

            if not delivery_ok:
                await log_failed_delivery(
                    room_id=room_data.get('room_id', ''),
                    message_type='notification',
                    target_discord_id=other_discord_id,
                    complain_id=complaint_id,
                    session=session,
                    headers=headers,
                )

                # Determine receiver name for the dm_blocked message
                if active_role == 'client':
                    receiver_name = room_data.get('freelancer_name', 'Freelancer')
                else:
                    receiver_name = room_data.get('client_name', 'Client')

                await interaction.response.send_message(
                    embed=dm_blocked_embed(
                        attempted_action='your complaint notification',
                        receiver_name=receiver_name,
                    ),
                    ephemeral=not is_dm,
                )
                return

        # 9. Success
        success_msg = f'Complaint submitted in room `{room_data.get("room_id", "")}`.'
        if complaint_id:
            success_msg += f' (ID: `{complaint_id}`)'

        await interaction.response.send_message(
            embed=success_embed(message=success_msg),
            ephemeral=not is_dm,
        )


# ── Cog ──────────────────────────────────────────────────────────────


class InterviewComplain(commands.Cog):
    """``/interview complain`` — Submit a complaint in the interview chat."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(
        name='interview_complain',
        description='Submit a complaint in the selected interview room.',
    )
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def interview_complain(
        self,
        interaction: discord.Interaction,
        message_id: str | None = None,
        complain_id: str | None = None,
    ) -> None:
        """Submit a complaint in the selected interview room.

        Parameters
        ----------
        message_id : optional
            Link this complaint to a specific message ID in the room.
        complain_id : optional
            Link this complaint to a specific complaint ID in the room.
        """
        # Validate mutual exclusivity — only one of message_id / complain_id may be set
        if message_id and complain_id:
            await interaction.response.send_message(
                embed=error_embed(
                    message='You can link a complaint to either a **message** or a '
                    'previous **complaint**, but not both. Please use only one of the '
                    '`message_id` or `complain_id` parameters.'
                ),
                ephemeral=True,
            )
            return

        # Open the modal with the provided parameters
        modal = InterviewComplainModal(
            message_id=message_id or '',
            complain_id=complain_id or '',
        )
        await interaction.response.send_modal(modal)


# ── setup ────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewComplain(bot))
    logger.info('InterviewComplain cog loaded')
