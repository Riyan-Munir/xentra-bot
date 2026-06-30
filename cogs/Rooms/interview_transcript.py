"""
``/interview transcript`` — Request a transcript of the interview chat.

Flow:
  1. ``validate_and_respond`` validates the user, role, and room context.
  2. Checks premium tier — on-demand transcript generation requires Premium.
  3. Fetches the selected interview room via the shared resolver.
  4. Calls the backend ``BotRoomTranscriptView`` to log the command execution
     and persist the ``InterviewRoomMsg`` record.
  5. Sends a DM notification to the other party via ``handle_system_message``
     with ``command_name='interview_transcript'`` and the specified msg_data.
  6. Returns a success message to the executor immediately. The PDF generation
     happens asynchronously and is delivered via DM.
"""

import logging

import discord
from discord.ext import commands
from discord import app_commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, fetch_selected_room
from utils.embeds import info_embed, error_embed
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery

logger = logging.getLogger('bot.rooms.interview_transcript')


class InterviewTranscript(commands.Cog):
    """``/interview transcript`` — Generate a transcript of the interview chat."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _notify_other_party(
        room_id: str,
        job_title: str,
        executor_name: str,
        other_discord_id: str,
        msg_id: str,
        bot: discord.Client,
        session,
        headers: dict,
    ) -> None:
        """Send a DM notification to the other party about the transcript request.

        Uses the existing ``room_interview_message`` command notification template.
        If the DM fails (DMs blocked / disabled), logs a failed delivery record
        with the ``msg_id`` so it can be retried via ``/interview delivery``.
        """
        if not other_discord_id:
            return

        notify_data = {
            'discord_id': other_discord_id,
            'room_id': room_id,
            'job_title': job_title,
            'command_name': 'interview_transcript',
            'executor_name': executor_name,
            'msg_data': 'Requested interview room chat transcript.',
        }

        delivery_ok = await handle_system_message(
            message_type='room_interview_message',
            data=notify_data,
            bot=bot,
        )

        if not delivery_ok and msg_id:
            await log_failed_delivery(
                room_id=room_id,
                message_type='notification',
                target_discord_id=other_discord_id,
                msg_id=msg_id,
                session=session,
                headers=headers,
            )

    # ------------------------------------------------------------------
    # Command
    # ------------------------------------------------------------------

    @app_commands.command(
        name='interview_transcript',
        description='...',
    )
    async def interview_transcript(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Generate a transcript of the interview chat."""

        async def callback(user_data: dict):
            active_role = user_data.get('active_role')
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            is_freelancer = active_role == 'freelancer'

            # ── 1. Premium tier check ─────────────────────────────────────
            role_ids = user_data.get('role_ids', {})
            role_info = role_ids.get(active_role, {})
            is_premium = role_info.get('is_premium', False)

            if not is_premium:
                return error_embed(
                    'On-demand transcript generation is a **Premium** feature.\n\n'
                    'Upgrade to Premium to generate transcripts of your interview '
                    'chat at any time. Free-tier users automatically receive a '
                    'transcript when the interview room is closed.',
                )

            # ── 2. Fetch selected interview room ──────────────────────────
            room_data = await fetch_selected_room(
                discord_id=interaction.user.id,
                role=active_role,
                room_type='interview',
                headers=headers,
            )
            if room_data is None:
                return error_embed(
                    'No selected interview room found. '
                    'Use `/switch room` to select one.',
                )

            room_id = room_data.get('room_id', '')
            job_title = room_data.get('job_title', '')
            client_discord_id = room_data.get('client_discord_id', '')
            freelancer_discord_id = room_data.get('freelancer_discord_id', '')
            client_name = room_data.get('client_name', 'Client')
            freelancer_name = room_data.get('freelancer_name', 'Freelancer')

            session = get_http_session()

            # ── 3. Log command execution via backend ──────────────────────
            log_url = f'{BACKEND_URL}rooms/bot/transcript/'
            log_payload = {
                'discord_id': str(interaction.user.id),
                'role': active_role,
                'room_id': room_id,
            }

            msg_id = ''
            try:
                async with session.post(
                    log_url, json=log_payload, headers=headers,
                ) as resp:
                    body = await resp.json()
                    if resp.status == 200:
                        msg_id = body.get('msg_id', '')
                    else:
                        logger.warning(
                            'Transcript logging returned %s: %s',
                            resp.status, body.get('error', ''),
                        )
                        return error_embed(
                            'Failed to log the transcript request. '
                            'Please try again.',
                        )
            except Exception:
                logger.exception('Failed to reach transcript logging endpoint')
                return error_embed(
                    'Unable to reach the backend service. '
                    'Please try again later.',
                )

            # ── 4. Notify the other party ─────────────────────────────────
            executor_name = client_name if active_role == 'client' else freelancer_name
            other_discord_id = (
                freelancer_discord_id if active_role == 'client'
                else client_discord_id
            )

            await self._notify_other_party(
                room_id=room_id,
                job_title=job_title,
                executor_name=executor_name,
                other_discord_id=other_discord_id,
                msg_id=msg_id,
                bot=interaction.client,
                session=session,
                headers=headers,
            )

            # ── 5. Return success message immediately ─────────────────────
            logger.info(
                'Transcript request submitted for room %s by %s',
                room_id, interaction.user.id,
            )

            return info_embed(
                'Your transcript request has been received and will be processed shortly. '
                'You will receive the PDF document via DM once it is ready.',
            )

        await validate_and_respond(interaction, callback)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewTranscript(bot))
