"""
Shared role selector UI components for premium/custom ID resolution.

Used by user_profile.py and user_stats.py (and potentially other cogs)
to let the user select a target role perspective when a premium ID is
provided.  Extracted to eliminate code duplication.
"""

import discord
from typing import Any, Callable, Coroutine, Optional

from utils.command_handler import is_author
from utils.embeds import info_embed


class ProfileRoleSelect(discord.ui.Select):
    """Dropdown to pick Freelancer or Client when resolving a premium ID."""

    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        options = [
            discord.SelectOption(
                label="Freelancer", value="freelancer",
                emoji="👨‍💻", description="View as Freelancer",
            ),
            discord.SelectOption(
                label="Client", value="client",
                emoji="💼", description="View as Client",
            ),
        ]
        super().__init__(placeholder="Select the target role.", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        self.view.selected_role = self.values[0]  # type: ignore[union-attr]
        # Mark the chosen option as default visually
        for option in self.options:
            option.default = option.value == self.values[0]
        await interaction.response.edit_message(view=self.view)


class ProfileRoleView(discord.ui.View):
    """View containing a role-select dropdown plus Send/Cancel buttons.

    Args:
        identifier: Normalized premium ID string.
        callback_func: Async callable ``(interaction, role, identifier, view)``.
        user_data: The user_data dict from ``validate_and_respond``.
    """

    def __init__(
        self,
        identifier: str,
        callback_func: Callable[..., Coroutine[Any, Any, Any]],
        user_data: dict,
    ) -> None:
        super().__init__(timeout=60)
        self.author_id: int | None = None
        self.user_data = user_data
        self.identifier = identifier
        self.callback_func = callback_func
        self.selected_role: Optional[str] = None
        self.add_item(ProfileRoleSelect(identifier))

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label="Send Request", style=discord.ButtonStyle.green)
    async def send_button(
        self, interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return
        if not self.selected_role:
            await interaction.response.send_message(
                "Please select a role from the dropdown first.", ephemeral=True,
            )
            return
        await self.callback_func(
            interaction, self.selected_role, self.identifier, self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_button(
        self, interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        err = info_embed(message="The profile identity process was dismissed.")
        await interaction.response.edit_message(content=None, embed=err, view=None)
