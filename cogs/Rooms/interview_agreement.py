"""
``/interview agreement``, Sign the agreement of the selected interview room.

Flow:
  1. Command handler validates the user and room context.
  2. Fetches selected interview room via shared resolver.
  3. Calls ``BotProcessAgreementView`` GET to check review flags.
  4. If reviews incomplete, error with notification to the other party.
  5. If both reviewed, shows confirmation embed with Accept / Decline.
  6. On **Accept**, POST to ``BotAcceptAgreementView``, show success embed.
  7. Records the command message and DM-notifies the other party.
  8. If **both** parties have now accepted, fires the signed PDF request via
     ``request_pdf()`` (fire-and-forget) and keeps the closure process.
"""

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import success_embed, error_embed, info_embed, create_embed, BrandColor
from utils.http import get_http_session
from utils.room_closure import send_room_closure_and_transcript
from ._shared import record_and_notify, request_pdf

logger = logging.getLogger('bot.rooms.interview_agreement')


async def _finalize_agreement_closure(
    room_id: str,
    agreement_id: str,
    headers: dict,
) -> list[str]:
    """Call ``finalize-closure/`` and return the resulting ``closure_ids`` list.

    The backend persists everything in one atomic transaction (winning room
    closure + all system-closed rooms) and returns their closure ids.  The
    closure utility then only delivers notifications + transcripts.
    """
    session = get_http_session()
    try:
        async with session.post(
            f'{BACKEND_URL}rooms/bot/finalize-closure/',
            json={'room_id': room_id, 'agreement_id': agreement_id},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    return data.get('closure_ids', [])
                logger.warning(
                    'finalize-closure failed: %s', data.get('message', ''),
                )
            else:
                logger.warning(
                    'finalize-closure returned %s for room %s',
                    resp.status, room_id,
                )
    except Exception:
        logger.exception('Failed to finalize closure for room %s', room_id)
    return []


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
        success_msg = "You've signed the Job Agreement."
        success = success_embed(success_msg)
        await interaction.edit_original_response(embed=success, view=None)

        # ── Record + notify the other party ──────────────────────────────
        await record_and_notify(
            room_id=self.room_id,
            sender_role=self.active_role,
            msg_data=success_msg,
            command_name='interview_agreement',
            target_discord_id=self.other_discord_id,
            executor_name=self.executor_name,
            job_title=self.job_title,
            bot=interaction.client,
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
        """Fire the signed agreement PDF request and run the closure sequence."""
        # Store agreement_id for closure sequence
        self._agreement_id = body.get('agreement_id', '')

        task_id = await request_pdf(
            task_type='signed_agreement',
            room_id=self.room_id,
            room_type='interview',
            requester_discord_id=str(interaction.user.id),
            client_discord_id=str(self.client_discord_id),
            freelancer_discord_id=str(self.freelancer_discord_id),
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

        # ── Room Closure Sequence (fire-and-forget) ───────────────────
        # The backend persists every closure record (winning + system) in
        # one request and returns their ids; the utility only delivers.
        async def _run_closure():
            try:
                await asyncio.sleep(30)
                closure_ids = await _finalize_agreement_closure(
                    room_id=self.room_id,
                    agreement_id=self._agreement_id,
                    headers=self.headers,
                )
                if not closure_ids:
                    logger.error(
                        'No closure ids returned for room %s, skipping closure delivery',
                        self.room_id,
                    )
                    return
                await send_room_closure_and_transcript(
                    closure_ids=closure_ids,
                    bot=interaction.client,
                    headers=self.headers,
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

        # ── Record the decline (save-only) ─────────────────────────────
        session = get_http_session()
        await record_and_notify(
            room_id=self.room_id,
            sender_role=self.active_role,
            msg_data='Agreement signing cancelled.',
            command_name='interview_agreement',
            target_discord_id='',
            executor_name=self.executor_name,
            job_title=self.job_title,
            bot=interaction.client,
            session=session,
            headers=self.headers,
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

            # ── Determine sender display name ────────────────────────────
            if active_role == 'client':
                sender_name = room_data.get('client_name', 'Client')
            else:
                sender_name = room_data.get('freelancer_name', 'Freelancer')

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
                error_msg = 'The service is temporarily unavailable.'
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_agreement',
                    target_discord_id='',
                    executor_name=sender_name,
                    job_title=job_title,
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

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

                    # Record + notify the other party (the one who needs to act)
                    notify_discord_id = body.get('notify_discord_id', '')
                    await record_and_notify(
                        room_id=room_id,
                        sender_role=active_role,
                        msg_data=error_msg,
                        command_name='interview_agreement',
                        target_discord_id=notify_discord_id,
                        executor_name=body.get('notify_executor_name', body.get('notify_receiver_name', 'Someone')),
                        job_title=job_title,
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(message=error_msg)

                # Fallback for unknown error codes
                error_msg = body.get('message', 'The service is temporarily unavailable.')
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_agreement',
                    target_discord_id='',
                    executor_name=sender_name,
                    job_title=job_title,
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

            # ── 4. Both reviews complete, show confirmation embed ──────
            if body.get('status') != 'ok':
                error_msg = 'The service is temporarily unavailable.'
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_agreement',
                    target_discord_id='',
                    executor_name=sender_name,
                    job_title=job_title,
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

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
