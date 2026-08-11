import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, add_admin_post_button, sync_cog_commands, is_author
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, info_embed, loading_embed
from utils.pagination import PaginationView
from utils.userid_resolver import resolve_user_id
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.job_mgmt')

class AppliedJobs(commands.Cog):
    """``/applied_jobs``, View jobs you have applied for."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="applied_jobs", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def applied_jobs(self, interaction: discord.Interaction, user_id: str = None):
        
        async def apps_callback(user_data):
            active_role = user_data.get('active_role')
            if active_role == 'server_admin' and not user_id:
                return error_embed(
                    message='Provide a Freelancer ID.'
                )

            url = f"{BACKEND_URL}jobs/bot/applied/"
            params = {
                'discord_id': interaction.user.id
            }
            if interaction.guild_id:
                params['guild_id'] = interaction.guild_id
            normalized_user_id = None
            if user_id:
                result = resolve_user_id(user_id)
                # ── Strict prefix check ──────────────────────────────
                # applied_jobs only accepts FREELANCER IDs
                if result.is_system and result.prefix != 'FRL':
                    return error_embed(
                        message="Provide a valid ID."
                    )
                # Use backend resolution for all ID types (handles premium/custom IDs)
                resolve_url = f"{BACKEND_URL}users/resolve-id/"
                packet = BotPacketFactory.create_packet(
                    packet_type="user_resolve_id",
                    data={'raw_id': result.normalized},
                    provider="bot"
                )
                resolve_headers = {'X-Webhook-Token': WEBHOOK_SECRET}
                session = get_http_session()
                async with session.post(resolve_url, json=packet.to_dict(), headers=resolve_headers) as resp:
                        if resp.status in (200, 201):
                            res = await resp.json()
                            normalized_user_id = res['canonical_id']
                        else:
                            err = await resp.json()
                            return error_embed(message=err.get('error', 'Could not resolve user ID.'))
                params['user_id'] = normalized_user_id

            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        total_count = data['count']
                        apps_list = data['results']
                        
                        if total_count == 0:
                            return info_embed(
                                message=(
                                    '> ***No applications found.***\n'
                                    '> You have not applied to any open jobs yet.\n'
                                    '\n'
                                    '> __Use /jobs list to discover opportunities and apply.__'
                                ),
                                footer='Xentra • Jobs',
                            )
                            
                        view = ApplicationsPaginationView(apps_list, 1, total_count, user_data, target_user_id=normalized_user_id)
                        view.author_id = interaction.user.id
                        view.update_buttons()
                        return view.build_embed(), view
                    else:
                        err_data = await resp.json()
                        return error_embed(message=err_data.get('error', 'Could not load applications.'))
        
        await validate_and_respond(interaction, apps_callback)

class ApplicationsPaginationView(PaginationView):
    def __init__(self, apps_data, current_page, total_count, user_data, target_user_id=None):
        total_pages = (total_count + 4) // 5
        super().__init__(current_page=current_page, total_pages=total_pages, user_data=user_data)
        self.author_id = None
        self.apps = apps_data
        self.total_count = total_count
        self.target_user_id = target_user_id

    async def change_page(self, interaction: discord.Interaction, new_page):
        if not is_author(interaction, self):
            return
        url = f"{BACKEND_URL}jobs/bot/applied/"
        params = {
            'discord_id': interaction.user.id,
            'page': new_page
        }
        if interaction.guild_id:
            params['guild_id'] = interaction.guild_id
        if self.target_user_id:
            params['user_id'] = self.target_user_id
            
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        
        session = get_http_session()
        async with session.get(url, params=params, headers=headers) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    self.apps = data['results']
                    self.current_page = new_page
                    await self.update_message(interaction)
                else:
                    await interaction.response.edit_message(embed=error_embed(message="Could not load this page."), view=None)

    def build_embed(self):
        title = "Freelancer Applied Jobs"
        if self.target_user_id:
            title = f"Applied Jobs for {self.target_user_id}"
            
        is_premium = any(app.get('is_premium_freelancer', False) for app in self.apps)
        embed_color = BrandColor.PREMIUM if is_premium else BrandColor.PRIMARY

        lines = [
            f"> ***{title}** — page `{self.current_page}` of `{self.total_pages}`*",
            f"**Total:** `{self.total_count}`",
        ]

        if not self.apps:
            lines.append("\n> __No pending applications found. Use /jobs list to discover opportunities.__")
            return create_embed(
                title=title,
                description="\n".join(lines),
                color=embed_color,
                footer="Xentra • Jobs",
            )

        for idx, app in enumerate(self.apps, start=1):
            status_text = {
                'pending': "Pending",
                'accepted': "Accepted",
                'rejected': "Rejected"
            }.get(app['status'], "Unknown")
            lines.append(
                f"\n`{idx}.` `{app['application_id']}` • **{app['job_title']}** — `{status_text}`\n"
                f"> Bid: `${app['bid_amount']}` • Budget: `${app['job_budget_min']}-${app['job_budget_max']}` • Job: `{app['job_id']}`"
            )

        lines.append("\n> __Use the buttons below to act on an application.__")

        return create_embed(
            title=title,
            description="\n".join(lines),
            color=embed_color,
            footer="Xentra • Jobs",
        )

async def setup(bot):
    await bot.add_cog(AppliedJobs(bot))
