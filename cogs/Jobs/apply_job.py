import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, info_embed, loading_embed
from utils.analytics_collector import AnalyticsCollector
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.jobs.apply_job')


class JobApplicationModal(discord.ui.Modal, title='Submit Job Application'):
    proposal = discord.ui.TextInput(
        label='Proposal Text (50-300 words)',
        style=discord.TextStyle.long,
        placeholder='Describe why you are the best fit in exactly 50-300 words...',
        required=True,
        min_length=10,
        max_length=1000
    )
    bid = discord.ui.TextInput(
        label='Bid Amount ($)',
        placeholder='e.g. 150.00 (Must be within the job budget)',
        required=True,
        max_length=10
    )

    def __init__(self, job_id, original_interaction, budget_min=None, proposal_text=None, bid_amount=None, preflight_view=None):
        super().__init__()
        self.job_id = job_id
        self.original_interaction = original_interaction
        self.preflight_view = preflight_view
        # Pre-fill with previous values on retry
        if proposal_text:
            self.proposal.default = proposal_text
        if bid_amount:
            self.bid.default = str(bid_amount)
        elif budget_min and not bid_amount:
            self.bid.default = str(budget_min)

    async def on_submit(self, interaction: discord.Interaction):
        proposal_text = self.proposal.value.strip()
        word_count = len(proposal_text.split())

        # --- Validate before deferring so we can show error + keep preflight alive ---

        # 1. Validate proposal word count
        if word_count < 50 or word_count > 300:
            # Store values in preflight view so retry modal is pre-filled
            if self.preflight_view:
                self.preflight_view.last_proposal = proposal_text
                self.preflight_view.last_bid = self.bid.value
            await interaction.response.send_message(
                embed=error_embed(message=f"Proposal must be between 50 and 300 words. You used {word_count} words."),
                ephemeral=True
            )
            return

        # 2. Validate bid is numeric
        try:
            bid_amount = float(self.bid.value)
            if bid_amount <= 0:
                raise ValueError
        except ValueError:
            if self.preflight_view:
                self.preflight_view.last_proposal = proposal_text
                self.preflight_view.last_bid = self.bid.value
            await interaction.response.send_message(
                embed=error_embed(message="Bid must be a positive number."),
                ephemeral=True
            )
            return

        # -- Validation passed — defer and submit ---
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.errors.NotFound:
            return

        # Disable the original button to prevent double-app
        try:
            view = discord.ui.View.from_message(interaction.message)
            for item in view.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.edit_original_response(view=view)
        except Exception:
            pass  # Ignore errors disabling view

        # Submit application
        url = f"{BACKEND_URL}jobs/bot/apply/"
        packet = BotPacketFactory.create_packet(
            packet_type="job_apply",
            data={
                'discord_id': interaction.user.id,
                'guild_id': str(interaction.guild_id),
                'guild_name': str(interaction.guild.name) if interaction.guild else "Direct Message",
                'job_id': self.job_id,
                'proposal': proposal_text,
                'bid': bid_amount
            },
            provider="bot"
        )
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        try:
            session = get_http_session()
            async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                    res_data = await resp.json()
                    if resp.status in (200, 201):
                        await interaction.followup.send(
                            embed=success_embed(message="Job application submitted successfully!"),
                            ephemeral=True
                        )
                        # Fire-and-forget analytics event for job application
                        job_data = {
                            'job_id': self.job_id,
                            'job_title': res_data.get('job_title', ''),
                        }
                        AnalyticsCollector.log_job_event(
                            interaction=interaction,
                            job_data=job_data,
                            event_type="job_application",
                            display_name=interaction.user.name,
                            discord_username=interaction.user.name,
                        )
                    else:
                        await interaction.followup.send(
                            embed=error_embed(message=res_data.get('error', "Failed to submit application.")),
                            ephemeral=True
                        )
        except Exception as e:
            logger.error(f"Error submitting job application: {e}")
            await interaction.followup.send(
                embed=error_embed(message="Something went wrong. Please try again."),
                ephemeral=True
            )


class ApplyJobPreflightView(discord.ui.View):
    def __init__(self, job_id, budget_min=None):
        super().__init__(timeout=180)
        self.author_id: int | None = None
        self.job_id = job_id
        self.budget_min = budget_min
        # Store last attempt values for retry pre-fill
        self.last_proposal = None
        self.last_bid = None

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label="Submit Application", style=discord.ButtonStyle.primary)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        try:
            modal = JobApplicationModal(
                self.job_id, interaction,
                budget_min=self.budget_min,
                proposal_text=self.last_proposal,
                bid_amount=self.last_bid,
                preflight_view=self
            )
            await interaction.response.send_modal(modal)
        except discord.errors.NotFound:
            await interaction.followup.send(embed=error_embed(message="Interaction expired or unknown. Please run the command again."), ephemeral=True)
        except Exception as e:
            logger.error(f"Error sending modal: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)
            else:
                await interaction.followup.send(embed=error_embed(message="Something went wrong. Please try again."), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        self.stop()
        embed = info_embed(
            message="Application process cancelled."
        )
        await interaction.response.edit_message(embed=embed, view=None)


class ApplyJob(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="apply_job", description="...")
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def apply_job(self, interaction: discord.Interaction, job_id: str):

        async def apply_callback(user_data):
            url = f"{BACKEND_URL}jobs/bot/apply/"
            packet = BotPacketFactory.create_packet(
                packet_type="job_apply_preflight",
                data={
                    'discord_id': interaction.user.id,
                    'job_id': job_id,
                    'preflight': True
                },
                provider="bot"
            )
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                    res_data = await resp.json()
                    if resp.status == 200:
                        embed = create_embed(
                            title="Job Application Preflight",
                            description=(
                                "Check the job details and click the button below to submit your proposal and bid.\n\n"
                                f"**Target Job ID**: `{job_id}`\n"
                                "**Constraint**: Proposals must be between 50 and 300 words."
                            ),
                            color=BrandColor.PRIMARY
                        )
                        embed.set_footer(text="Xentra • Verify eligibility and input constraints before submitting.")
                        budget_min = res_data.get('budget_min')
                        view = ApplyJobPreflightView(job_id, budget_min)
                        view.author_id = interaction.user.id
                        return embed, view
                    else:
                        return error_embed(message=res_data.get('error', "You are not eligible for this job."))

        await validate_and_respond(interaction, apply_callback)


async def setup(bot):
    await bot.add_cog(ApplyJob(bot))
