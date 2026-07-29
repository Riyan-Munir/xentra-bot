import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import (
    BrandColor, create_embed, error_embed, success_embed, info_embed,
)
from utils.retry import validation_fail, contains_security_threat

logger = logging.getLogger('bot.wallets.register')


class WalletRegisterModal(discord.ui.Modal, title="Register Wallet"):
    """Modal for registering a new wallet address."""

    address = discord.ui.TextInput(
        label="Wallet Address (0x..., 42 chars)",
        placeholder="0x...",
        min_length=42,
        max_length=64,
    )
    label = discord.ui.TextInput(
        label="Wallet Label (Optional, max 64 chars)",
        placeholder="e.g. My Main Wallet",
        required=False,
        max_length=64,
    )

    def __init__(self, *, prefill_address: str = '', prefill_label: str = ''):
        super().__init__(timeout=300)
        if prefill_address:
            self.address.default = prefill_address
        if prefill_label:
            self.label.default = prefill_label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_addr = self.address.value.strip()
        raw_label = self.label.value.strip()

        # Security check first (Rule 27: security before validation)
        if contains_security_threat(raw_addr) or contains_security_threat(raw_label):
            from utils.retry import security_fail
            await security_fail(
                interaction,
                message='Input contains prohibited content. Command terminated.',
                ephemeral=True,
            )
            return

        # Validation (before deferring)
        if not raw_addr or not raw_addr.startswith('0x') or len(raw_addr) != 42:
            await validation_fail(
                interaction,
                message='Address must be a 42-character hex string starting with `0x`.',
                modal_class=WalletRegisterModal,
                modal_kwargs={
                    'prefill_address': raw_addr,
                    'prefill_label': raw_label,
                },
                ephemeral=True,
            )
            return

        # All validation passed → defer
        await interaction.response.defer()

        # API call
        url = f"{BACKEND_URL}wallets/bot/register/"
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        payload = {
            'discord_id': str(interaction.user.id),
            'address': raw_addr,
            'label': raw_label,
        }

        session = get_http_session()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 201:
                    w = body.get('wallet', body)
                    wallet_id = w.get('id', '')
                    msg = (
                        f"Wallet **{wallet_id}** registered! "
                        f"Verify it using `/wallet verify` to activate."
                    )
                    await interaction.edit_original_response(
                        embed=success_embed(message=msg),
                        view=None,
                    )
                else:
                    err_msg = body.get('error', 'Could not register wallet.')
                    await interaction.edit_original_response(
                        embed=error_embed(message=err_msg),
                        view=None,
                    )
        except Exception:
            logger.exception("Wallet register failed")
            await interaction.edit_original_response(
                embed=error_embed(message="The service is temporarily unavailable."),
                view=None,
            )


class WalletRegisterView(discord.ui.View):
    """View with Register Wallet button and Cancel button."""

    def __init__(self) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False

    async def on_timeout(self) -> None:
        self.stop()

    async def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Register Wallet", style=discord.ButtonStyle.primary)
    async def register_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self._done = True
        modal = WalletRegisterModal()
        await interaction.response.send_modal(modal)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Wallet registration cancelled.'),
            view=None,
        )


class WalletRegister(commands.Cog):
    """``/wallet register``, Register a new wallet address."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_register", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_register(self, interaction: discord.Interaction) -> None:

        async def callback(user_data):
            embed = create_embed(
                title="Register a Wallet",
                description=(
                    "Click the button below to register a new wallet address.\n\n"
                    "After registration, you'll need to verify the wallet "
                    "using `/wallet verify` to activate it."
                ),
                color=BrandColor.PRIMARY,
                footer="Xentra • Wallet Registration",
            )
            view = WalletRegisterView()
            view.author_id = interaction.user.id
            return embed, view

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(WalletRegister(bot))
