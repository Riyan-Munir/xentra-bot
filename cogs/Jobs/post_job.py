import discord
from discord.ext import commands
from discord import app_commands
from utils.embeds import create_embed, BrandColor, error_embed, success_embed, info_embed
from utils.http import get_http_session
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import sync_cog_commands, validate_and_respond, is_author
from packet_templates.factory import BotPacketFactory
import logging

logger = logging.getLogger(__name__)


# =====================================================================
#  Dropdowns
# =====================================================================


class JobCategorySelect(discord.ui.Select):
    """Dropdown for job category selection with mirrored selection."""

    def __init__(self):
        self._all_options = [
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
        super().__init__(placeholder="Select a job category", options=self._all_options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        selected_value = self.values[0]
        selected_label = next(opt.label for opt in self._all_options if opt.value == selected_value)
        self.view.category_label = selected_value
        # Mirror selection in dropdown placeholder
        self.placeholder = f"✓ {selected_label}"
        await interaction.response.edit_message(view=self.view)


class ExperienceLevelSelect(discord.ui.Select):
    """Dropdown for experience level selection with mirrored selection."""

    def __init__(self):
        self._all_options = [
            discord.SelectOption(label="Entry Level", value="entry", description="0-2 years of experience"),
            discord.SelectOption(label="Intermediate", value="intermediate", description="2-5 years of experience"),
            discord.SelectOption(label="Expert", value="expert", description="5+ years of experience"),
        ]
        super().__init__(placeholder="Select experience level", options=self._all_options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        selected_value = self.values[0]
        selected_label = next(opt.label for opt in self._all_options if opt.value == selected_value)
        self.view.experience_label = selected_value
        # Mirror selection in dropdown placeholder
        self.placeholder = f"✓ {selected_label}"
        await interaction.response.edit_message(view=self.view)


# =====================================================================
#  Modal – Job details + optional deadline (single step after Proceed)
# =====================================================================


class JobPostDetailsModal(discord.ui.Modal, title="Enter Job Details"):
    """Modal collecting all job details. Deadline is optional."""

    job_title = discord.ui.TextInput(
        label="Job Title",
        placeholder="e.g. Fullstack Developer (max 64 chars)",
        required=True,
        max_length=64,
    )
    job_description = discord.ui.TextInput(
        label="Job Description",
        style=discord.TextStyle.long,
        placeholder="Describe the job duties and deliverables (50-800 words)",
        required=True,
        min_length=10,
        max_length=4000,
    )
    skills = discord.ui.TextInput(
        label="Skills Required (comma separated)",
        placeholder="e.g. PYTHON, DJANGO, REACT",
        required=True,
        max_length=250,
    )
    budget_range = discord.ui.TextInput(
        label="Budget Range ($min - $max)",
        placeholder="e.g. 500-5000",
        required=True,
        max_length=30,
    )
    deadline = discord.ui.TextInput(
        label="Deadline (optional)",
        placeholder="e.g. 2025-12-31 or 7 (days from now)",
        required=False,
        max_length=20,
    )

    def __init__(self, setup_view):
        super().__init__(timeout=600)
        self.setup_view = setup_view
        # Pre-fill with previous values on retry
        if setup_view.last_title:
            self.job_title.default = setup_view.last_title
        if setup_view.last_description:
            self.job_description.default = setup_view.last_description
        if setup_view.last_skills:
            self.skills.default = setup_view.last_skills
        if setup_view.last_budget_min and setup_view.last_budget_max:
            self.budget_range.default = (
                f"{setup_view.last_budget_min}-{setup_view.last_budget_max}"
            )

    async def on_submit(self, interaction: discord.Interaction):
        title_text = self.job_title.value.strip()
        desc_text = self.job_description.value.strip()
        skills_text = str(self.skills.value).strip()
        budget_text = self.budget_range.value.strip()
        deadline_text = self.deadline.value.strip() if self.deadline.value else ""

        # ── Validate description word count ────────────────────────
        word_count = len(desc_text.split())
        if word_count < 50 or word_count > 800:
            try:
                await interaction.response.send_message(
                    embed=error_embed(
                        message=f"Description must be between 50 and 800 words. You used {word_count} words."
                    ),
                    ephemeral=not (interaction.guild is None),
                )
            except discord.errors.NotFound:
                pass
            return

        # ── Parse budget range ─────────────────────────────────────
        try:
            parts = budget_text.replace("$", "").replace(",", "").split("-")
            if len(parts) != 2:
                raise ValueError
            budget_min_value = float(parts[0].strip())
            budget_max_value = float(parts[1].strip())
            if budget_min_value <= 0 or budget_max_value <= 0:
                raise ValueError
            if budget_min_value >= budget_max_value:
                try:
                    await interaction.response.send_message(
                        embed=error_embed(
                            message="Maximum budget must exceed minimum budget."
                        ),
                        ephemeral=not (interaction.guild is None),
                    )
                except discord.errors.NotFound:
                    pass
                return
        except ValueError:
            try:
                await interaction.response.send_message(
                    embed=error_embed(
                        message="Invalid budget range. Use format: min-max (e.g. 500-5000)"
                    ),
                    ephemeral=not (interaction.guild is None),
                )
            except discord.errors.NotFound:
                pass
            return

        # ── Validate deadline if provided ──────────────────────────
        if deadline_text:
            from datetime import datetime
            import re

            date_pattern = re.compile(r"\d{4}-\d{2}-\d{2}")
            if date_pattern.match(deadline_text):
                try:
                    datetime.strptime(deadline_text, "%Y-%m-%d")
                except ValueError:
                    try:
                        await interaction.response.send_message(
                            embed=error_embed(
                                message="Invalid date format. Use YYYY-MM-DD."
                            ),
                            ephemeral=not (interaction.guild is None),
                        )
                    except discord.errors.NotFound:
                        pass
                    return
            else:
                try:
                    days = int(deadline_text)
                    if days < 1:
                        raise ValueError
                except ValueError:
                    try:
                        await interaction.response.send_message(
                            embed=error_embed(
                                message="Invalid deadline. Enter a date (YYYY-MM-DD) or a positive number of days."
                            ),
                            ephemeral=not (interaction.guild is None),
                        )
                    except discord.errors.NotFound:
                        pass
                    return

        # ── Store values in setup view ─────────────────────────────
        self.setup_view.last_title = title_text
        self.setup_view.last_description = desc_text
        self.setup_view.last_skills = skills_text
        self.setup_view.last_budget_min = budget_min_value
        self.setup_view.last_budget_max = budget_max_value
        self.setup_view.deadline = deadline_text

        # ── Defer and post the job ─────────────────────────────────
        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            return

        try:
            url = f"{BACKEND_URL}jobs/bot/post/"
            packet = BotPacketFactory.create_packet(
                packet_type="job_post",
                data={
                    "discord_id": interaction.user.id,
                    "guild_id": str(interaction.guild_id),
                    "guild_name": (
                        str(interaction.guild.name)
                        if interaction.guild
                        else "Direct Message"
                    ),
                    "title": title_text,
                    "description": desc_text,
                    "skills": skills_text,
                    "budget_min": budget_min_value,
                    "budget_max": budget_max_value,
                    "category": self.setup_view.category_label,
                    "experience": self.setup_view.experience_label,
                    "featured": self.setup_view.featured,
                    "deadline": deadline_text or None,
                },
                provider="bot",
            )
            headers = {"X-Webhook-Token": WEBHOOK_SECRET}

            session = get_http_session()
            async with session.post(
                url, json=packet.to_dict(), headers=headers
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    embed = success_embed(
                        title="Job Posted Successfully",
                        message=(
                            "Your job listing has been posted!\n\n"
                            f"**Job ID**: `{data.get('job_id', '')}`"
                        ),
                    )
                    await interaction.edit_original_response(embed=embed, view=None)
                    self.setup_view.stop()
                else:
                    try:
                        err = await resp.json()
                        msg = err.get("error", "Failed to post job.")
                    except Exception:
                        msg = "Failed to post job."
                    await interaction.edit_original_response(
                        embed=error_embed(message=msg), view=None
                    )
        except Exception as e:
            logger.exception(f"Error posting job: {e}")
            await interaction.edit_original_response(
                embed=error_embed(message="The service is temporarily unavailable."),
                view=None,
            )


# =====================================================================
#  Setup View – Dropdowns above, buttons below
# =====================================================================


class JobPostSetupView(discord.ui.View):
    """Initial view: category dropdown + experience dropdown + proceed / cancel.

    Layout:
        Row 0 – Category dropdown
        Row 1 – Experience dropdown
        Row 2 – [Featured toggle]  Proceed  Cancel
    """

    def __init__(self, is_premium: bool):
        super().__init__(timeout=300)
        self.author_id: int | None = None
        self._done = False
        self.category_label: str = ""
        self.experience_label: str = ""
        self.last_title = ""
        self.last_description = ""
        self.last_skills = ""
        self.last_budget_min = 0.0
        self.last_budget_max = 0.0
        self.deadline: str = ""
        self.featured = False
        self.is_premium = is_premium

        # Row 0 – Category dropdown
        self.add_item(JobCategorySelect())
        # Row 1 – Experience dropdown
        self.add_item(ExperienceLevelSelect())

        # Row 2 – Buttons (left-to-right: toggle, proceed, cancel)
        if is_premium:
            toggle = discord.ui.Button(
                label="Featured: OFF",
                style=discord.ButtonStyle.primary,
                row=2,
            )
            toggle.callback = self._on_toggle_featured
            self.add_item(toggle)

        proceed = discord.ui.Button(
            label="Proceed",
            style=discord.ButtonStyle.success,
            row=2,
        )
        proceed.callback = self._on_proceed
        self.add_item(proceed)

        cancel = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    # ------------------------------------------------------------------

    async def on_timeout(self) -> None:
        self.stop()

    # ------------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._done:
            try:
                await interaction.response.edit_message(view=None)
            except discord.errors.NotFound:
                pass
            return False
        return True

    # ------------------------------------------------------------------

    async def _on_toggle_featured(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        self.featured = not self.featured
        # Update the button label in-place
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label.startswith(
                "Featured"
            ):
                child.label = (
                    "Featured: ON" if self.featured else "Featured: OFF"
                )
                break
        await interaction.response.edit_message(view=self)

    # ------------------------------------------------------------------

    async def _on_proceed(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        if self._done:
            return
        if not self.category_label:
            embed = error_embed(message="Please select a job category first.")
            await interaction.response.edit_message(embed=embed, view=self)
            return
        if not self.experience_label:
            embed = error_embed(
                message="Please select an experience level first."
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return
        self._done = True
        modal = JobPostDetailsModal(self)
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------

    async def _on_cancel(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self._done = True
        self.stop()
        embed = info_embed(message="Job posting cancelled.")
        await interaction.response.edit_message(embed=embed, view=None)


# =====================================================================
#  Cog
# =====================================================================


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
                data={"preflight": True},
                provider="bot",
            )
            packet.data["discord_id"] = interaction.user.id
            headers = {"X-Webhook-Token": WEBHOOK_SECRET}

            session = get_http_session()
            async with session.post(
                url, json=packet.to_dict(), headers=headers
            ) as resp:
                res_data = await resp.json()
                if resp.status in (200, 201):
                    is_premium = res_data.get("is_premium", False)
                    embed = create_embed(
                        title="Post a Job",
                        description=(
                            "> **Configure**, Use the dropdown menus below to select your job category and experience level.\n"
                            "> **Proceed**, Click the proceed button to fill in the title, description, skills, and budget range."
                        ),
                        color=BrandColor.PRIMARY,
                        footer="Xentra • Jobs",
                    )
                    view = JobPostSetupView(is_premium)
                    view.author_id = interaction.user.id
                    return embed, view
                else:
                    return error_embed(
                        message=res_data.get(
                            "error", "You are not eligible to post jobs."
                        )
                    )

        await validate_and_respond(interaction, post_job_callback)


async def setup(bot):
    await bot.add_cog(PostJob(bot))
