import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.retry import validation_fail
from utils.embeds import (
    BrandColor, create_embed, error_embed, success_embed, info_embed,
)

logger = logging.getLogger('bot.wallets.default')


class NonDefaultWalletSelect(discord.ui.Select):
    """Dropdown of non-default wallets to set as default."""

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
            placeholder="Select a wallet to set as default",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: WalletDefaultView = self.view  # type: ignore
        view._selected_wallet_id = self.values[0]
        await interaction.response.defer()


class WalletDefaultView(discord.ui.View):
    """View with dropdown of non-default wallets and Proceed/Cancel buttons."""

    def __init__(self, wallets: list[dict], wallet_type: str) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False
        self.wallet_type = wallet_type
        self.wallets = wallets
        self._selected_wallet_id: str | None = None
        self.add_item(NonDefaultWalletSelect(wallets))

        proceed = discord.ui.Button(label='Proceed', style=discord.ButtonStyle.success, row=1)
        proceed.callback = self._on_proceed
        self.add_item(proceed)

        cancel = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.danger, row=1)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Set default wallet cancelled.'),
            view=None,
        )

    async def _on_proceed(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        wallet_id = self._selected_wallet_id
        if not wallet_id:
            for child in self.children:
                if isinstance(child, NonDefaultWalletSelect) and child.values:
                    wallet_id = child.values[0]
                    break
        if not wallet_id:
            await validation_fail(interaction, message='Select a wallet first.')
            return

        self._done = True
        await interaction.response.defer()

        url = f"{BACKEND_URL}wallets/bot/set-default/"
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        payload = {
            'discord_id': str(interaction.user.id),
            'wallet_id': wallet_id,
        }

        session = get_http_session()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 200:
                    w = body.get('wallet', body)
                    new_default_id = w.get('id', wallet_id)
                    msg = f"Wallet **{new_default_id}** is now the **default** wallet."
                    await interaction.edit_original_response(
                        embed=success_embed(message=msg),
                        view=None,
                    )
                else:
                    err_msg = body.get('error', 'Could not set default wallet.')
                    await interaction.edit_original_response(
                        embed=error_embed(message=err_msg),
                        view=None,
                    )
        except Exception:
            logger.exception("Set default wallet failed")
            await interaction.edit_original_response(
                embed=error_embed(message="The service is temporarily unavailable."),
                view=None,
            )


class WalletDefault(commands.Cog):
    """``/wallet default``, Set a different wallet as your default wallet."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_default", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_default(self, interaction: discord.Interaction) -> None:

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

                        # Filter verified, non-default wallets (only verified wallets can be default)
                        available = [w for w in wallets if w.get('is_verified') and not w.get('is_default')]

                        if not available:
                            if not wallets:
                                msg = 'No registered wallets found. Use `/wallet register` to add one.'
                            elif all(w.get('is_default') for w in wallets if w.get('is_verified')):
                                msg = 'The only verified wallet is already the default.'
                            else:
                                msg = 'No available wallets to set as default. Verify unverified wallets with `/wallet verify`.'
                            return error_embed(message=msg)

                        embed = create_embed(
                            title="Set Default Wallet",
                            description=(
                                f"**{len(available)}** non-default wallet(s) available. "
                                f"Select one from the dropdown below to set it as the default."
                            ),
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Wallets",
                        )

                        view = WalletDefaultView(available, wallet_type)
                        view.author_id = interaction.user.id
                        return embed, view
                    else:
                        err_data = await resp.json()
                        return error_embed(
                            message=err_data.get('error', 'Could not load wallets.')
                        )
            except Exception:
                logger.exception("Failed to fetch wallets")
                return error_embed(
                    message='The service is temporarily unavailable.'
                )

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(WalletDefault(bot))
