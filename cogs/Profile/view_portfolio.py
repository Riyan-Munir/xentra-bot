import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.pagination import PaginationView
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, loading_embed
from utils.userid_resolver import resolve_user_id
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.profile_mgmt')

class PortfolioPaginationView(PaginationView):
    """Portfolio uses 0‑based page indexing — overrides prev/next accordingly."""
    def __init__(self, profile_data, portfolio, is_premium, viewer_data=None):
        items = portfolio.get('items', [])
        total_pages = len(items) if items else 1
        super().__init__(current_page=1, total_pages=total_pages, user_data=viewer_data or {})
        self.profile_data = profile_data
        self.portfolio = portfolio
        self.items = items
        self.is_premium = is_premium
        # Shift back to 0‑based for portfolio internal tracking
        self._portfolio_page = 0

    def update_buttons(self, embed: discord.Embed = None):
        """Portfolio uses 0‑based page index for its internal tracking."""
        self.clear_items()
        if self.total_pages > 1:
            prev = discord.ui.Button(
                label="Previous",
                style=discord.ButtonStyle.gray,
                disabled=self._portfolio_page <= 0,
            )
            prev.callback = self.prev_page
            self.add_item(prev)

            nxt = discord.ui.Button(
                label="Next",
                style=discord.ButtonStyle.gray,
                disabled=self._portfolio_page >= self.total_pages - 1,
            )
            nxt.callback = self.next_page
            self.add_item(nxt)

        close = discord.ui.Button(label="Close", style=discord.ButtonStyle.red)
        close.callback = self.close_view
        self.add_item(close)

        from utils.command_handler import add_admin_post_button
        add_admin_post_button(self, self.build_embed(), self.user_data)

    async def prev_page(self, interaction: discord.Interaction):
        self._portfolio_page -= 1
        await self.update_message(interaction)

    async def next_page(self, interaction: discord.Interaction):
        self._portfolio_page += 1
        await self.update_message(interaction)

    def build_embed(self):
        username = self.profile_data.get('username', 'Professional')
        discord_name = self.profile_data.get('discord_username', 'User')
        pfp = self.profile_data.get('discord_avatar')
        avatar_url = f"https://cdn.discordapp.com/avatars/{self.profile_data.get('user_id', '')}/{pfp}.png" if pfp else None
        
        # Premium Styling
        embed_color = BrandColor.PREMIUM if self.is_premium else BrandColor.PRIMARY
        
        # ── Profile Header (always visible on every page) ────────────
        page_info = f" — Project {self.current_page + 1}/{self.total_pages}" if self.items else ""
        embed = create_embed(
            title=self.portfolio.get('title', f"{username}'s Portfolio"),
            description=self.portfolio.get('description', "Professional freelancer portfolio.") + page_info,
            color=embed_color,
            thumbnail=avatar_url,
        )
        
        if self.is_premium:
            embed.add_field(name="Premium Status", value="> **Premium Tier Gold Portfolio Design**", inline=False)
        
        pref_field = self.portfolio.get('preferred_field')
        skills = self.portfolio.get('skill_tags', [])
        skills_str = " ".join(f"`{s}`" for s in skills) if skills else "`None`"
        
        embed.add_field(
            name="Freelancer Parameters",
            value=(
                f"> **Preferred Field**: `{pref_field or 'Not Specified'}`\n"
                f"> **Expertise & Skills**: {skills_str}"
            ),
            inline=False,
        )
        
        # ── Project Section ──────────────────────────────────────────
        if self.items:
            project = self.items[self.current_page]
            p_title = project.get('title', 'Unnamed Project')
            p_desc = project.get('description', 'No description provided.')
            p_tech = project.get('technologies', [])
            p_url = project.get('project_url')
            p_image = project.get('image_url')
            
            project_text = f"**{p_title}**\n{p_desc}"
            if p_url:
                project_text += f"\n\n[**View Project Source**]({p_url})"
                
            tech_str = " ".join(f"`{t}`" for t in p_tech) if p_tech else "`None`"
            
            project_details = (
                f"> **Title & Description**: {project_text}\n"
                f"> **Technologies Used**: {tech_str}"
            )
            embed.add_field(
                name=f"Featured Project {self.current_page + 1}",
                value=project_details,
                inline=False,
            )
            
            if p_image:
                embed.set_image(url=p_image)
        else:
            embed.add_field(name="Projects Showcase", value="> No projects showcased yet.", inline=False)
        
        embed.set_footer(text='Xentra •')
        return embed

class ViewPortfolio(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="view_portfolio", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def view_portfolio(self, interaction: discord.Interaction, user_id: str = None):
        
        async def fetch_and_show_portfolio(inter, role, canonical_id, viewer_data):
            if role != 'freelancer':
                return error_embed(message="This ID doesn't belong to a freelancer.")
            
            url = f"{BACKEND_URL}profiles/bot-detail/"
            params = {'profile_id': canonical_id, 'role': 'freelancer', 'discord_id': inter.user.id}
            if inter.guild_id:
                params['guild_id'] = inter.guild_id
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        portfolios = data.get('portfolios', [])
                        if not portfolios:
                            return error_embed(message="No portfolio found for this ID.")
                        
                        portfolio = portfolios[0]
                        is_premium = data.get('premium_tier') == 'premium'
                        
                        # Use pagination view
                        view = PortfolioPaginationView(data, portfolio, is_premium, viewer_data=viewer_data)
                        view.author_id = interaction.user.id
                        view.update_buttons() # Initialize buttons
                        return view.build_embed(), view
                    else:
                        err = await resp.json()
                        return error_embed(message=err.get('error', 'Could not load portfolio.'))
        
        async def portfolio_callback(user_data):
            if not user_id:
                # Self-lookup
                if not user_data.get('registered'):
                    return error_embed(message="**You** must be registered to view your own portfolio.")
                
                # Use current user's freelancer profile
                url = f"{BACKEND_URL}profiles/bot-detail/"
                params = {'discord_id': interaction.user.id, 'role': 'freelancer'}
                if interaction.guild_id:
                    params['guild_id'] = interaction.guild_id
                headers = {'X-Webhook-Token': WEBHOOK_SECRET}
                
                session = get_http_session()
                async with session.get(url, params=params, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            portfolios = data.get('portfolios', [])
                            if not portfolios:
                                return error_embed(message="No portfolio found for this ID.")
                            
                            portfolio = portfolios[0]
                            is_premium = data.get('premium_tier') == 'premium'
                            
                            view = PortfolioPaginationView(data, portfolio, is_premium, viewer_data=user_data)
                            view.author_id = interaction.user.id
                            view.update_buttons()
                            return view.build_embed(), view
                        else:
                            err = await resp.json()
                            return error_embed(message=err.get('error', 'Could not load your portfolio.'))
            
            # ID resolution logic (Force freelancer role for portfolios)
            resolve_url = f"{BACKEND_URL}users/resolve-id/"
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            result = resolve_user_id(user_id)
            if result.is_system:
                # Only FRL_ prefix is valid for portfolios
                if result.prefix != 'FRL':
                    return error_embed(message="This ID doesn't belong to a freelancer.")
                packet = BotPacketFactory.create_packet(
                    packet_type="user_resolve_id",
                    data={'raw_id': result.normalized},
                    provider="bot"
                )
            else:
                # Premium/Custom ID — force freelancer role perspective
                packet = BotPacketFactory.create_packet(
                    packet_type="user_resolve_id",
                    data={'raw_id': f"freelancer:{result.normalized}"},
                    provider="bot"
                )
            
            session = get_http_session()
            async with session.post(resolve_url, json=packet.to_dict(), headers=headers) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        if res['role'] != 'freelancer':
                            return error_embed(message="This ID doesn't belong to a freelancer.")
                        return await fetch_and_show_portfolio(interaction, res['role'], res['canonical_id'], user_data)
                    else:
                        err = await resp.json()
                        return error_embed(message=err.get('error', "This ID doesn't belong to a freelancer."))
        
        await validate_and_respond(interaction, portfolio_callback)

async def setup(bot):
    await bot.add_cog(ViewPortfolio(bot))