import discord
from discord.ext import commands
from discord import app_commands
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, add_admin_post_button, sync_cog_commands
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, loading_embed
from packet_templates.factory import BotPacketFactory
from utils.http import get_http_session

logger = logging.getLogger('bot.job_mgmt')

class JobDetailsCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="job_details", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def job_details(self, interaction: discord.Interaction, job_id: str):
        
        async def details_callback(user_data):
            url = f"{BACKEND_URL}jobs/bot/detail/"
            params = {
                'discord_id': interaction.user.id,
                'job_id': job_id
            }
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    job = await resp.json()
                    
                    is_featured = job.get('is_featured', False)
                    featured_icon = "✨ " if is_featured else ""
                    embed_color = BrandColor.PREMIUM if is_featured else BrandColor.PRIMARY
                    
                    embed = create_embed(
                        title=f"Job Details: {job['title']}",
                        description=job['description'],
                        color=embed_color,
                        footer=f"Xentra • Posted by {job['client_id']} • Job ID: {job_id}",
                    )
                    
                    # Skills Tags
                    skills = ", ".join([f"`{s.strip()}`" for s in job['skills_required']]) if job['skills_required'] else "`None`"
                    
                    embed.add_field(name="Skills Required", value=skills, inline=False)
                    
                    # Additional details
                    budget_text = f"${job['budget_min']} - ${job['budget_max']}" if job.get('budget_min') and job.get('budget_max') else job.get('budget_range', 'Not specified')
                    embed.add_field(name="Budget", value=f"`{budget_text}`", inline=True)
                    embed.add_field(name="Deadline", value=job.get('deadline', 'Not specified'), inline=True)
                    embed.add_field(name="Experience Level", value=f"`{job.get('experience_level', 'Any').title()}`", inline=True)
                    embed.add_field(name="Category", value=f"`{job.get('category', 'General').title()}`", inline=True)
                    
                    # Job Metadata
                    metadata = []
                    if job.get('is_confidential'):
                        metadata.append("Confidential")
                    if job.get('is_strict'):
                        metadata.append("Strict")
                    if metadata:
                        embed.add_field(name="Job Attributes", value=" • ".join(metadata), inline=True)
                    
                    # Use centralized tracker for admin button
                    from utils.command_handler import PublicPostView
                    final_view = PublicPostView(embed, user_data)
                    if len(final_view.children) == 0:
                        return embed
                    return embed, final_view
                else:
                    err_data = await resp.json()
                    return error_embed(message=err_data.get('error', 'Could not load job details.'))
        
        await validate_and_respond(interaction, details_callback)

async def setup(bot):
    await bot.add_cog(JobDetailsCommand(bot))
