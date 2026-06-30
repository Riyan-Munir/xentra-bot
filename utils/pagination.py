"""
Generic pagination base class for Discord UI Views.

Provides reusable prev/next/close button logic that paginated
views across all cogs can subclass. Supports both API-backed
and local-data pagination strategies.
"""

import discord
import logging
from utils.command_handler import add_admin_post_button, is_author
from utils.embeds import error_embed

logger = logging.getLogger('bot.pagination')


class PaginationView(discord.ui.View):
    """Reusable paginated view with Previous / Next / Close buttons.

    ── Subclassing guide ────────────────────────────────────────────
    ★ Override ``build_embed(self) -> discord.Embed``  (required)
    ★ Override ``change_page()``  for API-backed pagination  (optional)
    ★ Override ``update_buttons()``  if extra controls are needed  (optional)

    By default ``change_page()`` calls ``update_message()``, which
    rebuilds the embed and refreshes the buttons — suitable for
    local-data pagination (no extra fetch needed).
    """

    def __init__(
        self,
        current_page: int,
        total_pages: int,
        user_data: dict,
        timeout: int = 180,
    ):
        super().__init__(timeout=timeout)
        self.author_id: int | None = None
        self.current_page = current_page  # 1‑based
        self.total_pages = total_pages
        self.user_data = user_data

    async def on_timeout(self) -> None:
        self.stop()

    # ── Button management ──────────────────────────────────────────

    def update_buttons(self, embed: discord.Embed = None):
        """Rebuild the navigation row.  Subclasses may add extra items.

        Args:
            embed: An optional pre-built embed (avoids double build_embed()
                   when the caller already constructed one).  Defaults to
                   calling ``self.build_embed()`` if omitted.
        """
        self.clear_items()

        if self.total_pages > 1:
            prev = discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.gray,
                disabled=self.current_page <= 1,
            )
            prev.callback = self.prev_page
            self.add_item(prev)

            nxt = discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.gray,
                disabled=self.current_page >= self.total_pages,
            )
            nxt.callback = self.next_page
            self.add_item(nxt)

        close = discord.ui.Button(label="Close", style=discord.ButtonStyle.red)
        close.callback = self.close_view
        self.add_item(close)

        # Server‑admin broadcast button (if applicable)
        if embed is None:
            embed = self.build_embed()
        add_admin_post_button(self, embed, self.user_data)

    # ── Navigation callbacks ───────────────────────────────────────

    async def prev_page(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        await self.change_page(interaction, self.current_page - 1)

    async def next_page(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        await self.change_page(interaction, self.current_page + 1)

    async def change_page(self, interaction: discord.Interaction, new_page: int) -> None:
        """Override this method for API‑backed pagination.

        For local‑data pagination the default implementation simply
        updates the embed and buttons without any external fetch.
        """
        self.current_page = new_page
        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction) -> None:
        """Refresh the embed and button row in place."""
        embed = self.build_embed()
        self.update_buttons(embed)
        await interaction.response.edit_message(embed=embed, view=self)

    async def close_view(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        await interaction.response.edit_message(view=None)

    # ── Embed builder (MUST override) ──────────────────────────────

    def build_embed(self) -> discord.Embed:
        raise NotImplementedError("Subclasses must override build_embed().")
