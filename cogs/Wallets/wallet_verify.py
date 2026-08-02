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

logger = logging.getLogger('bot.wallets.verify')


# ── Signature Modal (opens after challenge is generated) ──────────────


class VerifySignatureModal(discord.ui.Modal, title="Verify Wallet"):
    """Modal where user pastes the signed challenge signature."""

    signature = discord.ui.TextInput(
        label="Signature (0x...)",
        placeholder="Paste your signed signature here",
        min_length=1,
        max_length=512,
        style=discord.TextStyle.long,
    )

    def __init__(self, wallet_id: str, challenge_msg: str):
        super().__init__(timeout=300)
        self.wallet_id = wallet_id
        self.challenge_msg = challenge_msg

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_sig = self.signature.value.strip()

        # Security check first
        if contains_security_threat(raw_sig):
            from utils.retry import security_fail
            await security_fail(
                interaction,
                message='Input contains prohibited content. Command terminated.',
                ephemeral=not (interaction.guild is None),
            )
            return

        if not raw_sig:
            await validation_fail(
                interaction,
                message='Signature cannot be empty.',
                modal_class=VerifySignatureModal,
                modal_kwargs={
                    'wallet_id': self.wallet_id,
                    'challenge_msg': self.challenge_msg,
                },
                ephemeral=not (interaction.guild is None),
            )
            return

        await interaction.response.defer()

        url = f"{BACKEND_URL}wallets/bot/verify/"
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        payload = {
            'discord_id': str(interaction.user.id),
            'wallet_id': self.wallet_id,
            'signature': raw_sig,
        }

        session = get_http_session()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 200:
                    w = body.get('wallet', body)
                    wallet_id = w.get('id', self.wallet_id)
                    is_default = w.get('is_default', False)
                    default_text = ' and set as your **default**' if is_default else ''
                    msg = f"Wallet **{wallet_id}** verified{default_text}!"
                    await interaction.edit_original_response(
                        embed=success_embed(message=msg),
                        view=None,
                    )
                else:
                    err_msg = body.get('error', 'Could not verify wallet.')
                    await interaction.edit_original_response(
                        embed=error_embed(message=err_msg),
                        view=None,
                    )
        except Exception:
            logger.exception("Wallet verify failed")
            await interaction.edit_original_response(
                embed=error_embed(message="The service is temporarily unavailable."),
                view=None,
            )


# ── Wallet Select View (list unverified wallets) ─────────────────────


class UnverifiedWalletSelect(discord.ui.Select):
    """Dropdown listing unverified wallets to verify."""

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
            placeholder="Select a wallet to verify",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: WalletVerifyView = self.view  # type: ignore
        view._selected_wallet_id = self.values[0]
        await interaction.response.defer()


class WalletVerifyView(discord.ui.View):
    """View that lists unverified wallets for selection with Proceed/← Back."""

    def __init__(self, wallets: list[dict], wallet_type: str) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False
        self.wallets = wallets
        self.wallet_type = wallet_type
        self._selected_wallet_id: str | None = None
        self.add_item(UnverifiedWalletSelect(wallets))

        proceed = discord.ui.Button(label='Proceed', style=discord.ButtonStyle.success, row=1)
        proceed.callback = self._on_proceed
        self.add_item(proceed)

        back = discord.ui.Button(label='← Back', style=discord.ButtonStyle.secondary, row=1)
        back.callback = self._on_back
        self.add_item(back)

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Wallet verification cancelled.'),
            view=None,
        )

    async def _on_proceed(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self._done = True
        wallet_id = self._selected_wallet_id or (self.children[0].values[0] if isinstance(self.children[0], UnverifiedWalletSelect) and self.children[0].values else None)
        if not wallet_id:
            await interaction.response.edit_message(
                embed=error_embed(message='Select a wallet first.'), view=None,
            )
            return

        # Find the selected wallet in stored list
        selected = next((w for w in self.wallets if w['id'] == wallet_id), None)
        if not selected:
            await interaction.response.edit_message(
                embed=error_embed(message='Selected wallet not found.'),
                view=None,
            )
            return

        await interaction.response.defer()

        challenge_url = f"{BACKEND_URL}wallets/bot/challenge/"
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        payload = {
            'discord_id': str(interaction.user.id),
            'wallet_id': wallet_id,
        }

        session = get_http_session()
        try:
            async with session.post(challenge_url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status == 200:
                    token = body.get('verification_token', '')
                    addr = selected['address']
                    short_addr = f"`{addr[:8]}...{addr[-4:]}`"

                    # Show challenge embed + signature button
                    embed = create_embed(
                        title="Sign the Challenge",
                        description=(
                            f"To verify wallet **{wallet_id}** ({short_addr}), "
                            f"sign the following message with your wallet:\n\n"
                            f"```\n{token}\n```\n\n"
                            f"After signing, click **Submit Signature** to complete verification."
                        ),
                        color=BrandColor.PRIMARY,
                        footer="Xentra • Wallets",
                    )

                    self.clear_items()
                    submit_btn = discord.ui.Button(
                        label="Submit Signature",
                        style=discord.ButtonStyle.success,
                    )

                    async def submit_btn_cb(inter: discord.Interaction) -> None:
                        if not is_author(inter, self):
                            return
                        modal = VerifySignatureModal(
                            wallet_id=wallet_id,
                            challenge_msg=token,
                        )
                        await inter.response.send_modal(modal)

                    submit_btn.callback = submit_btn_cb
                    self.add_item(submit_btn)

                    cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

                    async def cancel_btn_cb(inter: discord.Interaction) -> None:
                        if not is_author(inter, self):
                            return
                        self.stop()
                        await inter.response.edit_message(
                            embed=info_embed(message='Wallet verification cancelled.'),
                            view=None,
                        )

                    cancel_btn.callback = cancel_btn_cb
                    self.add_item(cancel_btn)

                    await interaction.edit_original_response(embed=embed, view=self)
                else:
                    err_msg = body.get('error', 'Could not generate challenge.')
                    await interaction.edit_original_response(
                        embed=error_embed(message=err_msg),
                        view=None,
                    )
        except Exception:
            logger.exception("Generate challenge failed")
            await interaction.edit_original_response(
                embed=error_embed(message="The service is temporarily unavailable."),
                view=None,
            )


# ── Main Command ─────────────────────────────────────────────────────


class WalletVerify(commands.Cog):
    """``/wallet verify``, Verify a registered wallet by signing a challenge."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="wallet_verify", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def wallet_verify(self, interaction: discord.Interaction) -> None:

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
                        unverified = [w for w in wallets if not w.get('is_verified')]

                        if not unverified:
                            all_verified = all(w.get('is_verified') for w in wallets) if wallets else False
                            if all_verified:
                                msg = 'All wallets are already **verified**. Use `/wallet list` to view them.'
                            else:
                                msg = 'No unverified wallets found. Use `/wallet register` to add one.'
                            return error_embed(message=msg)

                        embed = create_embed(
                            title="Verify a Wallet",
                            description=(
                                f"**{len(unverified)}** unverified wallet(s) found. "
                                f"Select one from the dropdown below to verify it.\n\n"
                                f"The first verified wallet will automatically be set as "
                                f"the **default**."
                            ),
                            color=BrandColor.PRIMARY,
                            footer="Xentra • Wallets",
                        )

                        view = WalletVerifyView(unverified, wallet_type)
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
    await bot.add_cog(WalletVerify(bot))
