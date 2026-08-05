"""
``/interview transcript``, Request a transcript of the interview chat.

Flow:
  1. ``validate_and_respond`` validates the user, role, and room context.
  2. Checks premium tier, on-demand transcript generation requires Premium.
  3. Fetches the selected interview room via the shared resolver.
  4. Calls the backend ``BotRoomTranscriptView`` to validate the user
     belongs to the room (no message recording — handled bot-side).
  5. Calls ``record_and_notify()`` to persist the command message record
     and DM-notify the other party.
  6. Fires the PDF request via ``request_pdf()`` (fire-and-forget).
  7. Returns an instant "request received" embed to the executor.
"""

import asyncio
import logging

import discord
from discord.ext import commands
from discord import app_commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import success_embed, error_embed
from utils.http import get_http_session
from ._shared import record_and_notify, request_pdf

logger = logging.getLogger('bot.rooms.interview_transcript')


class InterviewTranscript(commands.Cog):
    """``/interview transcript``, Generate a transcript of the interview chat."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

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

            room_data = user_data['_selected_room']

            room_id = room_data.get('room_id', '')

            session = get_http_session()

            # ── 1. Premium tier check ─────────────────────────────────────
            role_ids = user_data.get('role_ids', {})
            role_info = role_ids.get(active_role, {})
            is_premium = role_info.get('is_premium', False)

            if not is_premium:
                error_msg = (
                    'On-demand transcript generation is a **Premium** feature.\n\n'
                    'Free-tier users automatically receive a transcript '
                    'when the interview room is closed.'
                )
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_transcript',
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(error_msg)

            # ── 2. Validate user belongs to room (no msg saving) ──────────
            log_url = f'{BACKEND_URL}rooms/bot/transcript/'
            log_payload = {
                'discord_id': str(interaction.user.id),
                'room_id': room_id,
            }

            try:
                async with session.post(
                    log_url, json=log_payload, headers=headers,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.json()
                        logger.warning(
                            'Transcript validation returned %s: %s',
                            resp.status, body.get('error', ''),
                        )
                        error_msg = 'The service is temporarily unavailable.'
                        await record_and_notify(
                            room_id=room_id,
                            sender_role=active_role,
                            msg_data=error_msg,
                            command_name='interview_transcript',
                            bot=interaction.client,
                            session=session,
                            headers=headers,
                        )
                        return error_embed(error_msg)
            except Exception:
                logger.exception('Failed to reach transcript validation endpoint')
                error_msg = 'The service is temporarily unavailable.'
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_transcript',
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(error_msg)

            # ── 3. Record command message + notify other party ────────────
            msg_data = (
                'Transcript request has been received and will be '
                'processed shortly.'
            )

            await record_and_notify(
                room_id=room_id,
                sender_role=active_role,
                msg_data=msg_data,
                command_name='interview_transcript',
                bot=interaction.client,
                session=session,
                headers=headers,
            )

            # ── 4. Fire PDF generation request (fire-and-forget) ─────────
            viewer_role = 'freelancer' if is_freelancer else 'client'

            asyncio.create_task(
                request_pdf(
                    task_type='transcript',
                    room_id=room_id,
                    room_type='interview',
                    requester_discord_id=str(interaction.user.id),
                    recipient_discord_id=str(interaction.user.id),
                    viewer_role=viewer_role,
                )
            )

            # ── 5. Return instant "request received" message ──────────────
            return success_embed(msg_data)

        await validate_and_respond(interaction, callback)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewTranscript(bot))
