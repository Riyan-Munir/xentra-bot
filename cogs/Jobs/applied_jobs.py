import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, add_admin_post_button, sync_cog_commands
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, loading_embed
from utils.pagination import PaginationView
from utils.userid_resolver import resolve_user_id
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.job_mgmt')

class AppliedJobsCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="applied_jobs", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def applied_jobs(self, interaction: discord.Interaction, user_id: str = None):
        
        async def apps_callback(user_data):
            url = f"{BACKEND_URL}jobs/bot/applied/"
            params = {
                'discord_id': interaction.user.id
            }
            if interaction.guild_id:
                params['guild_id'] = interaction.guild_id
            normalized_user_id = None
            if user_id:
                result = resolve_user_id(user_id)
                normalized_user_id = result.normalized
                params['user_id'] = normalized_user_id
                

            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        total_count = data['count']
                        apps_list = data['results']
                        
                        if total_count == 0:
                            return error_embed(message="No applications found yet.")
                            
                        view = ApplicationsPaginationView(apps_list, 1, total_count, user_data, target_user_id=normalized_user_id)
                        view.author_id = interaction.user.id
                        view.update_buttons()
                        return view.build_embed(), view
                    else:
                        err_data = await resp.json()
                        return error_embed(message=err_data.get('error', 'Could not load your applications.'))
        
        await validate_and_respond(interaction, apps_callback)

class ApplicationsPaginationView(PaginationView):
    def __init__(self, apps_data, current_page, total_count, user_data, target_user_id=None):
        total_pages = (total_count + 4) // 5
        super().__init__(current_page=current_page, total_pages=total_pages, user_data=user_data)
        self.apps = apps_data
        self.total_count = total_count
        self.target_user_id = target_user_id

    async def change_page(self, interaction: discord.Interaction, new_page):
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
                if resp.status == 200:
                    data = await resp.json()
                    self.apps = data['results']
                    self.current_page = new_page
                    await self.update_message(interaction)
                else:
                    await interaction.response.edit_message(embed=error_embed(message="Failed to load page."), view=self)

    def build_embed(self):
        title = "Freelancer Applied Jobs"
        if self.target_user_id:
            title = f"Applied Jobs for {self.target_user_id}"
            
        is_premium = any(app.get('is_premium_freelancer', False) for app in self.apps)
        embed_color = BrandColor.PREMIUM if is_premium else BrandColor.PRIMARY
            
        embed = create_embed(
            title=title,
            description=f"Showing active applications (Page {self.current_page}/{self.total_pages})",
            color=embed_color
        )

        if not self.apps:
            embed.description = "No pending applications found."
            return embed

        for app in self.apps:
            job_title = f"**{app['job_title']}**"
            
            status_text = {
                'pending': "Pending",
                'accepted': "Accepted",
                'rejected': "Rejected"
            }.get(app['status'], "Unknown")
            
            details = (
                f"> **Application ID**: `{app['application_id']}` • **Job ID**: `{app['job_id']}`\n"
                f"> **Bid Amount**: `${app['bid_amount']}` • **Job Budget**: `${app['job_budget_min']}-${app['job_budget_max']}`\n"
                f"> **Status**: `{status_text}`"
            )
            
            embed.add_field(
                name=job_title,
                value=details,
                inline=False
            )

        embed.set_footer(text=f"Xentra • Total Applications: {self.total_count}")
        return embed

async def setup(bot):
    await bot.add_cog(AppliedJobsCommand(bot))