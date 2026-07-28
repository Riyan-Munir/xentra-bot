import logging
from decimal import Decimal
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import BrandColor, create_embed, error_embed, info_embed
from utils.http import get_http_session
from utils.role_selector import ProfileRoleView
from utils.userid_resolver import resolve_user_id
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger(__name__)


class PlanSelect(discord.ui.Select):
    """Dropdown listing available subscription plans."""

    def __init__(self, plans: list[dict]) -> None:
        self.plans = plans
        options = []
        for p in plans:
            tier_label = p.get('tier_display') or p.get('tier', '\u2014')
            interval_label = p.get('interval_display') or p.get('billing_interval', '\u2014')
            price = p.get('effective_price') or p.get('price', '0')
            label = f"{tier_label} \u2014 {interval_label} (${price})"
            desc = f"{p.get('duration_days', 0)} days"
            if p.get('discount_percent'):
                desc += f" \u2022 {p['discount_percent']}% off"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    description=desc[:100],
                    value=str(p.get('id', '')),
                )
            )
        super().__init__(placeholder='Select a plan\u2026', options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        selected_id = self.values[0]
        plan = next((p for p in self.plans if str(p.get('id', '')) == selected_id), None)
        if not plan:
            await interaction.response.send_message(
                embed=error_embed('Selected plan not found.'), ephemeral=True,
            )
            return
        await self.view.show_plan_detail(interaction, plan)


class PlanDetailView(discord.ui.View):
    """View showing selected plan detail with back/close buttons."""

    def __init__(self, plans: list[dict], target_user_id: Optional[str] = None, user_data: Optional[dict] = None) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.plans = plans
        self.target_user_id = target_user_id
        self.user_data = user_data or {}

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label='\u2190 Back to Plans', style=discord.ButtonStyle.gray)
    async def back_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        embed = self._build_plans_embed(self.plans)
        view = PlansView(self.plans, target_user_id=self.target_user_id, user_data=self.user_data)
        view.author_id = interaction.user.id
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label='Close', style=discord.ButtonStyle.red)
    async def close_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Plans browsing cancelled.'),
            view=None,
        )

    @staticmethod
    def _build_plans_embed(plans: list[dict]) -> discord.Embed:
        embed = create_embed(
            title='Premium Subscription Plans',
            description='Browse available plans. Select one to view details.',
            color=BrandColor.PRIMARY,
            footer='Xentra \u2022 Premium Plans',
        )
        for p in plans:
            tier_label = p.get('tier_display') or p.get('tier', '\u2014')
            interval = p.get('interval_display') or p.get('billing_interval', '\u2014')
            price = p.get('effective_price') or p.get('price', '0')
            duration = p.get('duration_days', 0)
            discount = p.get('discount_percent')
            discount_str = f' \u2022 **{discount}% off**' if discount else ''
            embed.add_field(
                name=f'{tier_label} \u2014 {interval}',
                value=(
                    f'> **Price**: `${price}{discount_str}`\n'
                    f'> **Duration**: `{duration} days`'
                ),
                inline=False,
            )
        return embed


class PlansView(discord.ui.View):
    """View with plan selection dropdown and optional target user context."""

    def __init__(self, plans: list[dict], target_user_id: Optional[str] = None, user_data: Optional[dict] = None) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.plans = plans
        self.target_user_id = target_user_id
        self.user_data = user_data or {}
        self.add_item(PlanSelect(plans))

    async def on_timeout(self) -> None:
        self.stop()

    async def show_plan_detail(self, interaction: discord.Interaction, plan: dict) -> None:
        """Show details of a selected plan, with gift context if applicable."""
        tier_label = plan.get('tier_display') or plan.get('tier', '\u2014')
        interval = plan.get('interval_display') or plan.get('billing_interval', '\u2014')
        price = plan.get('effective_price') or plan.get('price', '0')
        original_price = plan.get('price', '0')
        duration = plan.get('duration_days', 0)
        discount = plan.get('discount_percent')
        discount_expires = plan.get('discount_expires_at')

        description_parts = [f'**{tier_label}** \u2014 {interval}']
        if self.target_user_id:
            description_parts.append(f'\n**Gift Mode** \u2014 This plan will be gifted to **{self.target_user_id}**.')

        embed = create_embed(
            title='Plan Details',
            description='\n'.join(description_parts),
            color=BrandColor.SUCCESS,
            footer='Xentra \u2022 Plan Details',
        )
        embed.add_field(name='Price', value=f'${price}', inline=True)
        if discount and Decimal(str(original_price)) > Decimal(str(discount)):
            embed.add_field(name='Original Price', value=f'${original_price}', inline=True)
            embed.add_field(name='Discount', value=f'{discount}% off', inline=True)
            if discount_expires:
                embed.add_field(name='Discount Ends', value=discount_expires, inline=True)
        embed.add_field(name='Duration', value=f'{duration} days', inline=True)
        embed.add_field(name='Billing', value=interval, inline=True)

        view = PlanDetailView(self.plans, target_user_id=self.target_user_id, user_data=self.user_data)
        view.author_id = interaction.user.id
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label='\u2190 Back', style=discord.ButtonStyle.gray, row=2)
    async def back_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed('Plans browsing cancelled.'), view=None,
        )


class SubscribePlansCommand(commands.Cog):
    """``/subscribe plans``, Browse subscription plans."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="subscribe_plans", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def subscribe_plans(
        self,
        interaction: discord.Interaction,
        user_id: Optional[str] = None,
    ):
        async def _fetch_plans(discord_id: str, role: str | None = None) -> tuple[list[dict] | None, str | None]:
            """Fetch plans from the backend, optionally filtered by role.

            Returns (plans_list, error_msg) — one of which is None.
            """
            url = f"{BACKEND_URL}premium/bot/plans/"
            params = {'discord_id': discord_id}
            if role:
                params['role'] = role
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            session = get_http_session()
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        try:
                            err = await resp.json()
                            return None, err.get('error', 'Failed to fetch plans.')
                        except Exception:
                            return None, 'Failed to fetch plans.'
                    data = await resp.json()
            except Exception:
                logger.exception("Failed to fetch premium plans")
                return None, "An unexpected error occurred. Please try again later."

            plans = data.get('plans', [])
            if not plans:
                return None, 'No subscription plans are currently available.'
            return plans, None

        async def callback(user_data):
            # discord_id isn't echoed by the backend, so inject it here
            user_data['discord_id'] = str(interaction.user.id)

            # ── Determine role for plan filtering ─────────────────────
            # Self (no user_id) → use active_role from user_data
            # System-ID gift → role is encoded in the prefix
            # Premium-ID gift → role unknown yet → no filter
            gift_result = None
            role_for_plans = None
            if not user_id:
                role_for_plans = user_data.get('active_role')
            else:
                gift_result = resolve_user_id(user_id)
                if gift_result.is_system:
                    role_for_plans = gift_result.role

            # ── Fetch plans ────────────────────────────────────────────
            plans, err = await _fetch_plans(user_data['discord_id'], role_for_plans)
            if err:
                return error_embed(err)

            # ── Self: no user_id → show plans for own role ─────────────
            if not user_id:
                embed = PlanDetailView._build_plans_embed(plans)
                view = PlansView(plans, user_data=user_data)
                view.author_id = interaction.user.id
                return embed, view

            # ── user_id provided → gifting flow ────────────────────────
            if gift_result.is_system:
                # System ID (FRL_/CLI_/SER_) — role is known
                role_label = gift_result.role.replace('_', ' ').title()
                embed = create_embed(
                    title='Gift Subscription',
                    description=(
                        f'You are browsing plans to gift to **{gift_result.original}** '
                        f'(**{role_label}**).\n\n'
                        'Select a plan below to view details and purchase via the '
                        'Xentra web dashboard.'
                    ),
                    color=BrandColor.PRIMARY,
                    footer='Xentra \u2022 Gift Subscription',
                )
                view = PlansView(plans, target_user_id=gift_result.original, user_data=user_data)
                view.author_id = interaction.user.id
                return embed, view
            else:
                # Premium ID — needs role selection via ProfileRoleView
                async def premium_gift_callback(inter, role, identifier, view):
                    """Called after user selects a role for the premium ID."""
                    # Fetch plans filtered to the selected gift role
                    role_plans, fetch_err = await _fetch_plans(user_data['discord_id'], role)
                    if fetch_err or not role_plans:
                        await inter.response.edit_message(
                            embed=error_embed(fetch_err or 'No plans available for this role.'),
                            view=None,
                        )
                        return

                    resolve_url = f"{BACKEND_URL}users/resolve-id/"
                    packet = BotPacketFactory.create_packet(
                        packet_type="user_resolve_id",
                        data={'raw_id': f"{role}:{identifier}"},
                        provider="bot",
                    )
                    hdrs = {'X-Webhook-Token': WEBHOOK_SECRET}
                    sess = get_http_session()
                    try:
                        async with sess.post(resolve_url, json=packet.to_dict(), headers=hdrs) as resp:
                            if resp.status == 200:
                                res = await resp.json()
                                display_id = res.get('canonical_id', identifier)
                                role_label = res.get('role', role).replace('_', ' ').title()
                                embed = create_embed(
                                    title='Gift Subscription',
                                    description=(
                                        f'You are browsing plans to gift to **{display_id}** '
                                        f'(**{role_label}**).\n\n'
                                        'Select a plan below to view details and purchase '
                                        'via the Xentra web dashboard.'
                                    ),
                                    color=BrandColor.PRIMARY,
                                    footer='Xentra \u2022 Gift Subscription',
                                )
                                plans_view = PlansView(
                                    role_plans, target_user_id=display_id, user_data=user_data,
                                )
                                plans_view.author_id = inter.user.id
                                await inter.response.edit_message(
                                    embed=embed, view=plans_view,
                                )
                            else:
                                err_resp = await resp.json()
                                err_embed = error_embed(
                                    err_resp.get('error', 'This ID is not valid for the selected role.')
                                )
                                await inter.response.edit_message(
                                    embed=err_embed, view=None,
                                )
                    except Exception:
                        logger.exception("Failed to resolve premium ID for gifting")
                        await inter.response.edit_message(
                            embed=error_embed("An unexpected error occurred."), view=None,
                        )

                role_view = ProfileRoleView(
                    gift_result.normalized,
                    premium_gift_callback,
                    user_data,
                )
                role_view.author_id = interaction.user.id
                embed = create_embed(
                    title='Role Selection Required',
                    description=(
                        f'The ID **{gift_result.original}** is a custom Premium ID. '
                        'Please select the target role perspective for gifting:'
                    ),
                    color=BrandColor.PRIMARY,
                    footer='Xentra \u2022 Role Selection',
                )
                return embed, role_view

        await validate_and_respond(interaction, callback)


async def setup(bot):
    await bot.add_cog(SubscribePlansCommand(bot))
