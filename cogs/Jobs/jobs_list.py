import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, add_admin_post_button, sync_cog_commands, is_author
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, info_embed
from utils.pagination import PaginationView
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.jobs.jobs_list')

class JobCategoryFilterSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="All Categories", value="all"),
            discord.SelectOption(label="Web Development", value="Web development"),
            discord.SelectOption(label="Mobile App Development", value="Mobile app development"),
            discord.SelectOption(label="Game Development", value="Game development"),
            discord.SelectOption(label="AI & Machine Learning", value="Ai machine learning"),
            discord.SelectOption(label="UI/UX & Graphic Design", value="Ui ux graphic design"),
            discord.SelectOption(label="Video & Animation Editing", value="Video animation editing"),
            discord.SelectOption(label="Writing & Copywriting", value="Writing copywriting"),
            discord.SelectOption(label="Digital Marketing & SEO", value="Digital marketing seo"),
            discord.SelectOption(label="Ecommerce", value="Ecommerce"),
            discord.SelectOption(label="Data Analysis", value="Data analysis"),
            discord.SelectOption(label="Virtual Assistant & Admin", value="Virtual assistant admin"),
            discord.SelectOption(label="Other", value="Other")
        ]
        super().__init__(placeholder="Select Category (Default: All)", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        self.view.category = self.values[0]
        await interaction.response.defer()


class JobBudgetFilterSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Any Budget", value="any"),
            discord.SelectOption(label="Low", value="low"),
            discord.SelectOption(label="Medium", value="medium"),
            discord.SelectOption(label="High", value="high")
        ]
        super().__init__(placeholder="Select Budget (Default: Any)", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        self.view.budget_level = self.values[0]
        await interaction.response.defer()


class JobOrderFilterSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Newest First", value="newest"),
            discord.SelectOption(label="Highest Pay", value="budget_max_desc"),
            discord.SelectOption(label="Lowest Pay", value="budget_max_asc"),
            discord.SelectOption(label="Soonest Deadline", value="deadline")
        ]
        super().__init__(placeholder="Sort By (Default: Newest)", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        self.view.sort_by = self.values[0]
        await interaction.response.defer()


class JobsListFilterView(discord.ui.View):
    def __init__(self, user_data, featured=None):
        super().__init__(timeout=180)
        self.author_id: int | None = None
        self.user_data = user_data
        self.featured = featured
        self.category = "all"
        self.budget_level = "any"
        self.sort_by = "newest"
        
        self.add_item(JobCategoryFilterSelect())
        self.add_item(JobBudgetFilterSelect())
        self.add_item(JobOrderFilterSelect())
        
        # Action buttons
        send_btn = discord.ui.Button(label="Search Opportunities", style=discord.ButtonStyle.primary, row=3)
        send_btn.callback = self.on_send
        self.add_item(send_btn)
        
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, row=3)
        cancel_btn.callback = self.on_cancel
        self.add_item(cancel_btn)

    async def on_timeout(self) -> None:
        self.stop()
    
    async def on_cancel(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message="The job search configuration has been aborted."),
            view=None,
        )
    
    async def on_send(self, interaction: discord.Interaction):
        if not is_author(interaction, self):
            return
        # Disable all UI components
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        
        url = f"{BACKEND_URL}jobs/bot/list/"
        params = {
            'discord_id': interaction.user.id,
            'page': 1
        }
        if interaction.guild_id:
            params['guild_id'] = interaction.guild_id
        if self.category and self.category != "all":
            params['category'] = self.category
        if self.budget_level and self.budget_level != "any":
            params['budget_level'] = self.budget_level
        if self.sort_by:
            params['sort_by'] = self.sort_by
        if self.featured is not None:
            params['is_featured'] = 'true' if self.featured else 'false'
        
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        
        try:
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        total_count = data['count']
                        jobs_list = data['results']
                        
                        if total_count == 0:
                            await interaction.edit_original_response(
                                embed=error_embed(message="No jobs match your search filters."),
                                view=None,
                            )
                            return
                        
                        view = JobsDiscoverPaginationView(
                            jobs_list, 1, total_count, self.user_data,
                            category=self.category, budget_level=self.budget_level,
                            sort_by=self.sort_by, featured=self.featured
                        )
                        view.author_id = self.author_id
                        view.update_buttons()
                        embed = view.build_embed()
                        await interaction.edit_original_response(embed=embed, view=view)
                    else:
                        err_data = await resp.json()
                        await interaction.edit_original_response(
                            embed=error_embed(message=err_data.get('error', 'Could not load jobs.')),
                            view=None,
                        )
        except Exception as e:
            logger.error(f"Error querying jobs/bot/list/: {e}")
            await interaction.edit_original_response(
                embed=error_embed(message="Something went wrong. Please try again."),
                view=None,
            )


class JobsDiscoverPaginationView(PaginationView):
    def __init__(self, jobs_data, current_page, total_count, user_data, category="", budget_level="", sort_by="", featured=None):
        total_pages = (total_count + 4) // 5
        super().__init__(current_page=current_page, total_pages=total_pages, user_data=user_data)
        self.jobs = jobs_data
        self.total_count = total_count
        self.category = category
        self.budget_level = budget_level
        self.sort_by = sort_by
        self.featured = featured
    
    async def change_page(self, interaction: discord.Interaction, new_page):
        url = f"{BACKEND_URL}jobs/bot/list/"
        params = {
            'discord_id': interaction.user.id,
            'page': new_page
        }
        if interaction.guild_id:
            params['guild_id'] = interaction.guild_id
        if self.category and self.category != "all":
            params['category'] = self.category
        if self.budget_level and self.budget_level != "any":
            params['budget_level'] = self.budget_level
        if self.sort_by:
            params['sort_by'] = self.sort_by
        if self.featured is not None:
            params['is_featured'] = 'true' if self.featured else 'false'
        
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        
        try:
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.jobs = data['results']
                        self.current_page = new_page
                        await self.update_message(interaction)
                    else:
                        await interaction.response.edit_message(embed=error_embed(message="Could not load this page."), view=self)
        except Exception as e:
            logger.error(f"Error querying jobs/bot/list/: {e}")
            await interaction.response.edit_message(embed=error_embed(message="Something went wrong. Please try again."), view=self)
    
    def build_embed(self):
        # Premium Styling Detection
        has_premium_job = any(job.get('is_featured', False) for job in self.jobs)
        embed_color = BrandColor.PREMIUM if has_premium_job else BrandColor.PRIMARY
        
        embed = create_embed(
            title="Available Opportunities",
            description=f"Showing active listings (Page {self.current_page}/{self.total_pages})",
            color=embed_color,
            footer=f"Xentra • Total Matching Jobs: {self.total_count}"
        )
        
        if not self.jobs:
            embed.description = "No matching jobs found."
            return embed
        
        for job in self.jobs:
            is_featured = job.get('is_featured', False)
            featured_tag = " ✨" if is_featured else ""
            
            job_title = f"{featured_tag}**{job['title']}**"
            
            deadline_str = f" • **Deadline**: `{job['deadline']}`" if job.get('deadline') else ""
            
            details = (
                f"> **Job ID**: `{job['job_id']}` • **Client ID**: `{job['client_id']}`\n"
                f"> **Role**: `{job['experience_level'].title()}`\n"
                f"> **Category**: `{job['category'].replace('_', ' ').title()}`\n"
                f"> **Budget Range**: `${job['budget_min']} - ${job['budget_max']}`{deadline_str}"
            )
            
            embed.add_field(
                name=job_title,
                value=details,
                inline=False
            )
        
        return embed


class JobsList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="jobs_list", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def jobs_list(self, interaction: discord.Interaction, featured: bool = None):
        """
        Interactively discover and search for opportunities.
        """
        async def list_callback(user_data):
            active_role = user_data.get('active_role', 'non_bot_user')
            
            # Server Admin Flow: Direct lookup of non-confidential client jobs in this server
            if active_role == 'server_admin':
                url = f"{BACKEND_URL}jobs/bot/list/"
                params = {
                    'discord_id': interaction.user.id,
                    'page': 1
                }
                if interaction.guild_id:
                    params['guild_id'] = interaction.guild_id
                if featured is not None:
                    params['is_featured'] = 'true' if featured else 'false'
                
                headers = {'X-Webhook-Token': WEBHOOK_SECRET}
                
                try:
                    session = get_http_session()
                    async with session.get(url, params=params, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            total_count = data['count']
                            jobs_list = data['results']

                            if total_count == 0:
                                return error_embed(message="No jobs posted in this server yet.")

                            view = JobsDiscoverPaginationView(
                                jobs_list, 1, total_count, user_data,
                                category="all", budget_level="any",
                                sort_by="newest", featured=featured
                            )
                            view.author_id = interaction.user.id
                            view.update_buttons()
                            embed = view.build_embed()
                            return embed, view
                        else:
                            err_data = await resp.json()
                            return error_embed(message=err_data.get('error', 'Could not load server jobs.'))
                except Exception as e:
                    logger.error(f"Error querying jobs/bot/list/: {e}")
                    return error_embed(message="Something went wrong. Please try again.")

            # Freelancer Flow: Interactive search filters configuration
            embed = create_embed(
                title="Discover Jobs",
                description=(
                    "**Configure Filters**: Use the dropdowns below to narrow down your search.\n"
                    "**Category**: Filter by job type (Default: All Categories).\n"
                    "**Budget**: Filter by budget tier (Default: Any Budget).\n"
                    "**Sort By**: Choose your preferred ordering (Default: Newest First).\n\n"
                    + (f"**Featured Mode**: Showing only featured jobs.\n" if featured else "")
                ),
                color=BrandColor.PREMIUM if featured else BrandColor.PRIMARY,
                footer="Xentra • Select your filters and hit Search Opportunities"
            )
            view = JobsListFilterView(user_data, featured=featured)
            view.author_id = interaction.user.id
            return embed, view
        
        await validate_and_respond(interaction, list_callback)


async def setup(bot):
    await bot.add_cog(JobsList(bot))