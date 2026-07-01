import discord
from typing import Any, Dict
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, info_embed, loading_embed
from utils.analytics_collector import AnalyticsCollector
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.jobs.post_job')


class JobCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Web Development", value="web_development", description="Build websites and web applications"),
            discord.SelectOption(label="Mobile Development", value="mobile_development", description="Create iOS/Android apps"),
            discord.SelectOption(label="UI/UX Design", value="ui_ux_design", description="Design user interfaces and experiences"),
            discord.SelectOption(label="Graphic Design", value="graphic_design", description="Create visual content and branding"),
            discord.SelectOption(label="Data Science", value="data_science", description="Analyze data and build ML models"),
            discord.SelectOption(label="DevOps", value="devops", description="Deployment and infrastructure management"),
            discord.SelectOption(label="Content Writing", value="content_writing", description="Write articles, blogs, and copy"),
            discord.SelectOption(label="Video Editing", value="video_editing", description="Edit and produce video content"),
            discord.SelectOption(label="Marketing", value="marketing", description="Digital marketing and campaigns"),
            discord.SelectOption(label="Other", value="other", description="Miscellaneous services")
        ]
        super().__init__(placeholder="Select Job Category", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        view: JobPostSetupView = self.view  # type: ignore
        view.category = self.values[0]
        await interaction.response.defer()


class ExperienceLevelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Novice", value="Novice", description="Entry-level experience"),
            discord.SelectOption(label="Intermediate", value="Intermediate", description="Mid-level experience"),
            discord.SelectOption(label="Expert", value="Expert", description="Senior-level experience"),
            discord.SelectOption(label="Legend", value="Legend", description="Top-tier experience")
        ]
        super().__init__(placeholder="Select Experience Level", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        view: JobPostSetupView = self.view  # type: ignore
        view.experience_level = self.values[0]
        await interaction.response.defer()


class PremiumToggleButton(discord.ui.Button):
    def __init__(self, label, custom_id, field_name):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        view: JobPostSetupView = self.view  # type: ignore
        setattr(view, self.field_name, not getattr(view, self.field_name))
        # Update button style based on toggle state
        if getattr(view, self.field_name):
            self.style = discord.ButtonStyle.success
        else:
            self.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self.view)


class JobPostDeadlineModal(discord.ui.Modal, title="Enter Job Deadline"):
    deadline = discord.ui.TextInput(
        label="Job Deadline (Optional)",
        placeholder="e.g. 2024-12-31 or 7d for 7 days",
        required=False,
        max_length=20
    )

    def __init__(self, setup_view):
        super().__init__()
        self.setup_view = setup_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            return

        # Disable the original button to prevent double-submit
        try:
            view = discord.ui.View.from_message(interaction.message)
            for item in view.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.edit_original_response(view=view)
        except Exception:
            pass

        self.setup_view.deadline_val = self.deadline.value.strip()
        await interaction.followup.send(
            embed=success_embed(
                message=f"Job deadline set to: `{self.deadline.value or 'None (no deadline)'}`",
            ),
            ephemeral=True
        )


class JobPostDetailsModal(discord.ui.Modal, title="Enter Job Details"):
    job_title = discord.ui.TextInput(
        label="Job Title (Max 32 chars)",
        placeholder="e.g. Fullstack Developer",
        required=True,
        max_length=32
    )
    job_description = discord.ui.TextInput(
        label="Job Description (50-800 words)",
        style=discord.TextStyle.long,
        placeholder="Describe the job duties and deliverables in 50-800 words...",
        required=True,
        min_length=10,
        max_length=4000
    )
    skills = discord.ui.TextInput(
        label="Skills Required (comma separated)",
        placeholder="e.g. PYTHON, DJANGO, REACT",
        required=True,
        max_length=250
    )
    budget_min = discord.ui.TextInput(
        label="Min Budget ($)",
        placeholder="e.g. 100.00",
        required=True,
        max_length=10
    )
    budget_max = discord.ui.TextInput(
        label="Max Budget ($)",
        placeholder="e.g. 1000.00",
        required=True,
        max_length=10
    )

    def __init__(self, setup_view, title=None, description=None, skills=None,
                 budget_min=None, budget_max=None):
        super().__init__()
        self.setup_view = setup_view
        # Pre-fill with previous values on retry
        if title:
            self.job_title.default = title
        if description:
            self.job_description.default = description
        if skills:
            self.skills.default = skills
        if budget_min:
            self.budget_min.default = budget_min
        if budget_max:
            self.budget_max.default = budget_max

    async def on_submit(self, interaction: discord.Interaction):
        title_text = self.job_title.value.strip()
        description_text = self.job_description.value.strip()
        desc_word_count = len(description_text.split())
        title_char_count = len(title_text)
        raw_skills = self.skills.value or ""
        skills_list = [s.strip().upper() for s in raw_skills.split(",") if s.strip()]

        # --- Validate BEFORE deferring so we can respond with modal defaults on retry ---

        # Validate title character count
        if title_char_count > 32:
            if self.setup_view:
                self.setup_view.last_title = title_text
                self.setup_view.last_description = description_text
                self.setup_view.last_skills = raw_skills
                self.setup_view.last_budget_min = self.budget_min.value
                self.setup_view.last_budget_max = self.budget_max.value
            await interaction.response.send_message(
                embed=error_embed(message=f"Title exceeds maximum length ({title_char_count} > 32 characters)."),
                ephemeral=True
            )
            return

        # Validate description word count
        if desc_word_count < 50 or desc_word_count > 800:
            if self.setup_view:
                self.setup_view.last_title = title_text
                self.setup_view.last_description = description_text
                self.setup_view.last_skills = raw_skills
                self.setup_view.last_budget_min = self.budget_min.value
                self.setup_view.last_budget_max = self.budget_max.value
            await interaction.response.send_message(
                embed=error_embed(message=f"Description must be between 50 and 800 words. You used {desc_word_count} words."),
                ephemeral=True
            )
            return

        limit = 12 if (self.setup_view.is_premium and self.setup_view.is_featured) else 6
        if len(skills_list) > limit:
            if self.setup_view:
                self.setup_view.last_title = title_text
                self.setup_view.last_description = description_text
                self.setup_view.last_skills = raw_skills
                self.setup_view.last_budget_min = self.budget_min.value
                self.setup_view.last_budget_max = self.budget_max.value
            await interaction.response.send_message(
                embed=error_embed(message=f"Maximum of {limit} skills allowed."),
                ephemeral=True
            )
            return

        # -- Validation passed — defer and submit ---
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.errors.NotFound:
            return

        # Disable the original button to prevent double-submit
        try:
            view = discord.ui.View.from_message(interaction.message)
            for item in view.children:
                if isinstance(item, discord.ui.Button) or isinstance(item, discord.ui.Select):
                    item.disabled = True
            await interaction.edit_original_response(view=view)
        except Exception:
            pass

        url = f"{BACKEND_URL}jobs/bot/post/"
        packet = BotPacketFactory.create_packet(
            packet_type="job_post",
            data={
                'discord_id': interaction.user.id,
                'guild_id': str(interaction.guild_id) if interaction.guild else None,
                'guild_name': str(interaction.guild.name) if interaction.guild else "Direct Message",
                'title': self.job_title.value,
                'description': self.job_description.value,
                'category': self.setup_view.category,
                'skills_required': skills_list,
                'experience_level': self.setup_view.experience_level,
                'budget_min': self.budget_min.value,
                'budget_max': self.budget_max.value,
                'is_featured': self.setup_view.is_featured,
                'is_confidential': self.setup_view.is_confidential,
                'is_strict': self.setup_view.is_strict,
                'deadline': self.setup_view.deadline_val or None
            },
            provider="bot"
        )
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        try:
            session = get_http_session()
            async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                    res_data = await resp.json()
                    if resp.status == 200:
                        embed = success_embed(
                            message=f"Your job has been posted successfully under ID: `{res_data.get('job_id')}`.\n\nFreelancers can now discover and apply for this job.",
                        )
                        embed.add_field(name="Job ID", value=f"`{res_data.get('job_id')}`", inline=True)
                        embed.add_field(name="Title", value=self.job_title.value, inline=True)
                        embed.add_field(name="Budget Range", value=f"`${self.budget_min.value} - ${self.budget_max.value}`", inline=True)
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        # Fire-and-forget analytics event for job posted
                        job_data = {
                            'job_id': res_data.get('job_id', ''),
                            'job_title': self.job_title.value,
                            'budget_min': self.budget_min.value,
                            'budget_max': self.budget_max.value,
                        }
                        AnalyticsCollector.log_job_event(
                            interaction=interaction,
                            job_data=job_data,
                            event_type="job_posted",
                            display_name=interaction.user.name,
                            discord_username=interaction.user.name,
                        )
                    else:
                        await interaction.followup.send(embed=error_embed(message=res_data.get('error', "Could not post your job.")), ephemeral=True)
        except Exception as e:
            logger.error(f"Error posting job: {e}")
            await interaction.followup.send(
                embed=error_embed(message="Something went wrong posting your job. Please try again."),
                ephemeral=True
            )


class JobPostSetupView(discord.ui.View):
    def __init__(self, is_premium):
        super().__init__(timeout=300)
        self.author_id: int | None = None
        self.is_premium = is_premium
        self.category = None
        self.experience_level = None
        self.deadline_val = ""

        self.is_featured = False
        self.is_confidential = False
        self.is_strict = False

        # Store last attempt values for retry pre-fill
        self.last_title = None
        self.last_description = None
        self.last_skills = None
        self.last_budget_min = None
        self.last_budget_max = None

        self.add_item(JobCategorySelect())
        self.add_item(ExperienceLevelSelect())

        if self.is_premium:
            self.add_item(PremiumToggleButton("Featured", "toggle_featured", "is_featured"))
            self.add_item(PremiumToggleButton("Confidential", "toggle_confidential", "is_confidential"))
            self.add_item(PremiumToggleButton("Strict", "toggle_strict", "is_strict"))

        row_num = 3 if self.is_premium else 2

        self.deadline_btn = discord.ui.Button(label="Set Deadline (Optional)", style=discord.ButtonStyle.secondary, row=row_num)
        self.deadline_btn.callback = self.on_deadline
        self.add_item(self.deadline_btn)

        next_btn = discord.ui.Button(label="Next: Enter Details", style=discord.ButtonStyle.primary, row=row_num)
        next_btn.callback = self.on_next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, row=row_num)
        cancel_btn.callback = self.on_cancel
        self.add_item(cancel_btn)

    async def on_timeout(self) -> None:
        self.stop()

    async def on_deadline(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        modal = JobPostDeadlineModal(self)
        await interaction.response.send_modal(modal)

    async def on_cancel(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message="Job posting cancelled. No job was posted."),
            view=None,
        )

    async def on_next(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        if not self.category:
            await interaction.response.send_message(embed=error_embed(message="Please select a category first."), ephemeral=True)
            return
        if not self.experience_level:
            await interaction.response.send_message(embed=error_embed(message="Please select an experience level first."), ephemeral=True)
            return

        modal = JobPostDetailsModal(
            self,
            title=self.last_title,
            description=self.last_description,
            skills=self.last_skills,
            budget_min=self.last_budget_min,
            budget_max=self.last_budget_max
        )
        await interaction.response.send_modal(modal)


class PostJob(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="post_job", description="...")
    @app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
    async def post_job(self, interaction: discord.Interaction):

        async def post_job_callback(user_data):
            url = f"{BACKEND_URL}jobs/bot/post/"
            packet = BotPacketFactory.create_packet(
                packet_type="job_preflight",
                data={'preflight': True},
                provider="bot"
            )
            packet.data['discord_id'] = interaction.user.id
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                    res_data = await resp.json()
                    if resp.status == 200:
                        is_premium = res_data.get('is_premium', False)
                        embed = create_embed(
                            title="Post a Job",
                            description=(
                                f"**Configure**: Use the dropdown menus below to select your job category and experience level.\n"
                                f"**Details**: Click the configured button to fill in the title, description, skills, and budget range.\n"
                                f"**Constraint**: Description must be between 50 and 800 words."
                            ),
                            color=BrandColor.PRIMARY
                        )
                        view = JobPostSetupView(is_premium)
                        view.author_id = interaction.user.id
                        return embed, view
                    else:
                        return error_embed(message=res_data.get('error', "**You** are not eligible to post jobs."))

        await validate_and_respond(interaction, post_job_callback)


async def setup(bot):
    await bot.add_cog(PostJob(bot))
