import discord
from discord.ext import commands
from discord import app_commands
from utils.http import get_http_session
import logging
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import validate_and_respond, sync_cog_commands, is_author
from utils.embeds import success_embed, create_embed, BrandColor, error_embed, info_embed, loading_embed
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('bot.profile.switch_role')

class SwitchRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        sync_cog_commands(self)

    @app_commands.command(name="switch_role", description="...")
    @app_commands.checks.cooldown(1, 20, key=lambda i: i.user.id)
    async def switch_role(self, interaction: discord.Interaction):
        
        async def build_switch_ui(user_data):
            roles = user_data.get('available_roles', [])
            current = user_data.get('active_role')
            
            if not roles:
                return error_embed(message="You don't have any roles to switch to.")
            
            embed = create_embed(
                title="Identity Switcher",
                description="Select a role to update your dashboard permissions.",
                color=BrandColor.ACCENT
            )
            
            current_display = f"**{current.replace('_', ' ').title()}**"
            
            embed.add_field(name="Active Perspective", value=current_display, inline=False)
            embed.set_footer(text='Xentra •')
            
            view = SwitchRoleView(interaction.user.id, roles, current)
            view.author_id = interaction.user.id
            return embed, view
        
        await validate_and_respond(interaction, build_switch_ui)

class SwitchRoleView(discord.ui.View):
    def __init__(self, user_id, roles, current_role):
        super().__init__(timeout=60)
        self.author_id: int | None = None
        self.user_id = user_id
        self.selected_role = current_role
        self.add_item(RoleSelect(roles, current_role))

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label="Update Role", style=discord.ButtonStyle.green)
    async def update_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        url = f"{BACKEND_URL}users/bot/{self.user_id}/"
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        packet = BotPacketFactory.create_packet(
            packet_type="user_update_role",
            data={'role': self.selected_role},
            provider="bot"
        )
        
        try:
            session = get_http_session()
            async with session.post(url, json=packet.to_dict(), headers=headers) as resp:
                    if resp.status == 200:
                        embed = success_embed(
                            message=f"Active role switched to **{self.selected_role.replace('_', ' ').title()}**."
                        )
                        await interaction.response.edit_message(embed=embed, view=None)
                    else:
                        error_data = await resp.json()
                        err = error_embed(message=error_data.get('error', 'Failed to update role.'))
                        await interaction.followup.send(embed=err, ephemeral=True)
        except Exception as e:
            logger.error(f"Error updating role: {e}")
            err = error_embed(message="Something went wrong. Please try again.")
            await interaction.followup.send(embed=err, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_author(interaction, self):
            return
        self.stop()
        err = info_embed(message="Identity switch cancelled.")
        await interaction.response.edit_message(content=None, embed=err, view=None)

class RoleSelect(discord.ui.Select):
    def __init__(self, roles, current_role):
        options = []
        role_data = {
            'freelancer': {'label': 'Freelancer', 'desc': 'Manage your services and projects'},
            'client': {'label': 'Client', 'desc': 'Post jobs and hire professionals'},
            'server_admin': {'label': 'Server Admin', 'desc': 'Manage guild settings and users'}
        }
        
        for r in roles:
            data = role_data.get(r, {'label': r.title(), 'desc': f'Switch to {r}'})
            options.append(discord.SelectOption(
                label=data['label'],
                value=r,
                description=data['desc'],
                default=(r == current_role)
            ))
        
        super().__init__(
            placeholder="Select Role",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        if not is_author(interaction, self.view):
            return
        self.view.selected_role = self.values[0]
        await interaction.response.defer()

async def setup(bot):
    await bot.add_cog(SwitchRole(bot))