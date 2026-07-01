import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, add_admin_post_button, sync_cog_commands
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, throttled_embed, loading_embed
from utils.userid_resolver import resolve_user_id
from utils.role_selector import ProfileRoleView
from utils.view_tracker import increment_profile_view
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.profile_mgmt')

class UserProfile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="user_profile", description="...")
    @app_commands.checks.cooldown(2, 10, key=lambda i: i.user.id)
    async def user_profile(self, interaction: discord.Interaction, user_id: str = None):
        
        async def fetch_and_show(inter, role, canonical_id):
            url = f"{BACKEND_URL}profiles/bot-detail/"
            params = {'profile_id': canonical_id, 'role': role, 'discord_id': inter.user.id}
            if inter.guild_id:
                params['guild_id'] = inter.guild_id
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            session = get_http_session()
            async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Increment view count on backend
                        await increment_profile_view(interaction.user.id, role, canonical_id)
                        return self.build_profile_embed(data, role)
                    elif resp.status == 429:
                        err = await resp.json()
                        return throttled_embed(err.get('retry_after', 10))
                    else:
                        err = await resp.json()
                        return error_embed(message=err.get('error', 'Could not load profile data.'))

        async def premium_role_callback(inter, role, identifier, view):
            # Resolve premium ID
            resolve_url = f"{BACKEND_URL}users/resolve-id/"
            packet = BotPacketFactory.create_packet(
                packet_type="user_resolve_id",
                data={'raw_id': f"{role}:{identifier}"},
                provider="bot"
            )
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            session = get_http_session()
            async with session.post(resolve_url, json=packet.to_dict(), headers=headers) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        # View increment is handled inside fetch_and_show()
                        embed = await fetch_and_show(inter, res['role'], res['canonical_id'])
                        
                        # Use centralized tracker for admin button
                        from utils.command_handler import PublicPostView
                        final_view = PublicPostView(embed, view.user_data)
                        if len(final_view.children) == 0: final_view = None
                        
                        await inter.response.edit_message(content=None, embed=embed, view=final_view)
                    else:
                        err = await resp.json()
                        err_embed = error_embed(message=err.get('error', 'This ID is not valid for the selected role.'))
                        await inter.response.edit_message(content=None, embed=err_embed, view=None)

        async def profile_callback(user_data):
            if not user_id:
                # Self-lookup (using Discord ID for reliability)
                if not user_data.get('registered'):
                    return error_embed(message="**You** must be registered to view your own profile.")
                
                url = f"{BACKEND_URL}profiles/bot-detail/"
                params = {'discord_id': interaction.user.id}
                if interaction.guild_id:
                    params['guild_id'] = interaction.guild_id
                headers = {'X-Webhook-Token': WEBHOOK_SECRET}
                
                session = get_http_session()
                async with session.get(url, params=params, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            resp_role = data.get('active_role', user_data['active_role'])
                            return self.build_profile_embed(data, resp_role)
                        elif resp.status == 429:
                            err = await resp.json()
                            return throttled_embed(err.get('retry_after', 10))
                        else:
                            err = await resp.json()
                            return error_embed(message=err.get('error', 'Could not load your profile.'))

            result = resolve_user_id(user_id)
            if result.is_system:
                resolve_url = f"{BACKEND_URL}users/resolve-id/"
                packet = BotPacketFactory.create_packet(
                    packet_type="user_resolve_id",
                    data={'raw_id': result.normalized},
                    provider="bot"
                )
                headers = {'X-Webhook-Token': WEBHOOK_SECRET}
                
                session = get_http_session()
                async with session.post(resolve_url, json=packet.to_dict(), headers=headers) as resp:
                        if resp.status == 200:
                            res = await resp.json()
                            return await fetch_and_show(interaction, res['role'], res['canonical_id'])
                        else:
                            err = await resp.json()
                            return error_embed(message=err.get('error', 'No user found with that ID.'))
            else:
                # Premium ID (Show Role Selection)
                view = ProfileRoleView(result.normalized, premium_role_callback, user_data)
                view.author_id = interaction.user.id
                embed = create_embed(
                    title="Role Selection Required",
                    description=f"The ID **{result.original}** is a custom Premium ID. Please select the target role perspective:",
                    color=BrandColor.ACCENT
                )
                return embed, view

        await validate_and_respond(interaction, profile_callback)

    def build_profile_embed(self, data, role):
        is_premium = data.get('premium_tier') == 'premium'
        name = data.get('username', 'Unknown')
        pfp = data.get('discord_avatar')
        discord_name = data.get('discord_username', 'User')
        
        avatar_url = f"https://cdn.discordapp.com/avatars/{data.get('user_id', '')}/{pfp}.png" if pfp else None
        role_label = role.replace('_', ' ').title()
        
        premium_status = "Premium Tier Member" if is_premium else "Standard Tier Member"
        
        embed = create_embed(
            title=f"{role_label} Identity Profile",
            description=f"Identity details for **{name}** (@{discord_name})",
            color=BrandColor.PREMIUM if is_premium else BrandColor.PRIMARY,
            thumbnail=avatar_url
        )
        
        if role == 'freelancer':
            details = (
                f"> **Tier**: `{premium_status}`\n"
                f"> **Availability**: `{data.get('availability', 'Available')}`"
            )
        elif role == 'client':
            budget = data.get('min_project_budget', 0)
            details = (
                f"> **Tier**: `{premium_status}`\n"
                f"> **Min. Project Budget**: `${budget}`"
            )
        elif role == 'server_admin':
            count = data.get('managed_servers_count', 0)
            details = (
                f"> **Tier**: `{premium_status}`\n"
                f"> **Managed Guilds**: `{count} servers`"
            )
        else:
            details = f"> **Tier**: `{premium_status}`"
        
        embed.add_field(name="Profile Parameters", value=details, inline=False)
        embed.set_footer(text='Xentra •')
        return embed

async def setup(bot):
    await bot.add_cog(UserProfile(bot))