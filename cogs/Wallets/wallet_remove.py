import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import (
    BrandColor, create_embed, error_embed, success_embed, info_embed, loading_embed,
)

logger = logging.getLogger('bot.wallets.remove')


class RemovableWalletSelect(discord.ui.Select):
    """Dropdown of non-default wallets to remove."""

    def __init__(self, wallets: list[dict]):
        options = []
        for w in wallets:
            addr = w['address']
            short = f"{addr[:6]}...{addr[-4:]}"
            label_text = w.get('label', '') or short
            desc = f"{w['id']} | {short}"
            options.append(
                discord.SelectOption(label=label_text[:100], value=w['id'], description=desc[:100])
            )
        super().__init__(
            placeholder="Select a wallet to remove",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: WalletRemoveView = self.view  # type: ignore
        wallet_id = self.values[0]
        wallet_type = view.wallet_type
        selected = next((w for w in view.wallets if w['id'] == wallet_id), None)

        # Show confirmation view
        confirm_view = WalletRemoveConfirmView(
            wallet_id=wallet_id,
            wallet_type=wallet_type,
            wallet_label=selected.get('label', '') or f"{selected['address'][:8]}...{selected['address'][-4:]}",
        )
        confirm_view.author_id = interaction.user.id

        embed = create_embed(
            title="Confirm Wallet Removal",
            description=(
                f"Are you sure you want to remove wallet **{wallet_id}**?\n\n"
                f"This action **cannot be undone**. The wallet will be disabled "
                f"and removed from your account.\n\n"
                f"*Note: Only non-default wallets can be removed.*"
            ),
            color=BrandColor.WARNING,
            footer="Xentra • Wallet Removal",
        )

        await interaction.response.edit_message(embed=embed, view=confirm_view)


class WalletRemoveConfirmView(discord.ui.View):
    """Confirmation view with Proceed and Cancel buttons."""

    def __init__(self, wallet_id: str, wallet_type: str, wallet_label: str) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False
        self.wallet_id = wallet_id
        self.wallet_type = wallet_type
        self.wallet_label = wallet_label

    async def on_timeout(self) -> None:
        self.stop()

    async def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Proceed", style=discord.ButtonStyle.danger)
    async def proceed(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self._done = True

        await interaction.response.defer()
        await self._disable_all()

        await interaction.edit_original_response(
            embed=loading_embed(description="Removing wallet..."),
            view=self,
        )

        url = f"{BACKEND_URL}wallets/bot/disable/"
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        payload = {
            'discord_id': str(interaction.user.id),
            'wallet_id': self.wallet_id,
        }

        session = get_http_session()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 200:
                    msg = f"Wallet **{self.wallet_id}** has been **removed**."
                    await interaction.edit_original_response(
                        embed=success_embed(message=msg),
                        view=None,
                    )
                else:
                    err_msg = body.get('error', 'Could not remove wallet.')
                    await interaction.edit_original_response(
                        embed=error_embed(message=err_msg),
                        view=None,
                    )
        except Exception:
            logger.exception("Remove wallet failed")
            await interaction.edit_original_response(
                embed=error_embed(message="An unexpected error occurred."),
                view=None,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Wallet removal cancelled.'),
            view=None,
        )


class WalletRemoveView(discord.ui.View):
    """View with dropdown of removable (non-default) wallets."""

    def __init__(self, wallets: list[dict], wallet_type: str) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.wallet_type = wallet_type
        self.wallets = wallets
        self.add_item(RemovableWalletSelect(wallets))

    async def on_timeout(self) -> None:
        self.stop()


class WalletRemoveCommand(commands.Cog):
    """``/wallet remove``, Remove a registered wallet (default wallet cannot be removed)."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_remove", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_remove(self, interaction: discord.Interaction) -> None:

        async def callback(user_data):
            active_role = user_data.get('active_role')
            if active_role not in ('freelancer', 'client'):
                return error_embed(
                    message='Active role must be `freelancer` or `client` to manage wallets.'
                )
            wallet_type = active_role

            url = f"{BACKEND_URL}wallets/bot/list/"
            params = {'discord_id': interaction.user.id}
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            session = get_http_session()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        wallets = data.get('wallets', [])

                        # Default wallet cannot be removed (enforced by backend)
                        removable = [w for w in wallets if not w.get('is_default')]

                        if not removable:
                            if wallets and all(w.get('is_default') for w in wallets):
                                msg = 'The **default** wallet cannot be removed. Register another wallet, set it as default, then remove this one.'
                            else:
                                msg = 'No registered wallets found. Use `/wallet register` to add one.'
                            return error_embed(message=msg)

                        embed = create_embed(
                            title="Remove a Wallet",
                            description=(
                                f"**{len(removable)}** non-default wallet(s) available. "
                                f"Select one from the dropdown below to remove it.\n\n"
                                f"The default wallet cannot be removed."
                            ),
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Wallet Removal",
                        )

                        view = WalletRemoveView(removable, wallet_type)
                        view.author_id = interaction.user.id
                        return embed, view
                    else:
                        err_data = await resp.json()
                        return error_embed(
                            message=err_data.get('error', 'Failed to load wallets.')
                        )
            except Exception:
                logger.exception("Failed to fetch wallets")
                return error_embed(
                    message='An unexpected error occurred.'
                )

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(WalletRemoveCommand(bot))
