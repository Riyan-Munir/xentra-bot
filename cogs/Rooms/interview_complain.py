"""
``/interview complain``, Submit a complaint in the selected interview room.

Flow:
  1. Mutual exclusivity of ``message_id`` / ``complain_id`` is validated upfront.
  2. ``validate_and_respond`` validates the user, role, and room context.
  3. Shows a "Write Complaint" button after room verification passes.
  4. Button opens the complaint Modal.
  5. Modal collects text and saves the complaint for staff review.
     Complaints are private and never shared with the other room participant.
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
)
from utils.embeds import (
    BrandColor,
    create_embed,
    error_embed,
    success_embed,
    info_embed,
)
from utils.http import get_http_session

logger = logging.getLogger('bot.rooms.interview_complain')


# ──────────────────────────────────────────────────────────────────────
# Start View, opens the modal after room verification
# ──────────────────────────────────────────────────────────────────────


class ComplainStartView(discord.ui.View):
    """View shown after room verification, user clicks to open the complaint modal."""

    def __init__(
        self,
        user_data: dict,
        room_data: dict,
        message_id: str = '',
        complain_id: str = '',
    ) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.user_data = user_data
        self.room_data = room_data
        self.message_id = message_id
        self.complain_id = complain_id
        self._original_interaction: discord.Interaction | None = None

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label='Proceed', style=discord.ButtonStyle.success)
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
        modal._original_interaction = self._original_interaction
        await interaction.response.send_modal(modal)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Complaint cancelled.'),
            view=None,
        )


# ──────────────────────────────────────────────────────────────────────
# Modal, complaint text input (opens after room verification)
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
        self._original_interaction: discord.Interaction | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        complaint_text = self.complaint.value.strip()
        if not complaint_text:
            await interaction.response.defer()
            await self._edit_done(
                error_embed(message='Could not submit the complaint. The complaint text cannot be empty.'),
            )
            return

        # Defer so we can edit the original response later
        await interaction.response.defer()

        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
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
                        await self._edit_done(
                            error_embed(message=err_msg),
                        )
                        return
            except Exception:
                logger.exception('Failed to verify room reference')
                await self._edit_done(
                    error_embed(
                        message='Could not verify the reference ID.'
                    ),
                )
                return

        # 2. Save complaint via backend
        save_payload = {
            'discord_id': str(interaction.user.id),
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
                    err_msg = err_data.get('error', 'Could not save the complaint.')
                    await self._edit_done(
                        error_embed(message=err_msg),
                    )
                    return
                save_data = await resp.json()
                complaint_id = save_data.get('complaint_id', '')
        except Exception:
            logger.exception('Failed to save complaint to backend')
            await self._edit_done(
                error_embed(
                    message='Could not save the complaint.',
                ),
            )
            return

        # 3. Success
        success_msg = f'Complaint submitted in room `{room_data.get("room_id", "")}`.'
        if complaint_id:
            success_msg += f' (ID: `{complaint_id}`)'

        await self._edit_done(
            success_embed(message=success_msg),
        )

    async def _edit_done(self, embed: discord.Embed) -> None:
        """Edit the original interaction message to show the result."""
        try:
            await self._original_interaction.edit_original_response(
                embed=embed,
                view=None,
            )
        except Exception:
            pass


# ── Cog ──────────────────────────────────────────────────────────────


class InterviewComplain(commands.Cog):
    """``/interview complain``, Submit a complaint in the interview chat."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(
        name='interview_complain',
        description='...',
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
        # Validate mutual exclusivity, only one of message_id / complain_id may be set
        if message_id and complain_id:
            is_dm = interaction.guild is None
            await interaction.response.send_message(
                embed=error_embed(
                    message='Could not submit the complaint. A complaint can reference '
                    'a message or another complaint, but not both.'
                ),
                ephemeral=not is_dm,
            )
            return

        async def callback(user_data: dict):
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            # ── 1. Use auto-fetched selected room ─────────────────────────
            room_data = user_data['_selected_room']

            # ── 2. Show start view with Write Complaint button ──────────
            embed = create_embed(
                title='Interview Complaint',
                description=(
                    '> You are about to submit a complaint in the interview chat.\n\n'
                    f'> **Room:** `{room_data.get("room_id", "")}`\n'
                    f'> **Job:** {room_data.get("job_title", "")}\n\n'
                    '> Click **Write Complaint** to compose your complaint.'
                ),
                color=BrandColor.PRIMARY,
                footer='Xentra • Rooms',
            )

            view = ComplainStartView(
                user_data,
                room_data,
                message_id=message_id or '',
                complain_id=complain_id or '',
            )
            view.author_id = interaction.user.id
            view._original_interaction = interaction

            return embed, view

        await validate_and_respond(interaction, callback)


# ── setup ────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewComplain(bot))
    logger.info('InterviewComplain cog loaded')
