"""
``/interview complain`` — Submit a complaint in the selected interview room.

Flow:
  1. Mutual exclusivity of ``message_id`` / ``complain_id`` is validated upfront.
  2. ``validate_and_respond`` validates the user, role, and room context.
  3. Shows a "Write Complaint" button after room verification passes.
  4. Button opens the complaint Modal.
  5. Modal collects text; on submit it saves the complaint and notifies the other party.
  6. If the other party has DMs disabled, logs a failed delivery record with
     message_type='notification' and complain_id set so it can be retried.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import (
    sync_cog_commands,
    validate_and_respond,
    is_author,
    fetch_selected_room,
)
from utils.embeds import (
    BrandColor,
    create_embed,
    error_embed,
    success_embed,
    dm_blocked_embed,
)
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery

logger = logging.getLogger('bot.rooms.interview_complain')


# ──────────────────────────────────────────────────────────────────────
# Start View — opens the modal after room verification
# ──────────────────────────────────────────────────────────────────────


class ComplainStartView(discord.ui.View):
    """View shown after room verification — user clicks to open the complaint modal."""

    def __init__(
        self,
        user_data: dict,
        room_data: dict,
        message_id: str = '',
        complain_id: str = '',
    ) -> None:
        super().__init__(timeout=120)
        self.user_data = user_data
        self.room_data = room_data
        self.message_id = message_id
        self.complain_id = complain_id

    @discord.ui.button(label='Write Complaint', style=discord.ButtonStyle.danger)
    async def write_complaint(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return

        modal = InterviewComplainModal(
            user_data=self.user_data,
            room_data=self.room_data,
            message_id=self.message_id,
            complain_id=self.complain_id,
        )
        await interaction.response.send_modal(modal)
        self.stop()


# ──────────────────────────────────────────────────────────────────────
# Modal — complaint text input (opens after room verification)
# ──────────────────────────────────────────────────────────────────────


class InterviewComplainModal(discord.ui.Modal, title='Submit Complaint'):
    """Modal that collects complaint text.  Room already verified before opening."""

    complaint = discord.ui.TextInput(
        label='Complaint',
        style=discord.TextStyle.paragraph,
        placeholder='Describe your complaint here…',
        required=True,
        max_length=4000,
    )

    def __init__(
        self,
        user_data: dict,
        room_data: dict,
        message_id: str = '',
        complain_id: str = '',
    ) -> None:
        super().__init__(timeout=300)
        self.user_data = user_data
        self.room_data = room_data
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
        active_role = self.user_data.get('active_role', '')
        room_data = self.room_data

        # 1. If parameters provided, verify they exist in the room
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

        # 2. Save complaint via backend
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

        # 3. Determine sender display name
        sender_name = (
            room_data.get('client_name')
            if active_role == 'client'
            else room_data.get('freelancer_name', 'Freelancer')
        )

        # 4. Send notification to other party
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

        # 5. Success
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

        async def callback(user_data: dict):
            active_role = user_data.get('active_role')
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            # ── 1. Fetch selected room ──────────────────────────────────
            room_data = await fetch_selected_room(
                discord_id=interaction.user.id,
                role=active_role,
                room_type='interview',
                headers=headers,
            )
            if room_data is None:
                return error_embed(
                    message='No selected interview room found. '
                    'Use `/switch room` to select one.',
                )

            # ── 2. Show start view with Write Complaint button ──────────
            embed = create_embed(
                title='Interview Complaint',
                description=(
                    'You are about to submit a complaint in the interview chat.\n\n'
                    f'**Room:** `{room_data.get("room_id", "")}`\n'
                    f'**Job:** {room_data.get("job_title", "")}\n\n'
                    'Click **Write Complaint** to compose your complaint.'
                ),
                color=BrandColor.PRIMARY,
                footer='Xentra • Room System',
            )

            view = ComplainStartView(
                user_data,
                room_data,
                message_id=message_id or '',
                complain_id=complain_id or '',
            )
            view.author_id = interaction.user.id

            return embed, view

        await validate_and_respond(interaction, callback)


# ── setup ────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewComplain(bot))
    logger.info('InterviewComplain cog loaded')
