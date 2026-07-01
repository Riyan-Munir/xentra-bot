"""
``/switch room`` — Switch your selected room for chat.

Flow:
  1. Dropdown to select Interview Room or Job Room.
  2. Interview → fetch active rooms → show a selection dropdown.
  3. User picks a room → POST to backend to update selected room model.
  4. Job → placeholder "not implemented yet" message.
"""

import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import create_embed, BrandColor, error_embed, info_embed, success_embed

logger = logging.getLogger('bot.rooms.switch_room')


# ──────────────────────────────────────────────────────────────────────
# Step 1: Room-type selector
# ──────────────────────────────────────────────────────────────────────
class SwitchRoomTypeSelect(discord.ui.Select):
    """Dropdown: Interview Room or Job Room."""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="Interview Room",
                value="interview",
                description="Switch your selected interview room",
            ),
            discord.SelectOption(
                label="Job Room",
                value="job",
                description="Switch your selected job discussion room",
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
        view: "SwitchRoomSetupView" = self.view
        view.room_type = self.values[0]
        await interaction.response.defer()


class SwitchRoomSetupView(discord.ui.View):
    """Step 1: room-type dropdown + Submit / Cancel."""

    def __init__(self, user_data: dict) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.user_data = user_data
        self.room_type: str = "interview"

        self.add_item(SwitchRoomTypeSelect())

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
            embed=info_embed(message="Room switch cancelled."),
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

        # Interview flow: fetch active rooms to populate selection dropdown
        url = f"{BACKEND_URL}rooms/bot/active-rooms/"
        params = {
            "discord_id": str(interaction.user.id),
            "role": self.user_data.get("active_role", "client"),
            "page_size": 100,  # fetch a large batch for the dropdown
        }
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        try:
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rooms_list = data.get("results", [])

                    if not rooms_list:
                        await interaction.edit_original_response(
                            embed=error_embed(
                                message="No active interview rooms found. "
                                "Use `\\create_room` to create one first."
                            ),
                            view=None,
                        )
                        return

                    # Present the room picker view
                    picker_view = RoomPickerView(
                        rooms_list,
                        self.user_data,
                        interaction.user.id,
                    )
                    picker_view.author_id = interaction.user.id
                    embed = create_embed(
                        title="Switch Selected Room",
                        description=(
                            "**Select an active interview room** from the dropdown below "
                            "to set it as your selected room for messages."
                        ),
                        color=BrandColor.PRIMARY,
                        footer="Xentra • Room System",
                    )
                    await interaction.edit_original_response(
                        embed=embed,
                        view=picker_view,
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
            logger.error(f"Error fetching active rooms for switch: {e}")
            await interaction.edit_original_response(
                embed=error_embed(
                    message="Something went wrong. Please try again."
                ),
                view=None,
            )


# ──────────────────────────────────────────────────────────────────────
# Step 2: Room picker — dropdown of active rooms + Confirm / Cancel
# ──────────────────────────────────────────────────────────────────────
class ActiveRoomSelect(discord.ui.Select):
    """Dropdown listing active rooms by room_id + job_title."""

    def __init__(self, rooms: list) -> None:
        options = []
        for room in rooms[:25]:  # Discord max 25 options per dropdown
            room_id = room.get("room_id", "???")
            job_title = room.get("job_title", "Unknown")
            label = f"{room_id} — {job_title[:50]}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=room_id,
                    description=f"Job: {job_title[:50]}",
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No rooms available",
                    value="none",
                    default=True,
                )
            )

        super().__init__(
            placeholder="Choose a room to switch to...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: "RoomPickerView" = self.view
        view.selected_room_id = self.values[0]
        await interaction.response.defer()


class RoomPickerView(discord.ui.View):
    """Step 2: room selection dropdown + Confirm / Cancel."""

    def __init__(
        self,
        rooms: list,
        user_data: dict,
        discord_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.rooms = rooms
        self.user_data = user_data
        self.discord_id = discord_id
        self.selected_room_id: str = ""

        # If there are rooms, pre-select the first one
        if rooms:
            self.selected_room_id = rooms[0].get("room_id", "")

        self.add_item(ActiveRoomSelect(rooms))

        confirm = discord.ui.Button(
            label="Confirm Switch",
            style=discord.ButtonStyle.success,
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def update_buttons(self) -> None:
        pass  # No pagination needed here

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message="Room switch cancelled."),
            view=None,
        )

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        is_dm = interaction.guild is None

        if not self.selected_room_id or self.selected_room_id == "none":
            await interaction.response.edit_message(
                embed=error_embed(message="Please select a valid room first."),
            )
            return

        # Disable all controls and defer (no loading embed shown)
        for item in self.children:
            item.disabled = True
        await interaction.response.defer()

        # POST to backend to switch room
        url = f"{BACKEND_URL}rooms/bot/switch-room/"
        payload = {
            "discord_id": str(self.discord_id),
            "role": self.user_data.get("active_role", "client"),
            "room_type": "interview",
            "room_id": self.selected_room_id,
        }
        headers = {"X-Webhook-Token": WEBHOOK_SECRET}

        try:
            session = get_http_session()
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    embed = success_embed(
                        message=f"Selected room switched to **`{self.selected_room_id}`**."
                    )
                    await interaction.edit_original_response(
                        embed=embed,
                        view=None,
                    )
                else:
                    err_data = await resp.json()
                    await interaction.edit_original_response(
                        embed=error_embed(
                            message=err_data.get("error", "Could not switch room.")
                        ),
                        view=None,
                    )
        except Exception as e:
            logger.error(f"Error switching room: {e}")
            await interaction.edit_original_response(
                embed=error_embed(
                    message="Something went wrong. Please try again."
                ),
                view=None,
            )


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────
class SwitchRoom(commands.Cog):
    """``/switch room`` — Change your selected room for messages."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(name="switch_room", description="Switch selected room for chat.")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def switch_room(self, interaction: discord.Interaction) -> None:
        async def callback(user_data: dict) -> tuple:
            embed = create_embed(
                title="Switch Room",
                description=(
                    "**Select a room type** to switch your active room.\n"
                    "• **Interview Room** — Pick from your active interview rooms.\n"
                    "• **Job Room** — Not yet implemented.\n\n"
                    "Press **Submit** to continue or **Cancel** to abort."
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Room System",
            )
            view = SwitchRoomSetupView(user_data)
            view.author_id = interaction.user.id
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot) -> None:
    await bot.add_cog(SwitchRoom(bot))
