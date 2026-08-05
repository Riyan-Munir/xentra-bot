"""
``/interview review``, Review the agreement of the selected interview room.

Flow:
  1. Command handler validates the user and room context.
  2. Fetches selected interview room via shared resolver.
  3. Backend validates agreement budget, milestones, budget sum, deadline ordering,
     and job-deadline boundary.
  4. Unified error messages — same message regardless of caller role.
    The other party receives a DM notification with the identical text.
  5. On success, records the command message, DM-notifies the other party,
     and fires the agreement PDF request via ``request_pdf()``.
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

logger = logging.getLogger('bot.rooms.interview_review')


class InterviewReview(commands.Cog):
    """``/interview review``, Review the agreement of the selected interview room."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

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

            room_data = user_data['_selected_room']

            room_id = room_data.get('room_id', '')
            job_title = room_data.get('job_title', '')

            # ── Determine sender display name ────────────────────────────
            if active_role == 'client':
                sender_name = room_data.get('client_name', 'Client')
            else:
                sender_name = room_data.get('freelancer_name', 'Freelancer')

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
                error_msg = 'The service is temporarily unavailable.'
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_review',
                    target_discord_id='',
                    executor_name=sender_name,
                    job_title=job_title,
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

            # ── 3. Handle error codes ────────────────────────────────────
            if body.get('status') == 'error':
                code = body.get('code', '')

                # NO_AGREEMENT, neither party can fix via commands
                if code == 'NO_AGREEMENT':
                    error_msg = 'Could not review the agreement. No job agreement exists for this room.'
                    await record_and_notify(
                        room_id=room_id,
                        sender_role=active_role,
                        msg_data=error_msg,
                        command_name='interview_review',
                        target_discord_id='',
                        executor_name=sender_name,
                        job_title=job_title,
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(message=error_msg)

                # NO_BUDGET, client must set the budget
                if code == 'NO_BUDGET':
                    error_msg = 'Could not review the agreement. The job has no final budget.'
                    notify_discord_id = body.get('notify_discord_id', '')
                    await record_and_notify(
                        room_id=room_id,
                        sender_role=active_role,
                        msg_data=error_msg,
                        command_name='interview_review',
                        target_discord_id=notify_discord_id,
                        executor_name=body.get('notify_executor_name', 'Someone'),
                        job_title=job_title,
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(message=error_msg)

                # NO_MILESTONES, freelancer must create them
                if code == 'NO_MILESTONES':
                    error_msg = 'Could not review the agreement. The job has no milestones set.'
                    notify_discord_id = body.get('notify_discord_id', '')
                    await record_and_notify(
                        room_id=room_id,
                        sender_role=active_role,
                        msg_data=error_msg,
                        command_name='interview_review',
                        target_discord_id=notify_discord_id,
                        executor_name=body.get('notify_executor_name', 'Someone'),
                        job_title=job_title,
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(message=error_msg)

                # BUDGET_MISMATCH, milestone total != final budget
                if code == 'BUDGET_MISMATCH':
                    total = body.get('total_budget', '?')
                    final_budget = body.get('final_budget', '?')
                    error_msg = f'Could not review the agreement. Milestone total (${total}) does not match the final budget (${final_budget}).'
                    notify_discord_id = body.get('notify_discord_id', '')
                    await record_and_notify(
                        room_id=room_id,
                        sender_role=active_role,
                        msg_data=error_msg,
                        command_name='interview_review',
                        target_discord_id=notify_discord_id,
                        executor_name=body.get('notify_executor_name', 'Someone'),
                        job_title=job_title,
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(message=error_msg)

                # DEADLINE_CONFLICT, milestone deadlines have ordering issues
                if code == 'DEADLINE_CONFLICT':
                    detail = body.get(
                        'conflict_detail',
                        'Milestone deadlines have ordering conflicts.',
                    )
                    error_msg = f'Could not review the agreement. {detail}'
                    notify_discord_id = body.get('notify_discord_id', '')
                    await record_and_notify(
                        room_id=room_id,
                        sender_role=active_role,
                        msg_data=error_msg,
                        command_name='interview_review',
                        target_discord_id=notify_discord_id,
                        executor_name=body.get('notify_executor_name', 'Someone'),
                        job_title=job_title,
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(message=error_msg)

                # JOB_DEADLINE_EXCEEDED, last milestone past job deadline
                if code == 'JOB_DEADLINE_EXCEEDED':
                    job_deadline = body.get('job_deadline', '?')
                    last_milestone_dl = body.get('last_milestone_deadline', '?')
                    error_msg = f'Could not review the agreement. Last milestone deadline (`{last_milestone_dl}`) exceeds job deadline (`{job_deadline}`).'
                    await record_and_notify(
                        room_id=room_id,
                        sender_role=active_role,
                        msg_data=error_msg,
                        command_name='interview_review',
                        target_discord_id='',
                        executor_name=sender_name,
                        job_title=job_title,
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(message=error_msg)

                # Fallback for unknown error codes
                error_msg = body.get('message', 'Could not review the agreement.')
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_review',
                    target_discord_id='',
                    executor_name=sender_name,
                    job_title=job_title,
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

            # ── 4. Success, record + notify, return simple success ────
            if body.get('status') != 'ok':
                error_msg = 'The service is temporarily unavailable.'
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_review',
                    target_discord_id='',
                    executor_name=sender_name,
                    job_title=job_title,
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

            notify_discord_id = body.get('notify_discord_id', '')
            notify_executor_name = body.get('notify_executor_name', 'Someone')

            await record_and_notify(
                room_id=room_id,
                sender_role=active_role,
                msg_data='Agreement review request has been received and will be '
                'processed shortly.',
                command_name='interview_review',
                target_discord_id=notify_discord_id,
                executor_name=notify_executor_name,
                job_title=job_title,
                bot=interaction.client,
                session=session,
                headers=headers,
            )

            # ── 5. Fire agreement PDF generation request (fire-and-forget) ──
            asyncio.create_task(
                request_pdf(
                    task_type='agreement',
                    room_id=room_id,
                    room_type='interview',
                    requester_discord_id=str(interaction.user.id),
                    recipient_discord_id=str(interaction.user.id),
                    viewer_role=active_role,
                )
            )

            # Return success immediately, PDF is generated in background
            return success_embed(
                'Agreement review request has been received and will be '
                'processed shortly.'
            )

        await validate_and_respond(interaction, callback)


# ── setup ──────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewReview(bot))
    logger.info('InterviewReview cog loaded')
