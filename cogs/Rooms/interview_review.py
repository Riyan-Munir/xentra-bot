"""
``/interview review``, Review the agreement of the selected interview room.

Flow:
  1. Command handler validates the user and room context.
  2. Fetches selected interview room via shared resolver.
  3. Backend validates agreement budget, milestones, budget sum, deadline ordering,
     and job-deadline boundary.
  4. Unified error messages — same message regardless of caller role.
     The other party receives a DM notification with the identical text.
  5. On success, sets the proposal review flag on the room, logs a system message,
     sends a DM notification to the other party, and returns a simple success embed.
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
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery
from utils.pdf_service import create_pdf_task, build_single_agreement_parts

logger = logging.getLogger('bot.rooms.interview_review')


class InterviewReview(commands.Cog):
    """``/interview review``, Review the agreement of the selected interview room."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _notify_other_party(
        body: dict,
        room_id: str,
        job_title: str,
        error_message: str,
        bot: discord.Client,
        session,
        headers: dict,
    ) -> None:
        """Send a DM notification to the party whose action is needed.

        The backend returns ``notify_discord_id`` and ``notify_executor_name``
        for cases where the other party must take action (NO_BUDGET,
        NO_MILESTONES, BUDGET_MISMATCH, DEADLINE_CONFLICT).

        Uses the existing ``room_interview_message`` command notification template.
        If the DM fails (DMs blocked / disabled), logs a failed delivery record
        with the ``msg_id`` so it can be retried via ``/interview delivery``.
        """
        notify_discord_id = body.get('notify_discord_id')
        if not notify_discord_id:
            return  # nothing to do

        notify_data = {
            'discord_id': notify_discord_id,
            'room_id': room_id,
            'job_title': job_title,
            'command_name': 'interview_review',
            'executor_name': body.get('notify_executor_name', 'Someone'),
            'msg_data': error_message,
        }

        delivery_ok = await handle_system_message(
            message_type='room_interview_message',
            data=notify_data,
            bot=bot,
        )

        if not delivery_ok:
            msg_id = body.get('msg_id', '')
            if msg_id:
                await log_failed_delivery(
                    room_id=room_id,
                    message_type='notification',
                    target_discord_id=notify_discord_id,
                    msg_id=msg_id,
                    session=session,
                    headers=headers,
                )
            else:
                logger.warning(
                    'No msg_id in response, cannot log failed delivery for %s in room %s',
                    notify_discord_id, room_id,
                )

    # ------------------------------------------------------------------
    # Command
    # ------------------------------------------------------------------

    @app_commands.command(
        name='interview_review',
        description='...',
    )
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def interview_review(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Review the job agreement for the selected interview room."""

        async def callback(user_data: dict):
            active_role = user_data.get('active_role')
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            is_freelancer = active_role == 'freelancer'

            room_data = user_data['_selected_room']

            room_id = room_data.get('room_id', '')
            job_title = room_data.get('job_title', '')

            # ── 2. Call backend review endpoint ─────────────────────────
            session = get_http_session()
            url = f'{BACKEND_URL}rooms/bot/review-agreement/'
            params = {
                'discord_id': str(interaction.user.id),
                'room_id': room_id,
            }

            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    body = await resp.json()
            except Exception:
                logger.exception('Failed to reach review-agreement endpoint')
                return error_embed(
                    message='The service is temporarily unavailable.',
                )

            # ── 3. Handle error codes ────────────────────────────────────
            if body.get('status') == 'error':
                code = body.get('code', '')

                # NO_AGREEMENT, neither party can fix via commands
                if code == 'NO_AGREEMENT':
                    return error_embed(
                        message='Could not review the agreement. No job agreement exists for this room.',
                    )

                # NO_BUDGET, client must set the budget
                if code == 'NO_BUDGET':
                    error_msg = 'Could not review the agreement. The job has no final budget.'
                    await self._notify_other_party(
                        body, room_id, job_title,
                        error_msg,
                        interaction.client,
                        session, headers,
                    )
                    return error_embed(message=error_msg)

                # NO_MILESTONES, freelancer must create them
                if code == 'NO_MILESTONES':
                    error_msg = 'Could not review the agreement. The job has no milestones set.'
                    await self._notify_other_party(
                        body, room_id, job_title,
                        error_msg,
                        interaction.client,
                        session, headers,
                    )
                    return error_embed(message=error_msg)

                # BUDGET_MISMATCH, milestone total != final budget
                if code == 'BUDGET_MISMATCH':
                    total = body.get('total_budget', '?')
                    final_budget = body.get('final_budget', '?')
                    error_msg = f'Could not review the agreement. Milestone total (${total}) does not match the final budget (${final_budget}).'
                    await self._notify_other_party(
                        body, room_id, job_title,
                        error_msg,
                        interaction.client,
                        session, headers,
                    )
                    return error_embed(message=error_msg)

                # DEADLINE_CONFLICT, milestone deadlines have ordering issues
                if code == 'DEADLINE_CONFLICT':
                    detail = body.get(
                        'conflict_detail',
                        'Milestone deadlines have ordering conflicts.',
                    )
                    error_msg = f'Could not review the agreement. {detail}'
                    await self._notify_other_party(
                        body, room_id, job_title,
                        error_msg,
                        interaction.client,
                        session, headers,
                    )
                    return error_embed(message=error_msg)

                # JOB_DEADLINE_EXCEEDED, last milestone past job deadline
                if code == 'JOB_DEADLINE_EXCEEDED':
                    job_deadline = body.get('job_deadline', '?')
                    last_milestone_dl = body.get('last_milestone_deadline', '?')
                    return error_embed(
                        message=f'Could not review the agreement. Last milestone deadline (`{last_milestone_dl}`) exceeds job deadline (`{job_deadline}`).',
                    )

                # Fallback for unknown error codes
                return error_embed(
                    message=body.get('message', 'Could not review the agreement.'),
                )

            # ── 4. Success, notify other party, return simple success ──
            if body.get('status') != 'ok':
                return error_embed(
                    message='The service is temporarily unavailable.',
                )

            msg_id = body.get('msg_id', '')
            notify_discord_id = body.get('notify_discord_id')
            notify_executor_name = body.get('notify_executor_name', 'Someone')

            # Send notification to the other party
            if notify_discord_id:
                notify_data = {
                    'discord_id': notify_discord_id,
                    'room_id': room_id,
                    'job_title': job_title,
                    'command_name': 'interview_review',
                    'executor_name': notify_executor_name,
                    'msg_data': 'Review request has been submitted.',
                }

                delivery_ok = await handle_system_message(
                    message_type='room_interview_message',
                    data=notify_data,
                    bot=interaction.client,
                )

                if not delivery_ok and msg_id:
                    await log_failed_delivery(
                        room_id=room_id,
                        message_type='notification',
                        target_discord_id=notify_discord_id,
                        msg_id=msg_id,
                        session=session,
                        headers=headers,
                    )

            # ── 5. Generate agreement PDF in background via PDF service ──
            async def _generate_and_deliver_pdf():
                try:
                    executor_name = body.get('executor_name', 'Someone')
                    viewer_role = active_role

                    parts = build_single_agreement_parts(
                        recipient_discord_id=str(interaction.user.id),
                        recipient_name=executor_name,
                        viewer_role=viewer_role,
                    )

                    task_id = await create_pdf_task(
                        task_type='agreement',
                        room_id=room_id,
                        requester_discord_id=str(interaction.user.id),
                        parts=parts,
                        payload=body,
                    )

                    if not task_id:
                        logger.error(
                            'Agreement review: failed to create PDF task for room %s',
                            room_id,
                        )
                        return

                    other_party_name = body.get(
                        'client_name' if is_freelancer else 'freelancer_name',
                        'User',
                    )
                    from .create_rooms import CreateRooms
                    await CreateRooms._log_system_message(
                        room_id,
                        f'Job Agreement to {other_party_name}',
                        {},
                        msg_text=(
                            f'The Job Agreement document has been reviewed '
                            f'and delivered to {other_party_name}.'
                        ),
                        show_to=active_role,
                    )

                    logger.info(
                        'Agreement review task %s created for room %s',
                        task_id, room_id,
                    )
                except Exception:
                    logger.exception('Failed to create agreement PDF task')

            asyncio.create_task(_generate_and_deliver_pdf())

            # Return success immediately, PDF is generated in background
            return success_embed('Review request has been submitted.')

        await validate_and_respond(interaction, callback)


# ── setup ──────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewReview(bot))
    logger.info('InterviewReview cog loaded')
