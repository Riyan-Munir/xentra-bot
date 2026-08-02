"""
``/interview agreement``, Sign the agreement of the selected interview room.

Flow:
  1. Command handler validates the user and room context.
  2. Fetches selected interview room via shared resolver.
  3. Calls ``BotProcessAgreementView`` GET to check review flags.
  4. If reviews incomplete, error with notification to the other party.
  5. If both reviewed, shows confirmation embed with Accept / Decline.
  6. On **Accept**, POST to ``BotAcceptAgreementView``, show success embed:
     *"You've signed the Job Agreement. Xentra will share signed agreement soon."*
  7. Sends a DM notification to the other party with the identical success text
     shown to the executor.
  8. If **both** parties have now accepted, generates signed PDF with stamps
     from the database, sends it to **both** client and freelancer via DM.
  9. Logs *"Sent signed Job Agreement"* as a system message.
 10. If DM delivery fails for one person, sends a notification to the other
     party with heading *"Sender: Xentra"* and a message about the blocked DM.
"""

import asyncio
import logging

import discord
from discord.ext import commands
from discord import app_commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import success_embed, error_embed, info_embed, create_embed, BrandColor
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery
from utils.pdf_service import create_pdf_task, build_agreement_parts
from utils.room_closure import send_room_closure_and_transcript

logger = logging.getLogger('bot.rooms.interview_agreement')


# ---------------------------------------------------------------------------
# Confirmation View  (Accept / Decline)
# ---------------------------------------------------------------------------

class AgreementConfirmView(discord.ui.View):
    """Accept/Decline confirmation for the job agreement."""

    def __init__(
        self,
        room_id: str,
        job_title: str,
        active_role: str,
        headers: dict,
        client_discord_id: str,
        freelancer_discord_id: str,
        client_name: str,
        freelancer_name: str,
        executor_name: str,
        other_name: str,
        other_discord_id: str,
    ) -> None:
        super().__init__(timeout=120)
        self.room_id = room_id
        self.job_title = job_title
        self.active_role = active_role
        self.headers = headers
        self.client_discord_id = client_discord_id
        self.freelancer_discord_id = freelancer_discord_id
        self.client_name = client_name
        self.freelancer_name = freelancer_name
        self.executor_name = executor_name
        self.other_name = other_name
        self.other_discord_id = other_discord_id
        self._accepted = False
        self.author_id: int | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def on_timeout(self) -> None:
        """Remove view on timeout to prevent stale-state abuse."""
        self.stop()

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    @discord.ui.button(label='Accept', style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        if self._accepted:
            return
        self._accepted = True

        await interaction.response.defer()

        session = get_http_session()
        url = f'{BACKEND_URL}rooms/bot/accept-agreement/'
        payload = {
            'discord_id': str(interaction.user.id),
            'room_id': self.room_id,
        }

        try:
            async with session.post(url, json=payload, headers=self.headers) as resp:
                body = await resp.json()
        except Exception:
            logger.exception('Failed to reach accept-agreement endpoint')
            await interaction.edit_original_response(
                embed=error_embed(
                    message='The service is temporarily unavailable.',
                ),
                view=None,
            )
            return

        if body.get('status') == 'error':
            await interaction.edit_original_response(
                embed=error_embed(message=body.get('message', 'Could not sign the agreement.')),
                view=None,
            )
            return

        # ── Success embed ──────────────────────────────────────────────
        success_msg = "You've signed the Job Agreement. We will share the signed agreement soon."
        success = success_embed(success_msg)
        await interaction.edit_original_response(embed=success, view=None)

        # ── Notify the other party ──────────────────────────────────────
        notify_data = {
            'discord_id': self.other_discord_id,
            'room_id': self.room_id,
            'job_title': self.job_title,
            'command_name': 'interview_agreement',
            'executor_name': self.executor_name,
            'msg_data': success_msg,
        }

        delivery_ok = await handle_system_message(
            message_type='room_interview_message',
            data=notify_data,
            bot=interaction.client,
        )

        msg_id = body.get('msg_id', '')
        if not delivery_ok and msg_id:
            await log_failed_delivery(
                room_id=self.room_id,
                message_type='notification',
                target_discord_id=self.other_discord_id,
                msg_id=msg_id,
                session=session,
                headers=self.headers,
            )

        # ── If both parties have signed, deliver signed PDF ────────────
        if body.get('both_accepted'):
            asyncio.create_task(
                self._deliver_signed_pdf(interaction, body),
            )

    # ------------------------------------------------------------------
    # Signed PDF delivery
    # ------------------------------------------------------------------

    async def _deliver_signed_pdf(
        self,
        interaction: discord.Interaction,
        body: dict,
    ) -> None:
        """Create signed agreement PDF task and deliver to both parties via PDF service."""
        # Store agreement_id for closure sequence
        self._agreement_id = body.get('agreement_id', '')

        parts = build_agreement_parts(
            client_discord_id=str(self.client_discord_id),
            client_name=self.client_name,
            freelancer_discord_id=str(self.freelancer_discord_id),
            freelancer_name=self.freelancer_name,
        )

        payload = {
            'agreement_id': self._agreement_id,
            'job_id': body.get('job_id', ''),
            'job_application_id': body.get('job_application_id', ''),
            'interview_room_id': body.get('interview_room_id', ''),
            'client_name': body.get('client_name', self.client_name),
            'freelancer_name': body.get('freelancer_name', self.freelancer_name),
            'final_budget': body.get('final_budget', '0'),
            'milestones': body.get('milestones', []),
            'stamp_b64': body.get('stamp_b64', ''),
            'watermark_b64': body.get('watermark_b64', ''),
            'is_signed': True,
        }

        task_id = await create_pdf_task(
            task_type='signed_agreement',
            room_id=self.room_id,
            requester_discord_id=str(interaction.user.id),
            parts=parts,
            payload=payload,
        )

        if not task_id:
            logger.error(
                'Failed to create signed agreement PDF task for room %s',
                self.room_id,
            )
            return

        logger.info(
            'Signed agreement task %s created for room %s',
            task_id, self.room_id,
        )

        # ── Log system message ─────────────────────────────────────────
        from .create_rooms import CreateRooms
        await CreateRooms._log_system_message(
            self.room_id,
            'signed Job Agreement',
            {},
            msg_text=(
                'The Job Agreement has been signed by both parties. '
                'The signed document has been delivered.'
            ),
            show_to='both',
        )

        # ── Room Closure Sequence (fire-and-forget) ───────────────────
        async def _run_closure():
            try:
                await send_room_closure_and_transcript(
                    room_id=self.room_id,
                    bot=interaction.client,
                    headers=self.headers,
                    closure_type='agreement',
                    agreement_id=self._agreement_id,
                )
            except KeyboardInterrupt:
                logger.warning('Room closure task interrupted by shutdown')
            except BaseException:
                logger.exception('Failed during room closure sequence')
        asyncio.create_task(_run_closure())

    # ------------------------------------------------------------------
    # Decline
    # ------------------------------------------------------------------

    @discord.ui.button(label='Decline', style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        if self._accepted:
            return
        await interaction.response.defer()
        self.stop()
        await interaction.edit_original_response(
            embed=info_embed(message='Agreement signing cancelled.'),
            view=None,
        )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class InterviewAgreement(commands.Cog):
    """``/interview agreement``, Sign the agreement of the selected interview room."""

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
        bot: discord.Client,
        session,
        headers: dict,
        msg_data: str = '',
    ) -> None:
        """Send a DM notification to the other party.

        Uses the provided ``msg_data`` (which must be identical to the
        executor's response text).  If the DM fails (DMs blocked /
        disabled), logs a failed delivery record so it can be retried.
        """
        notify_discord_id = body.get('notify_discord_id')
        if not notify_discord_id:
            return

        notify_data = {
            'discord_id': notify_discord_id,
            'room_id': room_id,
            'job_title': job_title,
            'command_name': 'interview_agreement',
            'executor_name': body.get('notify_receiver_name', 'Someone'),
            'msg_data': msg_data or body.get('notify_msg_data', ''),
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
        name='interview_agreement',
        description='...',
    )
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def interview_agreement(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Sign the agreement for the selected interview room."""

        async def callback(user_data: dict):
            active_role = user_data.get('active_role')
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            is_freelancer = active_role == 'freelancer'

            room_data = user_data['_selected_room']

            room_id = room_data.get('room_id', '')
            job_title = room_data.get('job_title', '')

            # ── 2. Call backend process-agreement endpoint ──────────────
            session = get_http_session()
            url = f'{BACKEND_URL}rooms/bot/process-agreement/'
            params = {
                'discord_id': str(interaction.user.id),
                'room_id': room_id,
            }

            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    body = await resp.json()
            except Exception:
                logger.exception('Failed to reach process-agreement endpoint')
                return error_embed(
                    message='The service is temporarily unavailable.',
                )

            # ── 3. Handle error codes with role-aware messages ──────────
            if body.get('status') == 'error':
                code = body.get('code', '')

                if code == 'REVIEW_INCOMPLETE':
                    executor_ok = body.get('executor_review_ok', False)
                    other_ok = body.get('other_review_ok', False)

                    if executor_ok and not other_ok:
                        error_msg = 'Could not sign the agreement. The agreement has not been reviewed by any one participant.'
                    elif not executor_ok and other_ok:
                        error_msg = 'Could not sign the agreement. The agreement has not been reviewed by any one participant.'
                    else:
                        error_msg = 'Could not sign the agreement. The agreement has not been reviewed by one or more participants.'

                    # Notify the other party (the one who needs to act)
                    await self._notify_other_party(
                        body, room_id, job_title,
                        interaction.client,
                        session, headers,
                        msg_data=error_msg,
                    )
                    return error_embed(message=error_msg)

                # Fallback for unknown error codes
                return error_embed(
                    message=body.get('message', 'The service is temporarily unavailable.'),
                )

            # ── 4. Both reviews complete, handle ALREADY_SIGNED ─────────
            if body.get('code') == 'ALREADY_SIGNED':
                both_accepted = body.get('both_accepted', False)
                other_name = body.get('notify_receiver_name', 'The other party')

                # Notify the other party regardless
                await self._notify_other_party(
                    body, room_id, job_title,
                    interaction.client,
                    session, headers,
                )

                if both_accepted:
                    # Both parties have signed, deliver signed PDF
                    # Construct a minimal body for _deliver_signed_pdf
                    pdf_body = {
                        'agreement_id': body.get('agreement_id', ''),
                        'job_id': body.get('job_id', ''),
                        'job_application_id': body.get('job_application_id', ''),
                        'interview_room_id': body.get('interview_room_id', ''),
                        'client_name': body.get('client_name', 'Client'),
                        'freelancer_name': body.get('freelancer_name', 'Freelancer'),
                        'final_budget': body.get('final_budget', '0'),
                        'milestones': body.get('milestones', []),
                        'stamp_b64': body.get('stamp_b64', ''),
                        'watermark_b64': body.get('watermark_b64', ''),
                        'is_signed': True,
                    }

                    # We need a view instance; create one inline for _deliver_signed_pdf
                    async def _handle_both_signed():
                        from .create_rooms import CreateRooms
                        # Store agreement_id on self for closure sequence
                        self._agreement_id = pdf_body.get('agreement_id', '')

                        parts = build_agreement_parts(
                            client_discord_id=str(self.client_discord_id),
                            client_name=self.client_name,
                            freelancer_discord_id=str(self.freelancer_discord_id),
                            freelancer_name=self.freelancer_name,
                        )

                        task_id = await create_pdf_task(
                            task_type='signed_agreement',
                            room_id=self.room_id,
                            requester_discord_id=str(interaction.user.id),
                            parts=parts,
                            payload=pdf_body,
                        )

                        if not task_id:
                            logger.error(
                                'Failed to create signed agreement PDF task for room %s (ALREADY_SIGNED path)',
                                self.room_id,
                            )
                            return

                        logger.info(
                            'Signed agreement task %s created for room %s (ALREADY_SIGNED path)',
                            task_id, self.room_id,
                        )

                        # Log system message
                        await CreateRooms._log_system_message(
                            self.room_id,
                            'signed Job Agreement',
                            {},
                            msg_text=(
                                'The Job Agreement has been signed by both parties. '
                                'The signed document has been delivered.'
                            ),
                            show_to='both',
                        )

                        # Closure sequence (fire-and-forget)
                        async def _run_inline_closure():
                            try:
                                await send_room_closure_and_transcript(
                                    room_id=self.room_id,
                                    bot=interaction.client,
                                    headers=self.headers,
                                    closure_type='agreement',
                                    agreement_id=self._agreement_id,
                                )
                            except KeyboardInterrupt:
                                logger.warning('Room closure task interrupted by shutdown')
                            except BaseException:
                                logger.exception('Failed during room closure sequence')
                        asyncio.create_task(_run_inline_closure())

                    await _handle_both_signed()
                    # Return success to prevent fall-through to confirm embed
                    return success_embed(
                        'The Job Agreement has been signed by both parties. '
                        'The room will be closed shortly.',
                    )
                else:
                    # Already signed but other party hasn't, just notify
                    await self._notify_other_party(
                        body, room_id, job_title,
                        interaction.client,
                        session, headers,
                        msg_data='You have already signed the agreement.',
                    )
                    return info_embed(message='You have already signed the agreement.')

            # ── 5. Both reviews complete, show confirmation embed ──────
            if body.get('status') != 'ok':
                return error_embed(
                    message='The service is temporarily unavailable.',
                )

            client_discord_id = body.get('client_discord_id', '')
            freelancer_discord_id = body.get('freelancer_discord_id', '')
            client_name = body.get('client_name', 'Client')
            freelancer_name = body.get('freelancer_name', 'Freelancer')
            executor_name = body.get('executor_name', 'Someone')
            other_discord_id = body.get('notify_discord_id', '')
            other_name = body.get('notify_receiver_name', 'The other party')

            confirm_embed = create_embed(
                description=(
                    '> Did you accept the Job Agreement? By accepting it you are also '
                    '> agreeing to our terms and conditions.'
                ),
                color=BrandColor.PRIMARY,
            )

            view = AgreementConfirmView(
                room_id=room_id,
                job_title=job_title,
                active_role=active_role,
                headers=headers,
                client_discord_id=client_discord_id,
                freelancer_discord_id=freelancer_discord_id,
                client_name=client_name,
                freelancer_name=freelancer_name,
                executor_name=executor_name,
                other_name=other_name,
                other_discord_id=other_discord_id,
            )
            view.author_id = interaction.user.id

            return confirm_embed, view

        await validate_and_respond(interaction, callback)


# ── setup ──────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewAgreement(bot))
    logger.info('InterviewAgreement cog loaded')
