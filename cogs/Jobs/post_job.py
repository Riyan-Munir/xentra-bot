import discord
from discord.ext import commands
from discord import app_commands
from utils.embeds import create_embed, BrandColor, error_embed, success_embed, info_embed, loading_embed
from utils.http import get_http_session
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import sync_cog_commands, validate_and_respond, is_author
from packet_templates.factory import BotPacketFactory
import logging

logger = logging.getLogger(__name__)


class JobCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Web Development", value="web_dev", description="Websites, web apps, SaaS"),
            discord.SelectOption(label="Mobile Development", value="mobile_dev", description="iOS, Android, cross-platform"),
            discord.SelectOption(label="Desktop Development", value="desktop_dev", description="Windows, macOS, Linux apps"),
            discord.SelectOption(label="AI / Machine Learning", value="ai_ml", description="LLMs, computer vision, data science"),
            discord.SelectOption(label="Blockchain / Web3", value="blockchain", description="Smart contracts, dApps, DeFi"),
            discord.SelectOption(label="DevOps / Cloud", value="devops", description="CI/CD, k8s, AWS, GCP, Azure"),
            discord.SelectOption(label="Cybersecurity", value="cybersecurity", description="Pentesting, audits, secure coding"),
            discord.SelectOption(label="Game Development", value="game_dev", description="Unity, Unreal, Godot, WebGL"),
            discord.SelectOption(label="Scripting / Automation", value="scripting", description="Bots, scrapers, automation tools"),
            discord.SelectOption(label="Other", value="other", description="Anything else not listed"),
        ]
        super().__init__(placeholder="Select a job category", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        self.view.category_label = self.values[0]
        await interaction.response.edit_message()


class ExperienceLevelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Entry Level", value="entry", description="0-2 years of experience"),
            discord.SelectOption(label="Intermediate", value="intermediate", description="2-5 years of experience"),
            discord.SelectOption(label="Expert", value="expert", description="5+ years of experience"),
        ]
        super().__init__(placeholder="Select experience level", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        self.view.experience_label = self.values[0]
        await interaction.response.edit_message()


class PremiumToggleButton(discord.ui.Button):
    def __init__(self, label, custom_id, field_name):
        super().__init__(label=label, custom_id=custom_id, style=discord.ButtonStyle.primary)
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        setattr(self.view, self.field_name, not getattr(self.view, self.field_name))
        self.label = "Featured: ON" if getattr(self.view, self.field_name) else "Featured: OFF"
        await interaction.response.edit_message(view=self.view)


class JobPostDeadlineModal(discord.ui.Modal, title="Enter Job Deadline"):
    deadline = discord.ui.TextInput(
        label="Deadline",
        placeholder="e.g. 2025-12-31 or 7 (days from now)",
        required=False,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        try:
            if self.deadline.value.strip():
                self.view.deadline = self.deadline.value.strip()
            await interaction.response.edit_message()
        except discord.errors.NotFound:
            pass


class JobPostDetailsModal(discord.ui.Modal, title="Enter Job Details"):
    job_title = discord.ui.TextInput(
        label="Job Title",
        placeholder="e.g. Fullstack Developer (max 64 chars)",
        required=True,
        max_length=64
    )
    job_description = discord.ui.TextInput(
        label="Job Description",
        style=discord.TextStyle.long,
        placeholder="Describe the job duties and deliverables (50-800 words)",
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
        label="Minimum Budget ($)",
        placeholder="e.g. 500",
        required=True,
        max_length=10
    )
    budget_max = discord.ui.TextInput(
        label="Maximum Budget ($)",
        placeholder="e.g. 5000",
        required=True,
        max_length=10
    )

    def __init__(self, setup_view, title=None, description=None, skills=None,
                 budget_min=None, budget_max=None, deadline=None):
        super().__init__(timeout=600)
        self.setup_view = setup_view
        if title:
            self.job_title.default = title
        if description:
            self.job_description.default = description
        if skills:
            self.skills.default = skills
        if budget_min:
            self.budget_min.default = str(budget_min)
        if budget_max:
            self.budget_max.default = str(budget_max)

    async def on_submit(self, interaction: discord.Interaction):
        title_text = self.job_title.value.strip()
        desc_text = self.job_description.value.strip()
        skills_text = str(self.skills.value).strip()
        budget_min_value = float(self.budget_min.value.strip())
        budget_max_value = float(self.budget_max.value.strip())

        word_count = len(desc_text.split())
        if word_count < 50 or word_count > 800:
            try:
                await interaction.response.send_message(
                    embed=error_embed(message=f"Description must be between 50 and 800 words. You used {word_count} words."),
                    ephemeral=not (interaction.guild is None),
                )
            except discord.errors.NotFound:
                pass
            return

        self.setup_view.last_title = title_text
        self.setup_view.last_description = desc_text
        self.setup_view.last_skills = skills_text
        self.setup_view.last_budget_min = budget_min_value
        self.setup_view.last_budget_max = budget_max_value
        self.setup_view.last_deadline = getattr(self.setup_view, 'deadline', '')

        if budget_min_value >= budget_max_value and title_text and desc_text:
            try:
                await interaction.response.send_message(
                    embed=error_embed(message="Maximum budget must exceed minimum budget."),
                    ephemeral=not (interaction.guild is None),
                )
            except discord.errors.NotFound:
                pass
            return

        # Validate deadline if provided
        deadline_value = getattr(self.setup_view, 'deadline', '')
        if deadline_value:
            from datetime import datetime
            import re
            date_pattern = re.compile(r'\d{4}-\d{2}-\d{2}')
            if date_pattern.match(deadline_value):
                try:
                    datetime.strptime(deadline_value, '%Y-%m-%d')
                except ValueError:
                    try:
                        await interaction.response.send_message(
                            embed=error_embed(message="Invalid date format. Use YYYY-MM-DD."),
                            ephemeral=not (interaction.guild is None),
                        )
                    except discord.errors.NotFound:
                        pass
                    return
            else:
                try:
                    days = int(deadline_value)
                    if days < 1:
                        raise ValueError
                except ValueError:
                    try:
                        await interaction.response.send_message(
                            embed=error_embed(message="Invalid deadline. Enter a date (YYYY-MM-DD) or a positive number of days."),
                            ephemeral=not (interaction.guild is None),
                        )
                    except discord.errors.NotFound:
                        pass
                    return

        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            return

        try:
            url = f"{BACKEND_URL}jobs/bot/post/"
            packet = BotPacketFactory.create_packet(
                packet_type="job_post",
                data={
                    'discord_id': interaction.user.id,
                    'guild_id': str(interaction.guild_id),
                    'guild_name': str(interaction.guild.name) if interaction.guild else "Direct Message",
                    'title': title_text,
                    'description': desc_text,
                    'skills': skills_text,
                    'budget_min': budget_min_value,
                    'budget_max': budget_max_value,
                    'category': self.setup_view.category_label,
                    'experience': self.setup_view.experience_label,
                    'featured': self.setup_view.featured,
                    'deadline': deadline_value or None,
                },
                provider="bot"
            )
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    embed = success_embed(
                        title="Job Posted Successfully",
                        message=f"Your job listing has been posted!\n\n**Job ID**: `{data.get('job_id', '')}`"
                    )
                    await interaction.edit_original_response(embed=embed, view=None)
                    self.setup_view.disable_all = True
                    self.setup_view.stop()
                else:
                    try:
                        err = await resp.json()
                        msg = err.get('error', 'Failed to post job.')
                    except Exception:
                        msg = 'Failed to post job.'
                    await interaction.edit_original_response(
                        embed=error_embed(message=msg), view=None
                    )
        except Exception as e:
            logger.exception(f"Error posting job: {e}")
            await interaction.edit_original_response(
                embed=error_embed(message="The service is temporarily unavailable."),
                view=None
            )


class JobPostSetupView(discord.ui.View):
    def __init__(self, is_premium):
        super().__init__(timeout=300)
        self.author_id: int | None = None
        self._done = False
        self.category_label: str = ''
        self.experience_label: str = ''
        self.last_title = ''
        self.last_description = ''
        self.last_skills = ''
        self.last_budget_min = 0.0
        self.last_budget_max = 0.0
        self.last_deadline: str = ''
        self.deadline: str = ''
        self.featured = False
        self.is_premium = is_premium
        self.disable_all = False

        self.add_item(JobCategorySelect())
        self.add_item(ExperienceLevelSelect())
        if is_premium:
            self.add_item(PremiumToggleButton("Featured: OFF", "premium_toggle", "featured"))

    async def on_timeout(self) -> None:
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.disable_all:
            await interaction.response.edit_message(view=None)
            return False
        return True

    @discord.ui.button(label="Details", style=discord.ButtonStyle.secondary, row=1)
    async def on_details(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_author(interaction, self):
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

    @discord.ui.button(label="Deadline", style=discord.ButtonStyle.secondary, row=1)
    async def on_deadline(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        modal = JobPostDeadlineModal()
        modal.view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=2)
    async def on_cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self._done = True
        self.stop()
        embed = info_embed(message="Job posting cancelled.")
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Proceed", style=discord.ButtonStyle.success, row=2)
    async def on_next(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        if self._done:
            return
        if not self.last_title or not self.last_description:
            embed = error_embed(message="Please fill in the job details first.")
            await interaction.response.edit_message(embed=embed, view=self)
            return
        if not self.category_label:
            embed = error_embed(message="Please select a job category.")
            await interaction.response.edit_message(embed=embed, view=self)
            return
        if not self.experience_label:
            embed = error_embed(message="Please select an experience level.")
            await interaction.response.edit_message(embed=embed, view=self)
            return
        self._done = True
        self.disable_all = True

        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            return

        url = f"{BACKEND_URL}jobs/bot/post/"
        packet = BotPacketFactory.create_packet(
            packet_type="job_post",
            data={
                'discord_id': interaction.user.id,
                'guild_id': str(interaction.guild_id),
                'guild_name': str(interaction.guild.name) if interaction.guild else "Direct Message",
                'title': self.last_title,
                'description': self.last_description,
                'skills': self.last_skills,
                'budget_min': self.last_budget_min,
                'budget_max': self.last_budget_max,
                'category': self.category_label,
                'experience': self.experience_label,
                'featured': self.featured,
                'deadline': self.deadline or None,
            },
            provider="bot"
        )
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        session = get_http_session()
        async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
            if resp.status in (200, 201):
                data = await resp.json()
                embed = success_embed(
                    title="Job Posted Successfully",
                    message=f"Your job listing has been posted!\n\n**Job ID**: `{data.get('job_id', '')}`"
                )
                await interaction.edit_original_response(embed=embed, view=None)
                self.stop()
            else:
                try:
                    err = await resp.json()
                    msg = err.get('error', 'Failed to post job.')
                except Exception:
                    msg = 'Failed to post job.'
                await interaction.edit_original_response(
                    embed=error_embed(message=msg), view=None
                )


class PostJob(commands.Cog):
    """``/post_job``, Post a new job listing."""

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
                    if resp.status in (200, 201):
                        is_premium = res_data.get('is_premium', False)
                        embed = create_embed(
                            title="Post a Job",
                            description=(
                                "> **Configure**, Use the dropdown menus below to select your job category and experience level.\n"
                                "> **Details**, Click the configured button to fill in the title, description, skills, and budget range.\n"
                                "> **Constraint**, Description must be between 50 and 800 words."
                            ),
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Jobs"
                        )
                        view = JobPostSetupView(is_premium)
                        view.author_id = interaction.user.id
                        return embed, view
                    else:
                        return error_embed(message=res_data.get('error', "You are not eligible to post jobs."))

        await validate_and_respond(interaction, post_job_callback)


async def setup(bot):
    await bot.add_cog(PostJob(bot))
