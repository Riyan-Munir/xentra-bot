"""
``/active rooms``, Display a paginated list of active rooms.

Flow:
  1. Dropdown to select Interview Room or Job Room.
  2. Interview → fetch active (open) interview rooms → paginated embed.
  3. Job → fetch active job rooms (open / freezed / disputed) → paginated embed.
"""

import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import create_embed, BrandColor, error_embed, info_embed
from utils.pagination import PaginationView

logger = logging.getLogger('bot.rooms.active_rooms')


# ──────────────────────────────────────────────────────────────────────
# Room-type selector (reused across all three new commands)
# ──────────────────────────────────────────────────────────────────────
class RoomTypeSelect(discord.ui.Select):
    """Dropdown: Interview Room or Job Room."""

    def __init__(self) -> None:
        self._all_options = [
            discord.SelectOption(
                label="Interview Room",
                value="interview",
                description="View interview rooms",
            ),
            discord.SelectOption(
                label="Job Room",
                value="job",
                description="View job discussion rooms",
            ),
        ]
        super().__init__(
            placeholder="Select room type",
            min_values=1,
            max_values=1,
            options=self._all_options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: "ActiveRoomsSetupView" = self.view
        view.room_type = self.values[0]
        selected_label = next(
            (opt.label for opt in self._all_options if opt.value == self.values[0]),
            self.values[0],
        )
        self.placeholder = f"✓ {selected_label}"
        await interaction.response.defer()


class ActiveRoomsSetupView(discord.ui.View):
    """Initial view: room-type dropdown + Proceed / Cancel."""

    def __init__(self, user_data: dict) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False
        self.user_data = user_data
        self.room_type: str = "interview"  # default

        self.add_item(RoomTypeSelect())

        submit = discord.ui.Button(label="Proceed", style=discord.ButtonStyle.success)
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
            embed=info_embed(
                message=(
                    "> ***Room listing has been cancelled.***\n"
                    "> __Nothing was changed. You can run /active rooms again anytime.__"
                )
            ),
            view=None,
        )

    async def _on_submit(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self._done = True
        is_dm = interaction.guild is None

        await interaction.response.edit_message(view=None)

        # Fetch active rooms (interview = open, job = open/freezed/disputed)
        url = f"{BACKEND_URL}rooms/bot/active-rooms/"
        params = {
            "discord_id": str(interaction.user.id),
            "room_type": self.room_type,
            "page": 1,
        }
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        label = "interview" if self.room_type == "interview" else "job"

        try:
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    total_count = data["count"]
                    rooms_list = data["results"]

                    if total_count == 0:
                        embed = error_embed(
                            message=f"Could not find any active {label} room."
                        )
                        await interaction.edit_original_response(embed=embed, view=None)
                        return

                    view = ActiveRoomsPaginationView(
                        rooms_list,
                        current_page=1,
                        total_count=total_count,
                        user_data=self.user_data,
                        room_type=self.room_type,
                    )
                    view.author_id = interaction.user.id
                    embed = view.build_embed()
                    view.update_buttons(embed)
                    await interaction.edit_original_response(
                        content=None,
                        embed=embed,
                        view=view,
                    )
                else:
                    err_data = await resp.json()
                    await interaction.edit_original_response(
                        embed=error_embed(
                            message=err_data.get("error", "Could not load active rooms.")
                        ),
                        view=None,
                    )
        except Exception as e:
            logger.error(f"Error fetching active rooms: {e}")
            await interaction.edit_original_response(
                embed=error_embed(
                    message="The service is temporarily unavailable."
                ),
                view=None,
            )


# ──────────────────────────────────────────────────────────────────────
# Paginated list view for active rooms
# ──────────────────────────────────────────────────────────────────────
class ActiveRoomsPaginationView(PaginationView):
    """Paginated display of active interview or job rooms."""

    def __init__(
        self,
        rooms_data: list,
        current_page: int,
        total_count: int,
        user_data: dict,
        room_type: str = "interview",
    ) -> None:
        total_pages = (total_count + 4) // 5  # ceil division
        super().__init__(
            current_page=current_page,
            total_pages=total_pages,
            user_data=user_data,
        )
        self.rooms = rooms_data
        self.total_count = total_count
        self.room_type = room_type

    async def change_page(self, interaction: discord.Interaction, new_page: int) -> None:
        is_dm = interaction.guild is None

        url = f"{BACKEND_URL}rooms/bot/active-rooms/"
        params = {
            "discord_id": str(interaction.user.id),
            "room_type": self.room_type,
            "page": new_page,
        }
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        try:
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.rooms = data["results"]
                    self.current_page = new_page
                    await self.update_message(interaction)
                else:
                    await interaction.response.edit_message(
                        embed=error_embed(message="The service is temporarily unavailable."),
                    )
        except Exception as e:
            logger.error(f"Error fetching active rooms page: {e}")
            await interaction.response.edit_message(
                embed=error_embed(message="The service is temporarily unavailable."),
            )

    def build_embed(self) -> discord.Embed:
        is_job = self.room_type == "job"
        room_label = "Job" if is_job else "Interview"
        title = f"Active {room_label} Rooms"
        embed = create_embed(
            title=title,
            description=(
                f"> ***Active {room_label} Rooms** — page `{self.current_page}` of `{self.total_pages}`*\n"
                f"**Total:** `{self.total_count}`\n"
                "\n"
                "> __Use the arrows to browse pages, and the buttons below to act on a room.__"
            ),
            color=BrandColor.PRIMARY,
            footer="Xentra • Rooms",
        )

        if not self.rooms:
            embed.description = (
                f"> ***Could not find any active {room_label.lower()} rooms.***\n"
                "\n"
                f"> __Use /create room to start a new {room_label.lower()}, and it will appear here.__"
            )
            return embed

        for room in self.rooms:
            room_id = room["room_id"]
            job_title = room["job_title"]
            client_name = room.get("client_name", "Unknown")
            freelancer_name = room.get("freelancer_name", "Unknown")
            status = room.get("status", "")
            last_activity = room.get("last_activity", "")

            # Truncate ISO timestamp to readable format
            activity_short = last_activity[:19].replace("T", " ") if last_activity else "N/A"

            details = (
                f"> **Room**: `{room_id}`\n"
                f"> **Job**: `{job_title}`\n"
                f"> **Client**: **{client_name}**\n"
                f"> **Freelancer**: **{freelancer_name}**\n"
                f"> **Status**: `{status}`\n"
                f"> **Last Activity**: `{activity_short}`"
            )

            embed.add_field(
                name=f"Room, {room_id}",
                value=details,
                inline=False,
            )

        return embed


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────
class ActiveRooms(commands.Cog):
    """``/active rooms``, Browse your active interview or job rooms."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(name="active_rooms", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def active_rooms(self, interaction: discord.Interaction) -> None:
        async def callback(user_data: dict) -> tuple:
            embed = create_embed(
                title="Active Rooms",
                description=(
                    "> ***Select the type of room to view.***\n"
                    "**Step:** `1 of 2`\n"
                    "`1.` Interview Room — show all your open interview rooms.\n"
                    "`2.` Job Room — show your active job rooms (open / freezed / disputed).\n"
                    "\n"
                    "> __Choose a room type from the dropdown, then click Proceed.__"
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Rooms",
            )
            view = ActiveRoomsSetupView(user_data)
            view.author_id = interaction.user.id
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot) -> None:
    await bot.add_cog(ActiveRooms(bot))
