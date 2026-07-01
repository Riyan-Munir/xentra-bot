"""
``/selected room`` — Display the currently selected room for chat.

Flow:
  1. Dropdown to select Interview Room or Job Room.
  2. Interview → fetch selected room ID from backend model → show room details.
  3. Job → placeholder "not implemented yet" message.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from config import WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import create_embed, BrandColor, error_embed, info_embed

logger = logging.getLogger('bot.rooms.selected_room')


# ──────────────────────────────────────────────────────────────────────
# Room-type selector (reuses the same pattern)
# ──────────────────────────────────────────────────────────────────────
class SelectedRoomTypeSelect(discord.ui.Select):
    """Dropdown: Interview Room or Job Room."""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="Interview Room",
                value="interview",
                description="View selected interview room",
            ),
            discord.SelectOption(
                label="Job Room",
                value="job",
                description="View selected job discussion room",
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
        view: "SelectedRoomSetupView" = self.view
        view.room_type = self.values[0]
        await interaction.response.defer()


class SelectedRoomSetupView(discord.ui.View):
    """Initial view: room-type dropdown + Submit / Cancel."""

    def __init__(self, user_data: dict) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.user_data = user_data
        self.room_type: str = "interview"

        self.add_item(SelectedRoomTypeSelect())

        submit = discord.ui.Button(label="Submit", style=discord.ButtonStyle.primary)
        submit.callback = self._on_submit
        self.add_item(submit)

        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message="Room selection cancelled."),
            view=None,
        )

    async def _on_submit(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        is_dm = interaction.guild is None

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        if self.room_type == "job":
            embed = info_embed(
                message="**Job Rooms — Coming Soon**\n\n"
                "Job rooms are not implemented yet. "
                "This feature will be available in a future update."
            )
            await interaction.edit_original_response(embed=embed, view=None)
            return

        # Interview flow — use shared resolver
        from utils.command_handler import fetch_selected_room

        headers = {"X-Webhook-Token": WEBHOOK_SECRET}
        room = await fetch_selected_room(
            discord_id=interaction.user.id,
            role=self.user_data.get("active_role", "client"),
            room_type="interview",
            headers=headers,
        )

        if room is not None:
            embed = create_embed(
                title="Selected Room for Messages",
                description=(
                    f"This is the interview room currently selected "
                    f"as your active room."
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Room System",
            )

            # Format the last activity timestamp
            last_activity = room.get("last_activity", "")
            activity_str = (
                last_activity[:19].replace("T", " ")
                if last_activity else "N/A"
            )

            room_id = room.get("room_id", "Unknown")
            job_title = room.get("job_title", "Unknown")
            client_name = room.get("client_name", "Unknown")
            freelancer_name = room.get("freelancer_name", "Unknown")
            status = room.get("status", "Unknown")

            details = (
                f"> **Room ID**: `{room_id}`\n"
                f"> **Job Title**: `{job_title}`\n"
                f"> **Client**: **{client_name}**\n"
                f"> **Freelancer**: **{freelancer_name}**\n"
                f"> **Status**: `{status}`\n"
                f"> **Last Activity**: `{activity_str}`"
            )

            embed.add_field(
                name="Room Details",
                value=details,
                inline=False,
            )

            await interaction.edit_original_response(
                embed=embed,
                view=None,
            )
        else:
            await interaction.edit_original_response(
                embed=error_embed(
                    message="No selected interview room found. "
                    "Use `\\switch_room` to select one.",
                ),
                view=None,
            )


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────
class SelectedRoom(commands.Cog):
    """``/selected room`` — View your currently selected room."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(name="selected_room", description="Display selected room for chat.")
    @app_commands.checks.cooldown(3, 10, key=lambda i: i.user.id)
    async def selected_room(self, interaction: discord.Interaction) -> None:
        async def callback(user_data: dict) -> tuple:
            embed = create_embed(
                title="Selected Room",
                description=(
                    "**Select a room type** to view your selected room.\n"
                    "• **Interview Room** — Show your selected interview room.\n"
                    "• **Job Room** — Not yet implemented.\n\n"
                    "Press **Submit** to continue or **Cancel** to abort."
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Room System",
            )
            view = SelectedRoomSetupView(user_data)
            view.author_id = interaction.user.id
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot) -> None:
    await bot.add_cog(SelectedRoom(bot))
