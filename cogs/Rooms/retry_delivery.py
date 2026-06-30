"""
``/interview delivery`` — Retry any failed system message deliveries
(rules, job details) for your active interview rooms.

The bot stores a backend record when it cannot reach a user (DMs disabled /
blocked).  This command fetches all unresolved records for the calling user,
re-attempts delivery via ``handle_system_message()``, and marks successes as
resolved.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import (
    error_embed,
    success_embed,
    info_embed,
    loading_embed,
)
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message

logger = logging.getLogger('bot.rooms.retry_delivery')

PENDING_URL = f'{BACKEND_URL}rooms/bot/pending-deliveries/'
RECONSTRUCT_URL = f'{BACKEND_URL}rooms/bot/reconstruct-delivery/'
RESOLVE_URL = f'{BACKEND_URL}rooms/bot/resolve-delivery/'


class InterviewDelivery(commands.Cog):
    """``/interview delivery`` — retry failed system-message deliveries."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    # ------------------------------------------------------------------
    #  Slash command
    # ------------------------------------------------------------------

    @app_commands.command(
        name='interview_delivery',
        description='...',
    )
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def interview_delivery(self, interaction: discord.Interaction) -> None:
        """Fetch pending deliveries, attempt re-delivery, show results."""

        async def callback(user_data: dict) -> discord.Embed:
            session = get_http_session()
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            # ── 1. Fetch pending deliveries ─────────────────────────────
            params = {'discord_id': str(interaction.user.id)}
            try:
                async with session.get(PENDING_URL, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        err_data = await resp.json()
                        return error_embed(
                            message=f'Failed to fetch pending deliveries: {err_data.get("error", "Unknown error")}',
                        )
                    data = await resp.json()
            except Exception:
                logger.exception('Failed to fetch pending deliveries')
                return error_embed(message='Could not check deliveries due to a system error.')

            pending = data.get('pending', [])

            if not pending:
                return info_embed(
                    message='All system messages have been delivered successfully. No pending deliveries found.',
                )

            # ── 2. Re-attempt each delivery ─────────────────────────────
            total = len(pending)
            succeeded = 0
            failed = 0

            results_lines: list[str] = []

            for idx, delivery in enumerate(pending):
                record_id = delivery['id']
                room_id = delivery['room_id']
                message_type = delivery['message_type']
                target_discord_id = delivery['target_discord_id']

                # ── Call reconstruct endpoint ─────────────────────────
                try:
                    async with session.post(
                        RECONSTRUCT_URL,
                        json={'record_id': record_id},
                        headers=headers,
                    ) as recon_resp:
                        if recon_resp.status != 200:
                            err = await recon_resp.json()
                            logger.warning(
                                'Reconstruct failed for delivery %s: %s', record_id, err.get('error', 'Unknown'),
                            )
                            failed += 1
                            results_lines.append(
                                f'`{room_id}` — reconstruction failed',
                            )
                            continue
                        recon_data = await recon_resp.json()
                except Exception:
                    logger.exception('Exception during reconstruct for delivery %s', record_id)
                    failed += 1
                    results_lines.append(
                        f'`{room_id}` — reconstruction request failed',
                    )
                    continue

                handler_type = recon_data.get('handler_type', message_type)
                payload = recon_data.get('data', {})

                # Ensure discord_id is in the payload (handler needs it)
                if 'discord_id' not in payload:
                    payload['discord_id'] = target_discord_id

                # ── Attempt delivery ──────────────────────────────────
                try:
                    ok = await handle_system_message(
                        message_type=handler_type,
                        data=payload,
                        bot=interaction.client,
                    )
                except Exception:
                    logger.exception(
                        'Exception during retry of delivery %s (%s)', record_id, message_type,
                    )
                    ok = False

                if ok:
                    # Mark as resolved in backend
                    try:
                        async with session.post(
                            f'{RESOLVE_URL}{record_id}/',
                            json={},
                            headers=headers,
                        ) as resp:
                            if resp.status == 200:
                                logger.info(
                                    'Retry succeeded for delivery %s (%s)', record_id, message_type,
                                )
                            else:
                                logger.warning(
                                    'Retry succeeded but resolve POST failed for %s', record_id,
                                )
                    except Exception:
                        logger.exception('Failed to mark delivery %s as resolved', record_id)

                    succeeded += 1
                    results_lines.append(
                        f'`{room_id}` — delivered successfully',
                    )
                else:
                    failed += 1
                    results_lines.append(
                        f'`{room_id}` — user still unreachable',
                    )

            # ── 3. Build results embed ─────────────────────────────────
            summary = (
                f'**Retry complete — {succeeded} delivered, {failed} failed**\n\n'
                + '\n'.join(results_lines)
            )

            if failed == 0:
                return success_embed(message=summary)
            elif succeeded > 0:
                return info_embed(message=summary)
            return error_embed(message=summary)

        await validate_and_respond(interaction, callback)


# ── Cog setup ─────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    cog = InterviewDelivery(bot)
    await bot.add_cog(cog)
    logger.info('InterviewDelivery cog loaded')
