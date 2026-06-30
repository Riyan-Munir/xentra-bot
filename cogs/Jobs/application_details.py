import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, loading_embed
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.job_mgmt')


class ApplicationDetailsCommand(commands.Cog):
    """``/application details`` — Display application details."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(name="application_details", description="Display application details.")
    @app_commands.checks.cooldown(3, 10, key=lambda i: i.user.id)
    async def application_details(
        self,
        interaction: discord.Interaction,
        application_id: str,
    ) -> None:
        """Fetch and show details for a specific job application."""

        async def callback(user_data: dict) -> tuple:
            url = f"{BACKEND_URL}jobs/bot/application-detail/"
            params = {
                'discord_id': str(interaction.user.id),
                'application_id': application_id,
            }
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        embed = create_embed(
                            title="Application Details",
                            description=(
                                f"> **Job Title:** `{data.get('job_title', 'Unknown')}`\n"
                                f"> **Status:** `{data.get('status', 'Unknown')}`\n"
                                f"> **Bid:** `${data.get('bid', 'N/A')}`\n"
                                f"> **Proposal:**\n> {data.get('proposal', 'No proposal provided.')[:1000]}"
                            ),
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Application Details",
                        )

                        if data.get('created_at'):
                            embed.add_field(
                                name="Submitted",
                                value=f"`{data['created_at'][:19].replace('T', ' ')}`",
                                inline=True,
                            )
                        if data.get('freelancer_name'):
                            embed.add_field(
                                name="Freelancer",
                                value=f"> `{data['freelancer_name']}`",
                                inline=True,
                            )

                        return embed, None
                    else:
                        err_data = await resp.json()
                        return error_embed(
                            message=err_data.get(
                                "error",
                                "Could not fetch application details. "
                                "Check the application ID and try again.",
                            )
                        )
            except Exception as e:
                logger.error(f"Error fetching application details: {e}")
                return error_embed(
                    message="Something went wrong fetching application details. Please try again."
                )

        await validate_and_respond(interaction, callback)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApplicationDetailsCommand(bot))
