"""
``/interview budget`` — Set the final budget for the selected interview room.

Only the client can run this in a selected interview room before the proposal
is signed by both parties.  Saves a system message in records and sends a
notification to the freelancer.
"""

import logging

import discord
from discord.ext import commands
from discord import app_commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, fetch_selected_room
from utils.embeds import success_embed, error_embed
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery

logger = logging.getLogger('bot.rooms.interview_budget')


class InterviewBudget(commands.Cog):
    """``/interview budget`` — Set the final budget in the selected room."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(
        name='interview_budget',
        description='...',
    )
    @app_commands.checks.cooldown(1, 15, key=lambda i: i.user.id)
    async def interview_budget(
        self,
        interaction: discord.Interaction,
        budget: float,
    ) -> None:
        """Set the final budget for the selected interview room (client only)."""

        async def callback(user_data: dict):
            active_role = user_data.get('active_role')
            client_name = user_data.get('discord_username', 'Client')

            # --- budget validation ---
            if budget < 50:
                return error_embed('Least budget must be $50.')

            # 1. Fetch selected room via shared resolver
            room_data = await fetch_selected_room(
                discord_id=interaction.user.id,
                role=active_role,
                room_type='interview',
                headers={'X-Webhook-Token': WEBHOOK_SECRET},
            )
            if room_data is None:
                return error_embed(
                    'No selected interview room found. Use `/switch room` to select one.'
                )

            # 2. Call backend to set the budget
            session = get_http_session()
            url = f'{BACKEND_URL}rooms/bot/set-budget/'
            payload = {
                'discord_id': str(interaction.user.id),
                'role': active_role,
                'room_id': room_data['room_id'],
                'budget': str(budget),
            }
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    body = await resp.json()
                    if resp.status == 200:
                        # ── 3. Send notification to freelancer ─────────────
                        freelancer_discord_id = body.get('freelancer_discord_id', '')
                        msg_id = body.get('msg_id', '')

                        if freelancer_discord_id and msg_id:
                            notify_data = {
                                'discord_id': freelancer_discord_id,
                                'room_id': room_data['room_id'],
                                'job_title': room_data.get('job_title', ''),
                                'command_name': 'interview_budget',
                                'executor_name': client_name,
                                'msg_data': body.get('message', f'Final budget set to ${budget}.'),
                            }

                            delivery_ok = await handle_system_message(
                                message_type='room_interview_message',
                                data=notify_data,
                                bot=interaction.client,
                            )

                            if not delivery_ok:
                                await log_failed_delivery(
                                    room_id=room_data['room_id'],
                                    message_type='notification',
                                    target_discord_id=freelancer_discord_id,
                                    msg_id=msg_id,
                                    session=session,
                                    headers=headers,
                                )

                        return success_embed(
                            f'Final budget set to **${budget:,.2f}**.'
                        )
                    return error_embed(
                        body.get('error', 'Failed to set budget.')
                    )
            except Exception as e:
                logger.exception('Error setting budget: %s', e)
                return error_embed(
                    'Unable to reach the backend service right now. Please try again later.'
                )

        await validate_and_respond(interaction, callback)


# ── setup ──────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewBudget(bot))
    logger.info('InterviewBudget cog loaded')
