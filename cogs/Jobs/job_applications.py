import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, add_admin_post_button, sync_cog_commands, is_author
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, info_embed, loading_embed
from utils.pagination import PaginationView
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.job_mgmt')

class JobApplications(commands.Cog):
    """``/job_applications``, View applications for a specific job."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="job_applications", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def job_applications(self, interaction: discord.Interaction, job_id: str):
        
        async def apps_callback(user_data):
            url = f"{BACKEND_URL}jobs/bot/applications/"
            params = {
                'discord_id': interaction.user.id,
                'job_id': job_id
            }
            if interaction.guild_id:
                params['guild_id'] = interaction.guild_id
                
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
                                    '> ***No applications found for this job.***\n'
                                    '> No freelancer has applied to this listing yet.\n'
                                    '\n'
                                    '> __Check back later for new applications.__'
                                ),
                                footer='Xentra • Jobs',
                            )
                            
                        view = JobApplicationsPaginationView(apps_list, 1, total_count, user_data, job_id)
                        view.author_id = interaction.user.id
                        view.update_buttons()
                        return view.build_embed(), view
                    else:
                        err_data = await resp.json()
                        return error_embed(message=err_data.get('error', 'Could not load applications.'))
        
        await validate_and_respond(interaction, apps_callback)

class JobApplicationsPaginationView(PaginationView):
    def __init__(self, apps_data, current_page, total_count, user_data, job_id):
        total_pages = (total_count + 4) // 5
        super().__init__(current_page=current_page, total_pages=total_pages, user_data=user_data)
        self.author_id = None
        self.apps = apps_data
        self.total_count = total_count
        self.job_id = job_id

    async def change_page(self, interaction: discord.Interaction, new_page):
        if not is_author(interaction, self):
            return
        url = f"{BACKEND_URL}jobs/bot/applications/"
        params = {
            'discord_id': interaction.user.id,
            'job_id': self.job_id,
            'page': new_page
        }
        if interaction.guild_id:
            params['guild_id'] = interaction.guild_id
            
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
        title = f"Applications for Job: {self.job_id}"
        
        is_premium_freelancer = any(app.get('is_premium_freelancer', False) for app in self.apps)
        embed_color = BrandColor.PREMIUM if is_premium_freelancer else BrandColor.PRIMARY

        lines = [
            f"> ***{title}** — page `{self.current_page}` of `{self.total_pages}`*",
            f"**Total:** `{self.total_count}`",
        ]

        if not self.apps:
            lines.append("\n> __No applications found for this job yet. Check back later.__")
            return create_embed(
                title=title,
                description="\n".join(lines),
                color=embed_color,
                footer="Xentra • Jobs",
            )

        for idx, app in enumerate(self.apps, start=1):
            lines.append(
                f"\n`{idx}.` `{app['application_id']}` • **{app['effective_freelancer_id']}** — `{app['status'].title()}`\n"
                f"> Bid: `${app['bid_amount']}` • Job: `{app['job_id']}`"
            )

        lines.append("\n> __Use the buttons below to act on an application.__")

        return create_embed(
            title=title,
            description="\n".join(lines),
            color=embed_color,
            footer="Xentra • Jobs",
        )

async def setup(bot):
    await bot.add_cog(JobApplications(bot))