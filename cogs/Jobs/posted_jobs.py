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

class PostedJobs(commands.Cog):
    """``/posted_jobs``, View jobs you have posted."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="posted_jobs", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def posted_jobs(self, interaction: discord.Interaction, user_id: str = None):
        
        async def jobs_callback(user_data):
            active_role = user_data.get('active_role')
            if active_role == 'server_admin' and not user_id:
                return error_embed(
                    message='Provide a Client ID.'
                )

            url = f"{BACKEND_URL}jobs/bot/posted/"
            params = {
                'discord_id': interaction.user.id
            }
            if interaction.guild_id:
                params['guild_id'] = interaction.guild_id
            normalized_user_id = None
            if user_id:
                result = resolve_user_id(user_id)
                # ── Strict prefix check ──────────────────────────────
                # posted_jobs only accepts CLIENT IDs
                if result.is_system and result.prefix != 'CLI':
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
                        jobs_list = data['results']
                        
                        if total_count == 0:
                            return info_embed(message="No open jobs found.")
                            
                        view = JobsPaginationView(jobs_list, 1, total_count, user_data, target_user_id=normalized_user_id)
                        view.author_id = interaction.user.id
                        view.update_buttons()
                        return view.build_embed(), view
                    else:
                        err_data = await resp.json()
                        return error_embed(message=err_data.get('error', 'Could not load jobs.'))
        
        await validate_and_respond(interaction, jobs_callback)


class JobsPaginationView(PaginationView):
    def __init__(self, jobs_data, current_page, total_count, user_data, target_user_id=None):
        total_pages = (total_count + 4) // 5
        super().__init__(current_page=current_page, total_pages=total_pages, user_data=user_data)
        self.author_id = None
        self.jobs = jobs_data
        self.total_count = total_count
        self.target_user_id = target_user_id  # The client_id we are querying

    async def change_page(self, interaction: discord.Interaction, new_page):
        if not is_author(interaction, self):
            return
        url = f"{BACKEND_URL}jobs/bot/posted/"
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
                    self.jobs = data['results']
                    self.current_page = new_page
                    await self.update_message(interaction)
                else:
                    await interaction.response.edit_message(embed=error_embed(message="Could not load this page."), view=None)

    def build_embed(self):
        title = "Client Posted Jobs"
        if self.target_user_id:
            title = f"Posted Jobs for {self.target_user_id}"
            
        # Premium Styling Detection (Featured jobs indicate premium client perspective)
        is_premium_client = any(job.get('is_featured', False) for job in self.jobs)
        embed_color = BrandColor.PREMIUM if is_premium_client else BrandColor.PRIMARY
            
        embed = create_embed(
            title=title,
            description=f"Showing active listings (Page {self.current_page}/{self.total_pages})",
            color=embed_color,
            footer=f"Xentra • Total Open Jobs: {self.total_count}"
        )

        if not self.jobs:
            embed.description = "No open jobs found for this client."
            return embed

        for job in self.jobs:
            job_title = f"**{job['title']}**"
            budget = f"${job['budget_min']} - ${job['budget_max']}"
            
            details = (
                f"> **Category & Level**: `{job['category']}` • `{job['experience_level']}`\n"
                f"> **Budget**: `{budget}` • **Job ID**: `{job['job_id']}`\n"
                f"> **Applications**: `{job['application_count']}` candidates"
            )
            
            embed.add_field(
                name=job_title,
                value=details,
                inline=False
            )

        return embed

async def setup(bot):
    await bot.add_cog(PostedJobs(bot))