"""
Shared components for Rooms commands.

Provides the canonical :class:`RoomTypeSelect` dropdown used by all
``/active rooms``, ``/selected room``, ``/switch room`` and ``/create room``
commands so that every room-type picker looks and behaves identically.

Usage
-----
    from ._shared import RoomTypeSelect

    class MyView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__()
            self.room_type: str | None = None
            self.add_item(RoomTypeSelect())
"""

import discord


class RoomTypeSelect(discord.ui.Select):
    """Canonical room-type dropdown for every Rooms cog.

    Expects ``self.view.room_type`` (a ``str | None``) to exist on
    the parent view.  When the user makes a selection the value is
    stored there and the interaction is deferred.
    """

    def __init__(self, placeholder: str = "Select room type") -> None:
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
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.room_type = self.values[0]  # type: ignore[union-attr]
        await interaction.response.defer()
