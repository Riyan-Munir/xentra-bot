"""
``/active rooms`` — Display a paginated list of active (open) interview rooms.

Flow:
  1. Dropdown to select Interview Room or Job Room.
  2. Interview → fetch active rooms from backend → paginated embed.
  3. Job → placeholder "not implemented yet" message.
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
        options = [
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
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: "ActiveRoomsSetupView" = self.view
        view.room_type = self.values[0]
        await interaction.response.defer()


class ActiveRoomsSetupView(discord.ui.View):
    """Initial view: room-type dropdown + Submit / Cancel."""

    def __init__(self, user_data: dict) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.user_data = user_data
        self.room_type: str = "interview"  # default

        self.add_item(RoomTypeSelect())

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
            embed=info_embed(message="Room listing cancelled."),
            view=None,
        )

    async def _on_submit(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        is_dm = interaction.guild is None

        # Disable all UI components
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

        # Interview flow: fetch active rooms
        url = f"{BACKEND_URL}rooms/bot/active-rooms/"
        params = {
            "discord_id": str(interaction.user.id),
            "role": self.user_data.get("active_role", "client"),
            "page": 1,
        }
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        try:
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    total_count = data["count"]
                    rooms_list = data["results"]

                    if total_count == 0:
                        embed = error_embed(
                            message="No active interview rooms found for your account."
                        )
                        await interaction.edit_original_response(embed=embed, view=None)
                        return

                    view = ActiveRoomsPaginationView(
                        rooms_list,
                        current_page=1,
                        total_count=total_count,
                        user_data=self.user_data,
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
                    message="Something went wrong. Please try again."
                ),
                view=None,
            )


# ──────────────────────────────────────────────────────────────────────
# Paginated list view for active rooms
# ──────────────────────────────────────────────────────────────────────
class ActiveRoomsPaginationView(PaginationView):
    """Paginated display of active interview rooms."""

    def __init__(
        self,
        rooms_data: list,
        current_page: int,
        total_count: int,
        user_data: dict,
    ) -> None:
        total_pages = (total_count + 4) // 5  # ceil division
        super().__init__(
            current_page=current_page,
            total_pages=total_pages,
            user_data=user_data,
        )
        self.rooms = rooms_data
        self.total_count = total_count

    async def change_page(self, interaction: discord.Interaction, new_page: int) -> None:
        is_dm = interaction.guild is None

        url = f"{BACKEND_URL}rooms/bot/active-rooms/"
        params = {
            "discord_id": str(interaction.user.id),
            "role": self.user_data.get("active_role", "client"),
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
                        embed=error_embed(message="Could not load this page."),
                    )
        except Exception as e:
            logger.error(f"Error fetching active rooms page: {e}")
            await interaction.response.edit_message(
                embed=error_embed(message="Something went wrong. Please try again."),
            )

    def build_embed(self) -> discord.Embed:
        embed = create_embed(
            title="Active Interview Rooms",
            description=(
                f"Showing open rooms (Page **{self.current_page}**/"
                f"**{self.total_pages}**)"
            ),
            color=BrandColor.PRIMARY,
            footer="Xentra • Room System",
        )

        if not self.rooms:
            embed.description = "No active interview rooms found."
            return embed

        for room in self.rooms:
            room_id = room["room_id"]
            job_title = room["job_title"]
            client_name = room.get("client_name", "Unknown")
            freelancer_name = room.get("freelancer_name", "Unknown")
            last_activity = room.get("last_activity", "")

            # Truncate ISO timestamp to readable format
            activity_short = last_activity[:19].replace("T", " ") if last_activity else "N/A"

            details = (
                f"> **Room**: `{room_id}`\n"
                f"> **Job**: `{job_title}`\n"
                f"> **Client**: **{client_name}**\n"
                f"> **Freelancer**: **{freelancer_name}**\n"
                f"> **Last Activity**: `{activity_short}`"
            )

            embed.add_field(
                name=f"Room — {room_id}",
                value=details,
                inline=False,
            )

        return embed


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────
class ActiveRooms(commands.Cog):
    """``/active rooms`` — Browse your active interview rooms."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(name="active_rooms", description="Display active rooms.")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def active_rooms(self, interaction: discord.Interaction) -> None:
        async def callback(user_data: dict) -> tuple:
            embed = create_embed(
                title="Active Rooms",
                description=(
                    "**Select a room type** to view your active rooms.\n"
                    "• **Interview Room** — Show all open interview rooms.\n"
                    "• **Job Room** — Not yet implemented.\n\n"
                    "Press **Submit** to continue or **Cancel** to abort."
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Room System",
            )
            view = ActiveRoomsSetupView(user_data)
            view.author_id = interaction.user.id
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot) -> None:
    await bot.add_cog(ActiveRooms(bot))
