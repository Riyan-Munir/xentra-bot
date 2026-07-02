"""
``/interview review`` — Review the agreement of the selected interview room.

Flow:
  1. Command handler validates the user and room context.
  2. Fetches selected interview room via shared resolver.
  3. Backend validates agreement budget, milestones, budget sum, deadline ordering,
     and job-deadline boundary.
  4. Role-aware error messages — the party who can fix the issue sees actionable
     instructions; the other party receives a DM notification summarising the gap.
  5. On success, sets the proposal review flag on the room, logs a system message,
     sends a DM notification to the other party, and returns a simple success embed.
"""

import io
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
from utils.agreement_pdf import generate_agreement_bytes

logger = logging.getLogger('bot.rooms.interview_review')


class InterviewReview(commands.Cog):
    """``/interview review`` — Review the agreement of the selected interview room."""

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
                    'No msg_id in response — cannot log failed delivery for %s in room %s',
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
                'role': active_role,
                'room_id': room_id,
            }

            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    body = await resp.json()
            except Exception:
                logger.exception('Failed to reach review-agreement endpoint')
                return error_embed(
                    message='Unable to reach the backend service. Please try again later.',
                )

            # ── 3. Handle error codes with role-aware messages ──────────
            if body.get('status') == 'error':
                code = body.get('code', '')

                # NO_AGREEMENT — neither party can fix via commands
                if code == 'NO_AGREEMENT':
                    return error_embed(
                        message='No job agreement exists for this room yet. '
                        'Contact a server administrator if the issue persists.',
                    )

                # NO_BUDGET — client must set the budget
                if code == 'NO_BUDGET':
                    client_name = body.get('client_name', 'Client')
                    # Notify the client (always — they're the one who can act)
                    await self._notify_other_party(
                        body, room_id, job_title,
                        f'Requested review of Job Agreement. Requires {client_name} '
                        f'to set the final budget via `/interview_budget`.',
                        interaction.client,
                        session, headers,
                    )
                    if is_freelancer:
                        return error_embed(
                            message=f'The final budget has not been set yet. '
                            f'A notification has been sent to **{client_name}**.',
                        )
                    else:
                        return error_embed(
                            message='The final budget has not been set yet. '
                            'Use `/interview_budget` to set it, then re-run this command.',
                        )

                # NO_MILESTONES — freelancer must create them
                if code == 'NO_MILESTONES':
                    freelancer_name = body.get('freelancer_name', 'Freelancer')
                    await self._notify_other_party(
                        body, room_id, job_title,
                        'Milestones are required for the job agreement review.',
                        interaction.client,
                        session, headers,
                    )
                    if is_freelancer:
                        return error_embed(
                            message='No milestones have been configured yet. '
                            'Use `/interview_milestone` to create them and re-run this command.',
                        )
                    else:
                        return error_embed(
                            message=f'No milestones have been configured yet. '
                            f'A notification has been sent to **{freelancer_name}**.',
                        )

                # BUDGET_MISMATCH — milestone total != final budget
                if code == 'BUDGET_MISMATCH':
                    total = body.get('total_budget', '?')
                    final_budget = body.get('final_budget', '?')
                    freelancer_name = body.get('freelancer_name', 'Freelancer')
                    await self._notify_other_party(
                        body, room_id, job_title,
                        f'Milestone total (${total}) does not match the final budget (${final_budget}).',
                        interaction.client,
                        session, headers,
                    )
                    if is_freelancer:
                        return error_embed(
                            message=f'Total milestone budget (${total}) does not match the '
                            f'final budget (${final_budget}). '
                            f'Use `/interview_milestone` to adjust milestone budgets.',
                        )
                    else:
                        return error_embed(
                            message=f'Total milestone budget (${total}) does not match the '
                            f'final budget (${final_budget}). '
                            f'A notification has been sent to **{freelancer_name}**.',
                        )

                # DEADLINE_CONFLICT — milestone deadlines have ordering issues
                if code == 'DEADLINE_CONFLICT':
                    detail = body.get(
                        'conflict_detail',
                        'Milestone deadlines have ordering conflicts.',
                    )
                    freelancer_name = body.get('freelancer_name', 'Freelancer')
                    await self._notify_other_party(
                        body, room_id, job_title,
                        'Valid milestone deadlines are required for the job agreement review.',
                        interaction.client,
                        session, headers,
                    )
                    if is_freelancer:
                        return error_embed(message=detail)
                    else:
                        return error_embed(
                            message=f'Milestone deadlines contain ordering conflicts. '
                            f'A notification has been sent to **{freelancer_name}**.',
                        )

                # JOB_DEADLINE_EXCEEDED — last milestone past job deadline
                if code == 'JOB_DEADLINE_EXCEEDED':
                    job_deadline = body.get('job_deadline', '?')
                    last_milestone_dl = body.get('last_milestone_deadline', '?')
                    if is_freelancer:
                        return error_embed(
                            message=f'The last milestone deadline ({last_milestone_dl}) is after '
                            f'the job deadline ({job_deadline}). '
                            f'Use `/interview_milestone` to adjust milestone deadlines.',
                        )
                    else:
                        return error_embed(
                            message=f'The last milestone deadline ({last_milestone_dl}) is after '
                            f'the job deadline ({job_deadline}). '
                            f'A notification has been sent to **{body.get("freelancer_name", "Freelancer")}**.',
                        )

                # Fallback for unknown error codes
                return error_embed(
                    message=body.get('message', 'Failed to review the agreement.'),
                )

            # ── 4. Success — notify other party, return simple success ──
            if body.get('status') != 'ok':
                return error_embed(
                    message='Unexpected response from the server. Please try again.',
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
                    'msg_data': 'Requested Job Agreement Review.',
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

            # ── 5. Generate agreement PDF and send to executor ────────────
            try:
                pdf_bytes = generate_agreement_bytes(body)
                pdf_file = discord.File(io.BytesIO(pdf_bytes), filename='Job-Agreement.pdf')

                # Create embed explaining the PDF
                pdf_embed = success_embed(
                    'Review the attached Job Agreement document.',
                )

                # Send to the executor (command caller) via DM
                pdf_delivered = False
                try:
                    await interaction.user.send(
                        embed=pdf_embed,
                        file=pdf_file,
                    )
                    pdf_delivered = True
                except discord.Forbidden:
                    logger.warning(
                        'Cannot DM executor %s — DMs may be disabled.',
                        interaction.user.id,
                    )
                    # Fallback: send in the interaction channel with ephemeral
                    await interaction.followup.send(
                        embed=pdf_embed,
                        file=pdf_file,
                        ephemeral=True,
                    )
                    pdf_delivered = True
                except Exception:
                    logger.exception(
                        'Failed to send PDF to executor %s',
                        interaction.user.id,
                    )
                    # At least return the success embed
                    return success_embed(
                        'Review request has been submitted. '
                        'Could not send the PDF file — please check your DM settings.',
                    )

                # ── 6. Log PDF delivery as a system message ──────────────
                if pdf_delivered:
                    other_party_name = body.get(
                        'client_name' if is_freelancer else 'freelancer_name',
                        'User',
                    )
                    from .create_rooms import CreateRooms
                    await CreateRooms._log_system_message(
                        room_id,
                        f'Job Agreement to {other_party_name}',
                        {},
                    )

            except Exception:
                logger.exception('Failed to generate agreement PDF')
                # Still show success, just without the PDF
                return success_embed('Review request has been submitted.')

            # Return the final embed (already sent via DM/channel above)
            return success_embed('Review request has been submitted.')

        await validate_and_respond(interaction, callback)


# ── setup ──────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewReview(bot))
    logger.info('InterviewReview cog loaded')
