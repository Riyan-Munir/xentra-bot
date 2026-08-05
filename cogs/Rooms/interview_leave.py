"""
``/interview leave``, Leave the selected interview room.

Flow:
  1. Confirmation embed with Cancel / Proceed buttons
  2. On Proceed, opens a Reason modal (free-text paragraph)
  3. On modal submit, calls backend BotRoomLeaveView to persist leave,
     mark room as left + closed, and returns other party info
  4. Sends notification to the other party with the reason
  5. Sends closure notification + transcript to both parties
  6. Room is closed (no job/application updates)
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import sync_cog_commands, validate_and_respond, is_author
from utils.embeds import (
    error_embed,
    success_embed,
    info_embed,
    create_embed,
    BrandColor,
)
from utils.http import get_http_session
from utils.room_closure import send_room_closure_and_transcript
from ._shared import record_and_notify

logger = logging.getLogger('bot.rooms.interview_leave')


# ──────────────────────────────────────────────────────────────────────
# Confirmation View, Cancel / Proceed
# ──────────────────────────────────────────────────────────────────────


class LeaveConfirmView(discord.ui.View):
    """Confirmation view asking the user to confirm they want to leave."""

    def __init__(
        self,
        room_id: str,
        job_title: str,
        user_data: dict,
        room_data: dict,
        headers: dict,
    ) -> None:
        super().__init__(timeout=120)
        self.room_id = room_id
        self.job_title = job_title
        self.user_data = user_data
        self.room_data = room_data
        self.headers = headers
        self.author_id: int | None = None
        self.interaction: discord.Interaction | None = None

    async def _remove_view(self) -> None:
        if self.interaction:
            try:
                await self.interaction.edit_original_response(view=None)
            except Exception:
                pass

    async def on_timeout(self) -> None:
        """Remove view on timeout to prevent stale-state abuse."""
        await self._remove_view()
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Room leave cancelled.'),
            view=None,
        )

    @discord.ui.button(label='Proceed', style=discord.ButtonStyle.success)
    async def proceed(self, interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        self.interaction = interaction

        # Send modal FIRST as the initial response, then remove buttons
        modal = LeaveReasonModal(
            room_id=self.room_id,
            job_title=self.job_title,
            user_data=self.user_data,
            room_data=self.room_data,
            headers=self.headers,
            original_interaction=interaction,
        )
        await interaction.response.send_modal(modal)

        # Remove buttons on the original message after modal response
        await self._remove_view()


# ──────────────────────────────────────────────────────────────────────
# Reason Modal
# ──────────────────────────────────────────────────────────────────────


class LeaveReasonModal(discord.ui.Modal, title='Reason for Leaving'):
    """Modal that collects the reason for leaving the room."""

    reason = discord.ui.TextInput(
        label='Reason',
        style=discord.TextStyle.paragraph,
        placeholder='Describe why you are leaving this interview room…',
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        room_id: str,
        job_title: str,
        user_data: dict,
        room_data: dict,
        headers: dict,
        original_interaction: discord.Interaction,
    ) -> None:
        super().__init__(timeout=300)
        self.room_id = room_id
        self.job_title = job_title
        self.user_data = user_data
        self.room_data = room_data
        self.headers = headers
        self.original_interaction = original_interaction

    async def on_submit(self, interaction: discord.Interaction) -> None:
        reason_text = self.reason.value.strip()
        if not reason_text:
            try:
                await interaction.response.send_message(
                    embed=error_embed(message='Reason must not be empty.'),
                    ephemeral=True,
                )
            except (discord.errors.InteractionResponded, discord.errors.NotFound):
                pass
            return

        # Defer first, the API calls and PDF generation below can take >3s
        await interaction.response.defer()

        is_dm = interaction.guild is None
        session = get_http_session()

        active_role = self.user_data.get('active_role', '')

        # ── Determine sender display name ────────────────────────────
        if active_role == 'client':
            sender_name = self.room_data.get('client_name', 'Client')
        else:
            sender_name = self.room_data.get('freelancer_name', 'Freelancer')

        # ── Call backend to persist leave + close room ──────────────
        payload = {
            'discord_id': str(interaction.user.id),
            'room_id': self.room_id,
            'reason': reason_text,
        }

        try:
            async with session.post(
                f'{BACKEND_URL}rooms/bot/room-leave/',
                json=payload,
                headers=self.headers,
            ) as resp:
                if resp.status != 200:
                    err_data = await resp.json()
                    err_msg = err_data.get('error', 'The service is temporarily unavailable.')
                    await record_and_notify(
                        room_id=self.room_id,
                        sender_role=active_role,
                        msg_data=err_msg,
                        command_name='interview_leave',
                        target_discord_id='',
                        executor_name=sender_name,
                        job_title=self.job_title,
                        bot=interaction.client,
                        session=session,
                        headers=self.headers,
                    )
                    # interaction already deferred, edit the original message
                    await interaction.edit_original_response(
                        embed=error_embed(message=err_msg),
                        view=None,
                    )
                    return
                leave_data = await resp.json()
        except Exception:
            logger.exception('Failed to call room-leave backend')
            err_msg = 'The service is temporarily unavailable.'
            await record_and_notify(
                room_id=self.room_id,
                sender_role=active_role,
                msg_data=err_msg,
                command_name='interview_leave',
                target_discord_id='',
                executor_name=sender_name,
                job_title=self.job_title,
                bot=interaction.client,
                session=session,
                headers=self.headers,
            )
            await interaction.edit_original_response(
                embed=error_embed(message=err_msg),
                view=None,
            )
            return

        closure_id = leave_data.get('closure_id', '')
        other_discord_id = leave_data.get('other_discord_id', '')

        # ── Build success message (same text used for executor + record)
        success_msg = f'You have left interview room `{self.room_id}`.'

        # ── Record message + notify other party ─────────────────────
        await record_and_notify(
            room_id=self.room_id,
            sender_role=active_role,
            msg_data=success_msg,
            command_name='interview_leave',
            target_discord_id=other_discord_id,
            executor_name=sender_name,
            job_title=self.job_title,
            bot=interaction.client,
            session=session,
            headers=self.headers,
        )

        await interaction.edit_original_response(
            embed=success_embed(message=success_msg),
            view=None,
        )

        # ── Fire-and-forget: unified closure + transcript delivery ──
        # The closure record was already persisted by room-leave/ (single
        # point of persistence); the utility only delivers + marks complete.
        async def _run_closure():
            try:
                await asyncio.sleep(30)
                await send_room_closure_and_transcript(
                    closure_ids=[closure_id],
                    bot=interaction.client,
                    headers=self.headers,
                )
            except KeyboardInterrupt:
                logger.warning('Room closure task interrupted by shutdown')
            except BaseException:
                logger.exception('Failed during leave closure sequence')
        asyncio.create_task(_run_closure())

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception('LeaveReasonModal error')
        # Edit the original message (from the Proceed view) to show the error
        try:
            await self.original_interaction.edit_original_response(
                embed=error_embed(message='The service is temporarily unavailable.'),
                view=None,
            )
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────


class InterviewLeave(commands.Cog):
    """``/interview leave``, Leave the selected interview room."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    # ------------------------------------------------------------------
    # Command
    # ------------------------------------------------------------------

    @app_commands.command(
        name='interview_leave',
        description='...',
    )
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def interview_leave(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Leave the selected interview room."""

        async def callback(user_data: dict):
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            room_data = user_data['_selected_room']

            room_id = room_data.get('room_id', '')
            job_title = room_data.get('job_title', '')

            # ── 2. Show confirmation embed ──────────────────────────────
            confirm_embed = create_embed(
                title='Leave Interview Room',
                description=(
                    f'> Are you sure you want to leave room **`{room_id}`**?\n\n'
                    '> This action **cannot be undone**. Leaving will:\n'
                    '> • Close the interview room permanently\n'
                    '> • Generate and deliver a transcript to both parties\n'
                    '> • Notify the other party with your reason\n\n'
                    '> If you\'re unsure, click **Cancel**. '
                    'Otherwise, click **Proceed** to provide a reason.'
                ),
                color=BrandColor.PRIMARY,
                footer='Xentra • Rooms',
            )

            view = LeaveConfirmView(
                room_id=room_id,
                job_title=job_title,
                user_data=user_data,
                room_data=room_data,
                headers=headers,
            )
            view.author_id = interaction.user.id

            return confirm_embed, view

        await validate_and_respond(interaction, callback)


# ── setup ──────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewLeave(bot))
    logger.info('InterviewLeave cog loaded')
