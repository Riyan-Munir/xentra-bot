"""
``/switch room``, Switch your selected room for chat.

Flow:
  1. Dropdown to select Interview Room or Job Room.
  2. Fetch active rooms (interview: open; job: open/freezed/disputed).
  3. User picks a room → POST to backend to update selected room model.
"""

import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.retry import validation_fail
from utils.embeds import create_embed, BrandColor, error_embed, info_embed, success_embed

logger = logging.getLogger('bot.rooms.switch_room')


# ──────────────────────────────────────────────────────────────────────
# Step 1: Room-type selector
# ──────────────────────────────────────────────────────────────────────
class SwitchRoomTypeSelect(discord.ui.Select):
    """Dropdown: Interview Room or Job Room."""

    def __init__(self) -> None:
        self._all_options = [
            discord.SelectOption(
                label="Interview Room",
                value="interview",
                description="Switch selected interview room",
            ),
            discord.SelectOption(
                label="Job Room",
                value="job",
                description="Switch selected job room",
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
        view: "SwitchRoomSetupView" = self.view
        view.room_type = self.values[0]
        selected_label = next(
            (opt.label for opt in self._all_options if opt.value == self.values[0]),
            self.values[0],
        )
        self.placeholder = f"✓ {selected_label}"
        await interaction.response.defer()


class SwitchRoomSetupView(discord.ui.View):
    """Step 1: room-type dropdown + Proceed / Cancel."""

    def __init__(self, user_data: dict) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False
        self.user_data = user_data
        self.room_type: str = "interview"

        self.add_item(SwitchRoomTypeSelect())

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
                    "> ***Room switch has been cancelled.***\n"
                    "> __Your active room remains unchanged.__"
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

        # Fetch active rooms (interview: open; job: open/freezed/disputed)
        url = f"{BACKEND_URL}rooms/bot/active-rooms/"
        params = {
            "discord_id": str(interaction.user.id),
            "room_type": self.room_type,
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
                                message=f"Could not find any active {self.room_type} room."
                            ),
                            view=None,
                        )
                        return

                    # Present the room picker view
                    picker_view = RoomPickerView(
                        rooms_list,
                        self.user_data,
                        interaction.user.id,
                        self.room_type,
                    )
                    picker_view.author_id = interaction.user.id
                    embed = create_embed(
                        title="Switch Selected Room",
                        description=(
                            "> ***Choose your new active room.***\n"
                            "**Step:** `2 of 2`\n"
                            "\n"
                            "> __Use the dropdown to pick a room, then click Proceed.__"
                        ),
                        color=BrandColor.PRIMARY,
                        footer="Xentra • Rooms",
                    )
                    await interaction.edit_original_response(
                        embed=embed,
                        view=picker_view,
                    )
                else:
                    err_data = await resp.json()
                    await interaction.edit_original_response(
                        embed=error_embed(
                            message=err_data.get("error", "The service is temporarily unavailable.")
                        ),
                        view=None,
                    )
        except Exception as e:
            logger.error(f"Error fetching active rooms for switch: {e}")
            await interaction.edit_original_response(
                embed=error_embed(
                    message="The service is temporarily unavailable."
                ),
                view=None,
            )


# ──────────────────────────────────────────────────────────────────────
# Step 2: Room picker, dropdown of active rooms + Confirm / Cancel
# ──────────────────────────────────────────────────────────────────────
class ActiveRoomSelect(discord.ui.Select):
    """Dropdown listing active rooms by room_id + job_title."""

    def __init__(self, rooms: list) -> None:
        self._all_options = []
        for room in rooms[:25]:  # Discord max 25 options per dropdown
            room_id = room.get("room_id", "???")
            job_title = room.get("job_title", "Unknown")
            label = f"{room_id}, {job_title[:50]}"
            self._all_options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=room_id,
                    description=f"Job: {job_title[:50]}",
                )
            )

        if not self._all_options:
            self._all_options.append(
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
            options=self._all_options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: "RoomPickerView" = self.view
        view.selected_room_id = self.values[0]
        selected_label = next(
            (opt.label for opt in self._all_options if opt.value == self.values[0]),
            self.values[0],
        )
        self.placeholder = f"✓ {selected_label}"
        await interaction.response.defer()


class RoomPickerView(discord.ui.View):
    """Step 2: room selection dropdown + Proceed / ← Back."""

    def __init__(
        self,
        rooms: list,
        user_data: dict,
        discord_id: int,
        room_type: str = "interview",
    ) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False
        self.rooms = rooms
        self.user_data = user_data
        self.discord_id = discord_id
        self.room_type = room_type
        self.selected_room_id: str = ""

        # If there are rooms, pre-select the first one
        if rooms:
            self.selected_room_id = rooms[0].get("room_id", "")

        self.add_item(ActiveRoomSelect(rooms))

        confirm = discord.ui.Button(
            label="Proceed",
            style=discord.ButtonStyle.success,
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(label="\u2190 Back", style=discord.ButtonStyle.secondary)
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
        # Go back to step 1 (room-type selection)
        setup_view = SwitchRoomSetupView(self.user_data)
        setup_view.author_id = interaction.user.id
        embed = create_embed(
            title="Switch Selected Room",
            description=(
                "> Select a room type to switch."
            ),
            color=BrandColor.PRIMARY,
            footer="Xentra • Rooms",
        )
        await interaction.response.edit_message(embed=embed, view=setup_view)

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        is_dm = interaction.guild is None

        if not self.selected_room_id or self.selected_room_id == "none":
            await validation_fail(interaction, message="Select a valid room first.")
            return

        self._done = True
        await interaction.response.defer()

        # POST to backend to switch room
        url = f"{BACKEND_URL}rooms/bot/switch-room/"
        payload = {
            "discord_id": str(self.discord_id),
            "room_type": self.room_type,
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
                    message="The service is temporarily unavailable."
                ),
                view=None,
            )


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────
class SwitchRoom(commands.Cog):
    """``/switch room``, Change your selected room for messages."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(name="switch_room", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def switch_room(self, interaction: discord.Interaction) -> None:
        async def callback(user_data: dict) -> tuple:
            embed = create_embed(
                title="Switch Room",
                description=(
                    "> ***Select the type of room to switch to.***\n"
                    "**Step:** `1 of 2`\n"
                    "`1.` Interview Room — pick from your active interview rooms.\n"
                    "`2.` Job Room — pick from your active job rooms.\n"
                    "\n"
                    "> __Choose a room type from the dropdown, then click Proceed.__"
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Rooms",
            )
            view = SwitchRoomSetupView(user_data)
            view.author_id = interaction.user.id
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot) -> None:
    await bot.add_cog(SwitchRoom(bot))
