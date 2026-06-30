import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from utils.command_handler import validate_and_respond, sync_cog_commands
from utils.embeds import create_embed, BrandColor, error_embed, loading_embed

class HelpCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.commands_path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'commands.json')

    async def cog_load(self):
        sync_cog_commands(self)

    def load_commands(self):
        with open(self.commands_path, 'r') as f:
            return json.load(f)

    @app_commands.command(name="help", description="...")
    @app_commands.checks.cooldown(3, 10, key=lambda i: i.user.id)
    async def help(self, interaction: discord.Interaction):
        
        async def build_help_embed(user_data):
            active_role = user_data.get('active_role', 'non_bot_user')
            has_active_job_chat = user_data.get('has_active_job_chat', False)
            is_dm = interaction.guild is None
            context = "dm" if is_dm else "server"
            
            from utils.command_handler import load_commands_data
            all_commands = load_commands_data()
            filtered_commands = {}

            context_key = "dm" if is_dm else "server"
            for cmd in all_commands:
                if context not in cmd['context']: continue
                
                all_roles = cmd.get('roles', {})
                if isinstance(all_roles, dict):
                    target_roles = all_roles.get(context_key, [])
                else:
                    target_roles = all_roles
                
                # Rule: server_admin is treated as non_bot_user in DM for everything EXCEPT /register
                is_register = cmd['name'] == 'register'
                is_registered_user = active_role in ['freelancer', 'client', 'server_admin']
                
                if is_register and is_registered_user:
                    continue # Never show registration to people already in our system

                role_allowed = active_role in target_roles
                
                if not role_allowed: continue
                if cmd['requiresJobChat'] and not has_active_job_chat: continue

                cat = cmd['category']
                if cat not in filtered_commands: filtered_commands[cat] = []
                filtered_commands[cat].append(cmd)

            embed = create_embed(
                title="Xentra Help Center",
                description=f"Authorized slash commands for your active perspective: **{active_role.replace('_', ' ').title()}**",
                color=BrandColor.ACCENT
            )
            
            for category, cmds in filtered_commands.items():
                cmd_list = ", ".join([f"`/{c['name'].replace('_', ' ')}`" for c in cmds])
                embed.add_field(name=category, value=f"> {cmd_list}", inline=False)

            return embed

        await validate_and_respond(interaction, build_help_embed)

async def setup(bot):
    await bot.add_cog(HelpCommand(bot))