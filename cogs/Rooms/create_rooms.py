"""
Cog for the ``/create room`` command.

Flow
----
1. Room type selection (Interview Room / Job Room) via dropdown + Submit/Cancel.
2. **Job Room** → "Not available yet" success embed.
3. **Interview Room** multi-step flow:

   a. Quota check (GET /rooms/bot/quota-check/).
   b. If system quota exhausted & extra rooms available → confirmation prompt.
   c. If both exhausted → error.
   d. Fetch client's open jobs with ≥1 pending application.
   e. Job selection dropdown.
   f. Fetch pending applications for selected job.
   g. Application selection dropdown.
   h. DM validation — send greet messages to both parties sequentially.
   i. If both DMs succeed → atomically create room via backend.
   j. If either DM fails → professional error.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.analytics_collector import AnalyticsCollector
from utils.command_handler import sync_cog_commands, validate_and_respond, is_author
from utils.embeds import (
    BrandColor,
    create_embed,
    error_embed,
    success_embed,
    info_embed,
    loading_embed,
    dm_blocked_embed,
)
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger("bot.rooms.create_rooms")


# =====================================================================
#  Room Type Selection
# =====================================================================


class RoomTypeSelect(discord.ui.Select):
    """Dropdown: Interview Room or Job Room."""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="Interview Room",
                value="interview",
                description="Interview a freelancer for a job application",
            ),
            discord.SelectOption(
                label="Job Room",
                value="job",
                description="Complete a job with an assigned freelancer",
            ),
        ]
        super().__init__(
            placeholder="Select room type",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: "CreateRoomSetupView" = self.view  # type: ignore
        view.room_type = self.values[0]
        await interaction.response.defer()


class CreateRoomSetupView(discord.ui.View):
    """Initial view: room-type dropdown + Submit / Cancel."""

    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.author_id: int | None = None
        self.room_type: Optional[str] = None
        self.add_item(RoomTypeSelect())

        submit = discord.ui.Button(
            label="Submit", style=discord.ButtonStyle.primary, row=1
        )
        submit.callback = self._on_submit
        self.add_item(submit)

        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger, row=1
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    # ------------------------------------------------------------------

    async def on_timeout(self) -> None:
        self.stop()

    # ------------------------------------------------------------------

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message="Room creation cancelled."),
            view=None,
        )

    async def _on_submit(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if not self.room_type:
            await interaction.response.send_message(
                embed=error_embed(message="Select a room type first."),
                ephemeral=True,
            )
            return

        # Defer so we have time for backend calls
        await interaction.response.defer()

        # Disable controls so the user can't re-submit
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(
            embed=loading_embed(description="Processing your selection..."),
            view=self,
        )

        if self.room_type == "job":
            await interaction.edit_original_response(
                embed=success_embed(
                    message="Job room creation is not available yet. "
                    "This feature will be released in a future update.",
                ),
            )
            return

        # Interview Room — hand off to the flow coordinator
        cog: "CreateRooms" = interaction.client.get_cog("CreateRooms")  # type: ignore
        await cog.start_interview_flow(interaction)


# =====================================================================
#  Extra-Room Confirmation
# =====================================================================


class ExtraRoomConfirmView(discord.ui.View):
    """Confirm whether the client wants to burn an extra room."""

    def __init__(self, extra_count: int) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.use_extra: bool = False

        confirm = discord.ui.Button(
            label=f"Yes, use extra room ({extra_count} left)",
            style=discord.ButtonStyle.primary,
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.use_extra = True
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.use_extra = False
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message="Room creation cancelled."),
            view=None,
        )

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True


# =====================================================================
#  Job Selection
# =====================================================================


class JobSelectView(discord.ui.View):
    """Let the client pick one of their open jobs with applications."""

    def __init__(self, jobs: List[dict], use_extra: bool) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.jobs = jobs
        self.selected_job_id: Optional[str] = None
        self.selected_job_title: Optional[str] = None
        self.use_extra = use_extra

        options = [
            discord.SelectOption(
                label=j["title"][:100],
                value=j["job_id"],
                description=(
                    f"{j['application_count']} applicant"
                    f"{'s' if j['application_count'] != 1 else ''}"
                ),
            )
            for j in jobs
        ]

        self._job_select = discord.ui.Select(
            placeholder="Select a job",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._job_select.callback = self._on_select
        self.add_item(self._job_select)

        confirm = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.primary, row=1
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger, row=1
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    # ------------------------------------------------------------------

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.selected_job_id = self._job_select.values[0]
        match = next(
            (j for j in self.jobs if j["job_id"] == self.selected_job_id), None
        )
        self.selected_job_title = match["title"] if match else "Unknown"
        await interaction.response.defer()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if not self.selected_job_id:
            await interaction.response.send_message(
                embed=error_embed("Select a job first."),
                ephemeral=True,
            )
            return
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.selected_job_id = None
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(
            embed=info_embed(message="Room creation cancelled."),
            view=None,
        )

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True


# =====================================================================
#  Application Selection
# =====================================================================


class ApplicationSelectView(discord.ui.View):
    """Let the client pick one pending application for the selected job."""

    def __init__(self, applications: List[dict], use_extra: bool) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.applications = applications
        self.selected_app_id: Optional[str] = None
        self.selected_freelancer_id: Optional[str] = None
        self.selected_freelancer_name: Optional[str] = None
        self.selected_client_name: Optional[str] = None
        self.use_extra = use_extra

        options = [
            discord.SelectOption(
                label=(
                    f"{app['freelancer_name'][:50]} — "
                    f"${app['bid_amount']}"
                ),
                value=app["application_id"],
                description=f"Applied {app['created_at'][:10]}",
            )
            for app in applications
        ]

        self._app_select = discord.ui.Select(
            placeholder="Select an application",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._app_select.callback = self._on_select
        self.add_item(self._app_select)

        confirm = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.primary, row=1
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger, row=1
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    # ------------------------------------------------------------------

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.selected_app_id = self._app_select.values[0]
        match = next(
            (
                a
                for a in self.applications
                if a["application_id"] == self.selected_app_id
            ),
            None,
        )
        if match:
            self.selected_freelancer_id = match["freelancer_discord_id"]
            self.selected_freelancer_name = match["freelancer_name"]
            self.selected_client_name = match.get("client_name")
        await interaction.response.defer()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if not self.selected_app_id:
            await interaction.response.send_message(
                embed=error_embed("Select an application first."),
                ephemeral=True,
            )
            return
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.selected_app_id = None
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(
            embed=info_embed(message="Room creation cancelled."),
            view=None,
        )

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True


# =====================================================================
#  Job Selection
# =====================================================================


class JobSelectView(discord.ui.View):
    """Let the client pick one of their open jobs with applications."""

    def __init__(self, jobs: List[dict], use_extra: bool) -> None:
        super().__init__(timeout=120)
        self.jobs = jobs
        self.selected_job_id: Optional[str] = None
        self.selected_job_title: Optional[str] = None
        self.use_extra = use_extra

        options = [
            discord.SelectOption(
                label=j["title"][:100],
                value=j["job_id"],
                description=(
                    f"{j['application_count']} applicant"
                    f"{'s' if j['application_count'] != 1 else ''}"
                ),
            )
            for j in jobs
        ]

        self._job_select = discord.ui.Select(
            placeholder="Select a job",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._job_select.callback = self._on_select
        self.add_item(self._job_select)

        confirm = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.primary, row=1
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger, row=1
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    # ------------------------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected_job_id = self._job_select.values[0]
        match = next(
            (j for j in self.jobs if j["job_id"] == self.selected_job_id), None
        )
        self.selected_job_title = match["title"] if match else "Unknown"
        await interaction.response.defer()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not self.selected_job_id:
            await interaction.response.send_message(
                embed=error_embed("Select a job first."),
                ephemeral=True,
            )
            return
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.selected_job_id = None
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(
            embed=info_embed(message="Room creation cancelled."),
            view=None,
        )

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True


# =====================================================================
#  Application Selection
# =====================================================================


class ApplicationSelectView(discord.ui.View):
    """Let the client pick one pending application for the selected job."""

    def __init__(self, applications: List[dict], use_extra: bool) -> None:
        super().__init__(timeout=120)
        self.applications = applications
        self.selected_app_id: Optional[str] = None
        self.selected_freelancer_id: Optional[str] = None
        self.selected_freelancer_name: Optional[str] = None
        self.selected_client_name: Optional[str] = None
        self.use_extra = use_extra

        options = [
            discord.SelectOption(
                label=(
                    f"{app['freelancer_name'][:50]} — "
                    f"${app['bid_amount']}"
                ),
                value=app["application_id"],
                description=f"Applied {app['created_at'][:10]}",
            )
            for app in applications
        ]

        self._app_select = discord.ui.Select(
            placeholder="Select an application",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._app_select.callback = self._on_select
        self.add_item(self._app_select)

        confirm = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.primary, row=1
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger, row=1
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    # ------------------------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected_app_id = self._app_select.values[0]
        match = next(
            (
                a
                for a in self.applications
                if a["application_id"] == self.selected_app_id
            ),
            None,
        )
        if match:
            self.selected_freelancer_id = match["freelancer_discord_id"]
            self.selected_freelancer_name = match["freelancer_name"]
            self.selected_client_name = match.get("client_name")
        await interaction.response.defer()

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not self.selected_app_id:
            await interaction.response.send_message(
                embed=error_embed("Select an application first."),
                ephemeral=True,
            )
            return
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.selected_app_id = None
        self.stop()
        self._disable_all()
        await interaction.response.edit_message(
            embed=info_embed(message="Room creation cancelled."),
            view=None,
        )

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True


# =====================================================================
#  The Cog
# =====================================================================


class CreateRooms(commands.Cog):
    """``/create room`` — create interview & job rooms."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    # ------------------------------------------------------------------
    #  Slash command
    # ------------------------------------------------------------------

    @app_commands.command(name="create_room", description="...")
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def create_room(self, interaction: discord.Interaction) -> None:

        async def callback(user_data: dict) -> Any:
            embed = create_embed(
                title="Create a Room",
                description=(
                    "> **Interview Room** — Interview a freelancer for a job "
                    "application.\n"
                    "> **Job Room** — Complete a job with an assigned freelancer."
                ),
                color=BrandColor.PRIMARY,
            )
            embed.set_footer(text='Xentra • Room System')
            view = CreateRoomSetupView()
            view.author_id = interaction.user.id
            return embed, view

        await validate_and_respond(interaction, callback)

    # ------------------------------------------------------------------
    #  Interview flow coordinator
    # ------------------------------------------------------------------

    async def start_interview_flow(
        self, interaction: discord.Interaction
    ) -> None:
        """Run the multi-step interview room creation flow."""

        # ── Step 1 — Quota check ──────────────────────────────────

        quota = await self._check_quota(interaction)
        if isinstance(quota, discord.Embed):
            await interaction.edit_original_response(embed=quota)
            return

        can_use_system: bool = quota["can_use_system"]
        can_use_extra: bool = quota["can_use_extra"]
        extra_count: int = quota.get("extra_available", 0)
        use_extra = False

        # ── Step 1b — Extra-room confirmation ─────────────────────
        if can_use_system:
            use_extra = False
        elif can_use_extra:
            confirm_embed = create_embed(
                title="Monthly Limit Reached",
                description=(
                    "You have reached your monthly interview-room limit. "
                    f"You have **{extra_count}** extra room"
                    f"{'s' if extra_count != 1 else ''} remaining.\n\n"
                    "Would you like to use an extra room to proceed?"
                ),
                color=BrandColor.WARNING,
            )
            confirm_view = ExtraRoomConfirmView(extra_count)
            confirm_view.author_id = interaction.user.id
            await interaction.edit_original_response(
                embed=confirm_embed, view=confirm_view
            )
            await confirm_view.wait()

            if not confirm_view.use_extra:
                return  # cancel message already shown by view
            use_extra = True
        else:
            await interaction.edit_original_response(
                embed=error_embed(
                    message="Monthly interview-room limit reached. "
                    "Upgrade your plan to increase room capacity.",
                ),
            )
            return

        # ── Step 2 — Fetch client jobs ────────────────────────────

        jobs = await self._fetch_client_jobs(interaction)
        if isinstance(jobs, discord.Embed):
            await interaction.edit_original_response(embed=jobs)
            return

        if not jobs:
            await interaction.edit_original_response(
                embed=error_embed(
                    message="No open jobs with pending applications found. "
                    "Post a job and wait for applications before creating a room.",
                ),
            )
            return

        # ── Step 3 — Job selection ────────────────────────────────
        job_embed = create_embed(
            title="Select a Job",
            description="Choose a job to create an interview room for.",
            color=BrandColor.PRIMARY,
        )
        job_view = JobSelectView(jobs, use_extra)
        job_view.author_id = interaction.user.id
        await interaction.edit_original_response(embed=job_embed, view=job_view)
        await job_view.wait()

        if not job_view.selected_job_id:
            return  # cancelled

        # ── Step 4 — Fetch applications ───────────────────────────

        applications = await self._fetch_applications(
            interaction, job_view.selected_job_id
        )
        if isinstance(applications, discord.Embed):
            await interaction.edit_original_response(embed=applications)
            return

        if not applications:
            await interaction.edit_original_response(
                embed=error_embed(
                    message="No pending applications found for this job.",
                ),
            )
            return

        # ── Step 5 — Application selection ────────────────────────
        app_embed = create_embed(
            title="Select an Application",
            description="Choose an applicant to interview.",
            color=BrandColor.PRIMARY,
        )
        app_view = ApplicationSelectView(applications, use_extra)
        app_view.author_id = interaction.user.id
        await interaction.edit_original_response(
            embed=app_embed, view=app_view
        )
        await app_view.wait()

        if not app_view.selected_app_id:
            return  # cancelled

        # ── Step 6 — DM validation ────────────────────────────────
        await interaction.edit_original_response(
            embed=loading_embed(description="Sending invitations..."),
            view=None,
        )

        # Try freelancer DM first — if it fails the client gets no
        # premature notification.
        client_display_name = (
            app_view.selected_client_name
            or interaction.user.display_name
        )
        freelancer_dm_ok = await handle_system_message(
            "room_greet_freelancer",
            {
                "discord_id": app_view.selected_freelancer_id,
                "client_name": client_display_name,
                "job_title": job_view.selected_job_title,
            },
            interaction.client,
        )

        if not freelancer_dm_ok:
            await interaction.edit_original_response(
                embed=dm_blocked_embed(
                    attempted_action="the room invitation",
                    receiver_name=app_view.selected_freelancer_name,
                ),
            )
            return

        client_dm_ok = await handle_system_message(
            "room_greet_client",
            {
                "discord_id": str(interaction.user.id),
                "freelancer_name": app_view.selected_freelancer_name,
                "job_title": job_view.selected_job_title,
            },
            interaction.client,
        )

        if not client_dm_ok:
            await interaction.edit_original_response(
                embed=dm_blocked_embed(
                    attempted_action="the room invitation",
                    receiver_name="you",
                ),
            )
            return

        # ── Step 7 — Create room (backend) ────────────────────────
        await interaction.edit_original_response(
            embed=loading_embed(description="Creating interview room..."),
        )

        result = await self._create_room(
            interaction,
            app_view.selected_app_id,
            use_extra,
        )
        if isinstance(result, discord.Embed):
            await interaction.edit_original_response(embed=result)
            return

        # ── Step 8 — Send rules system message ─────────────────────
        rules_freelancer_ok = await handle_system_message(
            "room_rules",
            {
                "discord_id": result['freelancer_discord_id'],
                "room_id": result['room_id'],
            },
            interaction.client,
        )
        rules_client_ok = False
        if rules_freelancer_ok:
            rules_client_ok = await handle_system_message(
                "room_rules",
                {
                    "discord_id": str(interaction.user.id),
                    "room_id": result['room_id'],
                },
                interaction.client,
            )

        if rules_freelancer_ok and rules_client_ok:
            await self._log_system_message(
                room_id=result['room_id'],
                msg_type="rules",
                flags={
                    "freelancer_rules_sent": True,
                    "client_rules_sent": True,
                },
            )
        else:
            # Log failed deliveries for retry
            if not rules_freelancer_ok:
                await log_failed_delivery(
                    room_id=result['room_id'],
                    message_type='system_message',
                    target_discord_id=result['freelancer_discord_id'],
                    msg_name='rules',
                )
            if not rules_client_ok:
                await log_failed_delivery(
                    room_id=result['room_id'],
                    message_type='system_message',
                    target_discord_id=str(interaction.user.id),
                    msg_name='rules',
                )

        # ── Step 9 — Send job details system message ───────────────
        jd_freelancer_ok = await handle_system_message(
            "room_job_details",
            {
                "discord_id": result['freelancer_discord_id'],
                "room_id": result['room_id'],
                "job_title": result['job_title'],
                "job_description": result.get('job_description', ''),
                "budget_min": result.get('budget_min', '—'),
                "budget_max": result.get('budget_max', '—'),
                "deadline": result.get('deadline'),
            },
            interaction.client,
        )
        jd_client_ok = False
        if jd_freelancer_ok:
            jd_client_ok = await handle_system_message(
                "room_job_details",
                {
                    "discord_id": str(interaction.user.id),
                    "room_id": result['room_id'],
                    "job_title": result['job_title'],
                    "job_description": result.get('job_description', ''),
                    "budget_min": result.get('budget_min', '—'),
                    "budget_max": result.get('budget_max', '—'),
                    "deadline": result.get('deadline'),
                },
                interaction.client,
            )

        if jd_freelancer_ok and jd_client_ok:
            await self._log_system_message(
                room_id=result['room_id'],
                msg_type="job_details",
                flags={
                    "freelancer_job_details_sent": True,
                    "client_job_details_sent": True,
                },
            )
        else:
            # Log failed deliveries for retry
            if not jd_freelancer_ok:
                await log_failed_delivery(
                    room_id=result['room_id'],
                    message_type='system_message',
                    target_discord_id=result['freelancer_discord_id'],
                    msg_name='job_details',
                )
            if not jd_client_ok:
                await log_failed_delivery(
                    room_id=result['room_id'],
                    message_type='system_message',
                    target_discord_id=str(interaction.user.id),
                    msg_name='job_details',
                )

        # ── Success & DM-failure notifications ────────────────────
        success_embed_obj = success_embed(
            message="Interview room created successfully.\n\n"
            f"**Room ID:** `{result['room_id']}`\n"
            f"**Freelancer:** **{result['freelancer_name']}**\n"
            f"**Job:** {result['job_title']}",
        )

        # Collect individual DM delivery failures
        failure_embeds = []
        if not rules_freelancer_ok:
            failure_embeds.append(
                dm_blocked_embed(
                    attempted_action="the room rules",
                    receiver_name=result['freelancer_name'],
                ),
            )
        elif not rules_client_ok:
            failure_embeds.append(
                dm_blocked_embed(
                    attempted_action="the room rules",
                    receiver_name="you",
                ),
            )
        if not jd_freelancer_ok:
            failure_embeds.append(
                dm_blocked_embed(
                    attempted_action="the job details",
                    receiver_name=result['freelancer_name'],
                ),
            )
        elif not jd_client_ok:
            failure_embeds.append(
                dm_blocked_embed(
                    attempted_action="the job details",
                    receiver_name="you",
                ),
            )

        if failure_embeds:
            # Show success + all delivery-failure notifications together
            await interaction.edit_original_response(
                embeds=[success_embed_obj] + failure_embeds,
                view=None,
            )
        else:
            await interaction.edit_original_response(
                embed=success_embed_obj,
                view=None,
            )

        # Fire-and-forget analytics — use a guild-less event since this
        # command runs in DMs, so interaction.guild_id is None.
        AnalyticsCollector.log_custom_event({
            'event_type': 'interview_room_created',
            'target_type': 'interview_room',
            'target_id': result['room_id'],
            'actor': {
                'discord_id': str(interaction.user.id),
                'display_name': interaction.user.display_name,
                'profile_id': '',
                'role': '',
                'role_display_name': '',
            },
            'context': {
                'room_id': result['room_id'],
                'job_title': result['job_title'],
                'freelancer_name': result['freelancer_name'],
            },
            'metadata': {},
        })

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _log_system_message(
        room_id: str,
        msg_type: str,
        flags: dict,
    ) -> None:
        """Fire-and-forget log of a system message delivery to the backend."""
        log_packet = BotPacketFactory.create_packet(
            packet_type="log_system_message",
            data={
                "room_id": room_id,
                "msg_type": msg_type,
                "flags": flags,
            },
            provider="bot",
        )
        session = get_http_session()
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with session.post(
                f"{BACKEND_URL}rooms/bot/log-system-message/",
                json=log_packet.to_dict(),
                headers={"X-Webhook-Token": WEBHOOK_SECRET},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Failed to log %s system message: %s",
                        msg_type,
                        await resp.text(),
                    )
        except Exception:
            logger.exception("Failed to log %s system message", msg_type)


    async def _check_quota(
        self, interaction: discord.Interaction
    ) -> Union[dict, discord.Embed]:
        """Check interview-room quota for the calling user."""
        url = f"{BACKEND_URL}rooms/bot/quota-check/"
        params = {
            "discord_id": str(interaction.user.id),
            "room_type": "interview",
        }
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        session = get_http_session()
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data
                return error_embed(
                    message=data.get("error", "Could not verify room quota."),
                )
        except Exception:
            logger.exception("Quota check failed")
            return error_embed(
                message="Could not verify room quota. Please try again later.",
            )

    async def _fetch_client_jobs(
        self, interaction: discord.Interaction
    ) -> Union[List[dict], discord.Embed]:
        """Fetch open jobs with ≥1 pending application for this client."""
        url = f"{BACKEND_URL}rooms/bot/client-jobs/"
        params = {"discord_id": str(interaction.user.id)}
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        session = get_http_session()
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data
                return error_embed(
                    message=data.get("error", "Could not fetch your jobs."),
                )
        except Exception:
            logger.exception("Fetch client jobs failed")
            return error_embed(
                message="Could not fetch your jobs. Please try again later.",
            )

    async def _fetch_applications(
        self, interaction: discord.Interaction, job_id: str
    ) -> Union[List[dict], discord.Embed]:
        """Fetch pending applications for a specific job."""
        url = f"{BACKEND_URL}rooms/bot/job-applications/"
        params = {
            "discord_id": str(interaction.user.id),
            "job_id": job_id,
        }
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        session = get_http_session()
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with session.get(
                url, params=params, headers=headers, timeout=timeout
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data
                return error_embed(
                    message=data.get("error", "Could not fetch applications."),
                )
        except Exception:
            logger.exception("Fetch applications failed")
            return error_embed(
                message="Could not fetch applications. Please try again later.",
            )

    async def _create_room(
        self,
        interaction: discord.Interaction,
        application_id: str,
        use_extra: bool,
    ) -> Union[dict, discord.Embed]:
        """Atomically create the interview room via the backend."""
        url = f"{BACKEND_URL}rooms/bot/create-interview/"
        packet = BotPacketFactory.create_packet(
            packet_type="create_interview_room",
            data={
                "discord_id": str(interaction.user.id),
                "application_id": application_id,
                "use_extra_room": use_extra,
            },
            provider="bot",
        )
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        session = get_http_session()
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with session.post(
                url, json=packet.to_dict(), headers=headers, timeout=timeout
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data
                return error_embed(
                    message=data.get("error", "Could not create interview room."),
                )
        except Exception:
            logger.exception("Create room failed")
            return error_embed(
                message="Could not create interview room. Please try again later.",
            )


# =====================================================================
#  Entry point
# =====================================================================


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CreateRooms(bot))
