"""
``/interview budget``, Set the final budget for the selected interview room.

Only the client can run this in a selected interview room before the proposal
is signed by both parties.  Saves a system message in records and sends a
notification to the freelancer.
"""

import logging

import discord
from discord.ext import commands
from discord import app_commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import success_embed, error_embed
from utils.http import get_http_session
from ._shared import record_and_notify

logger = logging.getLogger('bot.rooms.interview_budget')


class InterviewBudget(commands.Cog):
    """``/interview budget``, Set the final budget in the selected room."""

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

            room_data = user_data['_selected_room']

            session = get_http_session()
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            # --- budget validation ---
            if budget < 50:
                error_msg = 'Could not set the budget. Minimum amount is $50.'
                await record_and_notify(
                    room_id=room_data['room_id'],
                    sender_role='client',
                    msg_data=error_msg,
                    command_name='interview_budget',
                    target_discord_id='',
                    executor_name=client_name,
                    job_title=room_data.get('job_title', ''),
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

            # 2. Call backend to set the budget
            url = f'{BACKEND_URL}rooms/bot/set-budget/'
            payload = {
                'discord_id': str(interaction.user.id),
                'room_id': room_data['room_id'],
                'budget': str(budget),
            }

            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    body = await resp.json()
                    if resp.status == 200:
                        # ── 3. Record message + notify freelancer ──────────
                        freelancer_discord_id = body.get('freelancer_discord_id', '')

                        success_msg = (
                            f'Final budget set to **${budget:,.2f}** for job '
                            f'**{room_data.get("job_title", "")}**.'
                        )

                        await record_and_notify(
                            room_id=room_data['room_id'],
                            sender_role='client',
                            msg_data=success_msg,
                            command_name='interview_budget',
                            target_discord_id=freelancer_discord_id,
                            executor_name=client_name,
                            job_title=room_data.get('job_title', ''),
                            bot=interaction.client,
                            session=session,
                            headers=headers,
                        )

                        return success_embed(success_msg)

                    error_msg = body.get('error', 'Could not set the budget.')
                    await record_and_notify(
                        room_id=room_data['room_id'],
                        sender_role='client',
                        msg_data=error_msg,
                        command_name='interview_budget',
                        target_discord_id='',
                        executor_name=client_name,
                        job_title=room_data.get('job_title', ''),
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    return error_embed(error_msg)
            except Exception as e:
                logger.exception('Error setting budget: %s', e)
                error_msg = 'The service is temporarily unavailable.'
                await record_and_notify(
                    room_id=room_data['room_id'],
                    sender_role='client',
                    msg_data=error_msg,
                    command_name='interview_budget',
                    target_discord_id='',
                    executor_name=client_name,
                    job_title=room_data.get('job_title', ''),
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(error_msg)

        await validate_and_respond(interaction, callback)


# ── setup ──────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewBudget(bot))
    logger.info('InterviewBudget cog loaded')
