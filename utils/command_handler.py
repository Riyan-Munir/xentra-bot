import discord
import logging
import json
import os
import time
import asyncio
from config import BACKEND_URL, WEBHOOK_SECRET
from utils.embeds import BrandColor, error_embed
from utils.http import get_http_session

logger = logging.getLogger('bot.handler')

# ---------------------------------------------------------------------------
# Global rate-limit mitigations
# ---------------------------------------------------------------------------

# Semaphore that limits the number of *concurrent* backend lookups in
# validate_and_respond.  When the backend is slow (e.g. cold-start behind
# Cloudflare), a burst of parallel commands would otherwise open many TCP
# connections, keep them alive waiting, and quickly exhaust Discord's
# per-route / global rate-limit budget before the first response arrives.
#
# Value chosen to stay well under Discord's 50 req/s global limit while
# allowing reasonable throughput.
_COMMAND_SEMAPHORE = asyncio.Semaphore(5)

# Simple TTL cache for ``/users/bot/{id}/`` responses.
# The cache stores a tuple (timestamp, user_data_dict) keyed by user_id.
# TTL is intentionally short so that role/permission changes propagate quickly.
_USER_CACHE_TTL = 10  # seconds
_user_cache: dict[int, tuple[float, dict]] = {}


def _get_cached_user(user_id: int) -> dict | None:
    """Return cached user data, or None if not cached / expired."""
    entry = _user_cache.get(user_id)
    if entry is None:
        return None
    ts, data = entry
    if time.monotonic() - ts > _USER_CACHE_TTL:
        del _user_cache[user_id]
        return None
    return data


def _set_cached_user(user_id: int, data: dict) -> None:
    """Store user data in the short-lived cache."""
    _user_cache[user_id] = (time.monotonic(), data)


def _bust_user_cache(user_id: int) -> None:
    """Force-expire the cache entry for *user_id* (call after a mutation)."""
    _user_cache.pop(user_id, None)


# ---------------------------------------------------------------------------


def add_admin_post_button(view, embed, user_data):
    """
    General tracker/handler for adding the 'Post to Channel' button.
    Centralizes role checks and duplicate prevention.
    """
    active_role = user_data.get('active_role')
    assigned_channel_id = user_data.get('assigned_channel_id')

    # 1. Permission Check
    if active_role != 'server_admin' or not assigned_channel_id:
        return

    # 2. Error Check (Never post errors)
    is_error = embed.color and embed.color.value == BrandColor.ERROR.value
    if is_error:
        return

    # 3. Duplicate Check
    if any(isinstance(item, discord.ui.Button) and item.label == "Post to Channel 📢" for item in view.children):
        return

    # 4. Create and Add Button
    button = discord.ui.Button(label="Post to Channel 📢", style=discord.ButtonStyle.secondary)
    
    async def post_callback(interaction: discord.Interaction):
        channel = interaction.channel
        if assigned_channel_id:
            target = interaction.guild.get_channel(int(assigned_channel_id))
            if target: channel = target
        
        await interaction.response.edit_message(content=f"Posted publicly in {channel.mention}.", embed=None, view=None)
        await channel.send(embed=embed)
    
    button.callback = post_callback
    view.add_item(button)


class PublicPostView(discord.ui.View):
    """
    Common view for the 'Post to Channel' button.
    
    NOTE: This class is imported lazily in user_profile.py and user_stats.py
    (``from utils.command_handler import PublicPostView``) to avoid circular
    imports — those modules are imported by command_handler.py's own imports.
    The lazy import is intentional and should be preserved.
    """
    def __init__(self, embed, user_data):
        super().__init__(timeout=60)
        add_admin_post_button(self, embed, user_data)


async def fetch_selected_room(
    discord_id: int | str,
    role: str,
    room_type: str = 'interview',
    headers: dict | None = None,
) -> dict | None:
    """
    Fetch the user's currently selected room from the backend.

    Returns the room data dict if found, or None if not found or an error
    occurred.  Logs failures internally so callers don't need to duplicate
    logging.

    Shared resolver — use in any command that needs the user's currently
    selected interview or job room.
    """
    if headers is None:
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

    session = get_http_session()
    url = f'{BACKEND_URL}rooms/bot/selected-room/'
    params = {
        'discord_id': str(discord_id),
        'role': role,
        'room_type': room_type,
    }
    try:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.info(
                'fetch_selected_room returned %s for discord_id=%s role=%s',
                resp.status, discord_id, role,
            )
            return None
    except Exception:
        logger.exception(
            'Failed to fetch selected room for discord_id=%s role=%s',
            discord_id, role,
        )
        return None


COMMANDS_DATA = None

def load_commands_data():
    global COMMANDS_DATA
    if COMMANDS_DATA is None:
        path = os.path.join(os.path.dirname(__file__), '..', 'data', 'commands.json')
        with open(path, 'r') as f:
            COMMANDS_DATA = json.load(f)
    return COMMANDS_DATA

def check_command_roles(command_name: str, active_role: str, is_dm: bool = True) -> list[str]:
    """
    Load role restrictions for *command_name* from ``commands.json`` and return
    the list of allowed roles for the current context (DM / server).

    The caller is responsible for checking whether ``active_role`` is in the
    returned list and rejecting the user if it is not.

    Returns an empty list if the command is not found in commands.json
    (which means the command has no role restrictions).
    """
    data = load_commands_data()
    # commands.json uses space-separated names; Discord uses underscores.
    q_name = command_name.replace('_', ' ')
    cmd_meta = next((c for c in data if c['name'] == q_name), None)
    if not cmd_meta:
        return []
    all_roles = cmd_meta.get('roles', {})
    context_key = 'dm' if is_dm else 'server'
    if isinstance(all_roles, dict):
        return all_roles.get(context_key, [])
    return all_roles  # fallback — bare list


def _collect_all_commands(cmds):
    """
    Recursively walks an app_commands list, yielding every leaf Command
    (i.e. non-Group) with its full qualified_name intact.
    This correctly handles GroupCog trees where get_app_commands() returns
    nested Group objects whose subcommands must be reached recursively.
    """
    for cmd in cmds:
        if isinstance(cmd, discord.app_commands.Group):
            yield from _collect_all_commands(cmd.commands)
        else:
            yield cmd


def sync_cog_commands(cog):
    """
    Dynamically updates a Cog's slash command metadata (name, description, parameter descriptions)
    using commands.json as the source of truth.
    """
    data = load_commands_data()
    commands_map = {cmd['name']: cmd for cmd in data}

    # For a regular Cog, get_app_commands() returns all registered commands/groups.
    # For a GroupCog, the cog CLASS itself IS the Group, so get_app_commands() returns
    # an empty list — the subcommands live on cog.commands instead.
    # We handle both cases by building the source from get_app_commands() and also
    # including the cog itself if it is a Group (GroupCog pattern).
    sources = list(cog.get_app_commands())

    # commands.GroupCog does NOT make the cog itself an instance of
    # discord.app_commands.Group. Instead discord.py stores the internal Group
    # object under cog.__cog_app_commands_group__ and marks the class with
    # __cog_is_app_commands_group__ = True. We detect this and add the real
    # Group so _collect_all_commands can recurse into its subcommands.
    if getattr(cog, '__cog_is_app_commands_group__', False):
        group = getattr(cog, '__cog_app_commands_group__', None)
        if group is not None:
            sources.append(group)

    logger.info(f"[sync] {cog.__class__.__name__} | sources={[getattr(s,'name','?') for s in sources]}")

    for cmd in _collect_all_commands(sources):
        # Discord uses underscores in command names (e.g. "active_role").
        # commands.json uses space-separated names (e.g. "active role") for display.
        # Convert underscores to spaces to match JSON keys.
        q_name = cmd.qualified_name.replace('_', ' ')
        cmd_data = commands_map.get(q_name)
        logger.info(f"[sync]   -> q_name='{q_name}' match={'YES' if cmd_data else 'NO'}")

        if not cmd_data:
            logger.warning(
                f"[sync] ⚠️ Command '{q_name}' not found in commands.json! "
                f"Discord will display '...' as the description."
            )
            continue

        cmd.description = cmd_data.get('description', cmd.description)
        logger.info(f"[sync]      desc_after='{cmd.description}'")

        json_params = {p['name']: p['description'] for p in cmd_data.get('parameters', [])}

        for p_name, p_obj in cmd._params.items():
            if p_name in json_params:
                new_desc = json_params[p_name]
                try:
                    p_obj.description = new_desc
                except AttributeError:
                    if hasattr(p_obj, '_description'):
                        p_obj._description = new_desc

        # --- Context Restriction (Discord native UI filtering) ---
        # Map commands.json context values to discord.py AppCommandContext
        # "server" -> guild only, "dm" -> DM only, both -> unrestricted
        allowed_contexts = cmd_data.get('context', ['dm', 'server'])
        try:
            from discord.app_commands import AppCommandContext
            # AppCommandContext is a class, NOT an IntFlag.
            # It takes keyword bool args: guild, dm_channel, private_channel.
            # Setting cmd.allowed_contexts = None means "use defaults" (visible everywhere).
            kw = {}
            for c in allowed_contexts:
                if c == 'server':
                    kw['guild'] = True
                elif c == 'dm':
                    kw['dm_channel'] = True
            cmd.allowed_contexts = AppCommandContext(**kw) if kw else None

            # CRITICAL: to_dict() also serialises dm_permission = not self.guild_only.
            # If we only set allowed_contexts but leave guild_only=False, discord sends
            # dm_permission=True which OVERRIDES the contexts field and still shows the
            # command in DMs. We must sync guild_only with the context restrictions.
            # Server-only → guild_only=True → dm_permission=False
            # DM-only     → guild_only=False → dm_permission=True
            # Both        → guild_only=False → dm_permission=True
            is_server = 'server' in allowed_contexts
            is_dm     = 'dm' in allowed_contexts
            if is_server and not is_dm:
                cmd.guild_only = True
            elif not is_server and is_dm:
                cmd.guild_only = False
            # else both or neither: leave default (False)

            logger.info(f"[sync]      contexts={cmd.allowed_contexts} guild_only={cmd.guild_only}")
        except (ImportError, AttributeError, TypeError) as e:
            logger.warning(f"[sync]      AppCommandContext not available, skipping: {e}")


async def validate_and_respond(interaction, embed_builder_callback, required_roles=None, additional_params=None):
    """
    Centralized logic for all bot commands.
    Now supports dynamic metadata picking from commands.json.
    """
    user_id = interaction.user.id
    guild_id = interaction.guild_id
    is_dm = interaction.guild is None
    
    # Load dynamic metadata if available
    data = load_commands_data()
    # Discord uses underscores in command names; commands.json uses space-separated names.
    # Convert underscores to spaces to match JSON keys.
    q_name = interaction.command.qualified_name.replace('_', ' ')
    cmd_metadata = next((c for c in data if c['name'] == q_name), None)
    
    # 1.1 Context-Specific Role Extraction
    required_roles = required_roles or []
    if cmd_metadata:
        all_roles = cmd_metadata.get('roles', {})
        context_key = 'dm' if is_dm else 'server'
        
        if isinstance(all_roles, dict):
            required_roles = all_roles.get(context_key, [])
        else:
            required_roles = all_roles # Fallback
            
        requires_job_chat = cmd_metadata.get('requiresJobChat', False)
        requires_interview_chat = cmd_metadata.get('requiresInterviewChat', False)
    else:
        requires_job_chat = False
        requires_interview_chat = False
    
    # ── Defer the interaction (may fail if interaction expired or was already acknowledged) ──
    try:
        await interaction.response.defer(ephemeral=not is_dm)
    except discord.errors.NotFound:
        logger.warning(
            "Interaction %s expired or already acknowledged before defer (command=%s). "
            "This is usually caused by the 3-second interaction window expiring.",
            interaction.id, q_name,
        )
        return
    except Exception:
        logger.exception(
            "Unexpected error deferring interaction %s (command=%s)",
            interaction.id, q_name,
        )
        return

    # ── 1. Fetch User Data from Backend (with TTL cache) ─────────────
    # Check the in-memory cache first.  This avoids hitting the backend
    # (through Cloudflare) on every single command, reducing latency and
    # the risk of the 3-second interaction window expiring.
    user_data = _get_cached_user(user_id)

    if user_data is None:
        # ── Concurrency gate ───────────────────────────────────────
        # Acquire the semaphore BEFORE making the HTTP call to prevent
        # a burst of commands from opening N parallel connections to the
        # backend when it is slow (cold-start behind Cloudflare).
        #
        # Each concurrent backend call = 1 outstanding HTTP request that
        # adds latency and potential error cascades.  The semaphore keeps
        # the in-flight count low.
        async with _COMMAND_SEMAPHORE:
            url = f"{BACKEND_URL}users/bot/{user_id}/"
            params = {'guild_id': guild_id} if guild_id else {}
            
            # Rule: Trigger automatic guild sync for any interaction within a server
            if guild_id:
                params['should_sync'] = 'true'
                if interaction.guild:
                    # Check permissions for server admin role tracking
                    member = interaction.guild.get_member(user_id)
                    if member:
                        params['is_owner'] = 'true' if member.id == interaction.guild.owner_id else 'false'
                        params['is_mod'] = 'true' if (member.guild_permissions.manage_guild or member.guild_permissions.administrator) else 'false'
                
            if additional_params:
                params.update(additional_params)
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            
            try:
                session = get_http_session()
                async with session.get(url, params=params, headers=headers) as resp:
                        if resp.status == 200:
                            user_data = await resp.json()
                            _set_cached_user(user_id, user_data)
                        elif resp.status == 429:
                            # Handle Throttling
                            err_data = await resp.json()
                            from utils.embeds import throttled_embed
                            wait_time = err_data.get('retry_after', 10)
                            await interaction.followup.send(embed=throttled_embed(wait_time), ephemeral=True)
                            return
                        elif resp.status == 403:
                            # Distinguish between account ban and pending hacking alert
                            from config import FRONTEND_URL
                            try:
                                err_data = await resp.json()
                            except Exception:
                                err_data = {}

                            if err_data.get('require_dismiss'):
                                err = error_embed(
                                    "**Security Alert Active**\n\n"
                                    "A security notification is waiting for you on the Xentra Dashboard. "
                                    f"Please visit [{FRONTEND_URL}]({FRONTEND_URL}) and acknowledge it "
                                    "before using any bot commands."
                                )
                            else:
                                err = error_embed(
                                    "**Account Suspended**\n\n"
                                    "Your account has been **automatically suspended** due to "
                                    "repeated security violations detected by our systems.\n\n"
                                    f"Contact a server administrator or visit "
                                    f"[Xentra Dashboard]({FRONTEND_URL}) to appeal the suspension."
                                )
                            await interaction.followup.send(embed=err, ephemeral=True)
                            return
                        else:
                            err_text = await resp.text()
                            logger.warning(f"Backend returned {resp.status} for user lookup: {err_text[:200]}")
                            await interaction.followup.send(
                                embed=error_embed(
                                    "The backend service returned an error. "
                                    "Please try again later or contact support."
                                ),
                                ephemeral=True,
                            )
                            return
            except Exception as e:
                logger.error(f"Backend error: {e}")
                await interaction.followup.send(
                    embed=error_embed(
                        "Unable to reach the backend service right now. "
                        "Please try again later."
                    ),
                    ephemeral=True,
                )
                return

    # At this point user_data must be valid
    active_role = user_data.get('active_role', 'non_bot_user')
    assigned_channel_id = user_data.get('assigned_channel_id')
    has_active_job_chat = user_data.get('has_active_job_chat', False)
    has_active_interview_chat = user_data.get('has_active_interview_chat', False)

    # ── Hacking alert enforcement ─────────────────────────────────────────
    # Mirror SecurityEnforcementMiddleware: if the user has an unseen hacking
    # notification, block ALL bot commands until they dismiss it on the dashboard.
    if user_data.get('has_pending_hacking'):
        from config import FRONTEND_URL
        err = error_embed(
            "**Security Alert — Commands Locked**\n\n"
            "A security notification requires your attention on the Xentra Dashboard.\n"
            f"Visit **{FRONTEND_URL}** and acknowledge the alert "
            "to restore access to all bot commands."
        )
        await interaction.followup.send(embed=err, ephemeral=True)
        return
    # ─────────────────────────────────────────────────────────────────────

    # Universal executor allowance check (backend resolver)
    if not user_data.get('is_allowed_executor', True):
        err = error_embed("You are not permitted to execute commands in this server. Contact moderators for more information.")
        await interaction.followup.send(embed=err, ephemeral=True)
        return

    # 1.5. Context Validation Check (Server vs DM) - Early check before send_response is defined
    if cmd_metadata:
        allowed_contexts = cmd_metadata.get('context', [])
        current_context = 'dm' if is_dm else 'server'
        
        if allowed_contexts and current_context not in allowed_contexts:
            if current_context == 'dm':
                err = error_embed("This command is only available in Server.")
            else:
                err = error_embed("This command is only available in DM.")
            await interaction.followup.send(embed=err, ephemeral=True)
            return

    # 1.6. Job Chat Requirement Enforcement
    if requires_job_chat and not has_active_job_chat:
        err = error_embed("This command requires an active job chat session.")
        await interaction.followup.send(embed=err, ephemeral=True)
        return

    # 1.7. Interview Chat Requirement Enforcement
    if requires_interview_chat and not has_active_interview_chat:
        err = error_embed("This command requires an active interview chat session.")
        await interaction.followup.send(embed=err, ephemeral=True)
        return

    async def send_response(embed):
        """Internal helper to send response with or without admin button."""
        view = PublicPostView(embed, user_data)
        has_items = len(view.children) > 0
            
        if has_items:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=not is_dm)
    
    # 2. Channel Restriction Check (Server Only)
    if not is_dm:
        # If the command being run is `/command channel`, bypass the restriction so they can set it
        if q_name != "command channel":
            if not assigned_channel_id:
                # Channel is not set
                msg = "Command Channel is not set for this Server."
                if active_role == 'server_admin' and user_data.get('is_guild_admin'):
                    msg += " Run `/command channel` to set a Channel for Command execution."
                
                err = error_embed(msg)
                await send_response(err)
                return
            else:
                # Channel is set, enforce restriction
                if str(interaction.channel_id) != str(assigned_channel_id):
                    target_channel = interaction.guild.get_channel(int(assigned_channel_id))
                    channel_name = target_channel.mention if target_channel else f"ID: {assigned_channel_id}"
                    
                    err = error_embed(
                        f"Commands are restricted to {channel_name}."
                    )
                    await send_response(err)
                    return
 
    # 3. Role Validation
    role_match = active_role in required_roles
    
    # Strict administrative verification (Only enforced within a guild context).
    # Only block server_admins who lack guild admin permissions when the command
    # does NOT explicitly allow `server_admin`.  If the command lists `server_admin`
    # as an allowed role, skip the strict guild admin check — role_match below
    # handles permission verification.
    if (
        active_role == 'server_admin'
        and interaction.guild
        and not user_data.get('is_guild_admin')
        and 'server_admin' not in required_roles
    ):
        err = error_embed("This command is not available for your role. Run `/help` for details.")
        await send_response(err)
        return

    if not role_match:
        err = error_embed(
            "This command is not available for your role. Run `/help` for details."
        )
        await send_response(err)
        return

    # 4. Generate the actual Command Content
    result = await embed_builder_callback(user_data)
    
    # Handle both (embed, custom_view) and just embed
    if isinstance(result, tuple):
        result_embed, custom_view = result
    else:
        result_embed, custom_view = result, None
    
    # 5. Admin "Post to Channel" Logic
    final_view = custom_view
    if final_view:
        add_admin_post_button(final_view, result_embed, user_data)
    else:
        # Check if we should create a PublicPostView (which handles its own check now)
        temp_view = PublicPostView(result_embed, user_data)
        if len(temp_view.children) > 0:
            final_view = temp_view
    
    # 6. Send Response
    if final_view:
        await interaction.followup.send(embed=result_embed, view=final_view, ephemeral=True)
    else:
        await interaction.followup.send(embed=result_embed, ephemeral=not is_dm)


# ---------------------------------------------------------------------------
# Concurrency Helpers — View-level process isolation
# ---------------------------------------------------------------------------


def is_author(interaction: discord.Interaction, view: discord.ui.View) -> bool:
    """Verify that the interaction user matches the view's stored author_id.

    Every View that receives button/select callbacks MUST store
    ``self.author_id = interaction.user.id`` **after construction**
    (usually in the command callback, right after ``view = MyView(...)``).

    Call this **first thing** in every button/dropdown callback to reject
    interactions from other users when the same View instance is shared:

    .. code:: python

        async def my_callback(self, interaction, ...):
            if not is_author(interaction, self):
                return
            ...

    Returns ``True`` if the user is authorised to interact, ``False`` otherwise.
    """
    author_id = getattr(view, 'author_id', None)
    return author_id is not None and interaction.user.id == author_id


def is_done(view: discord.ui.View) -> bool:
    """Check-and-set the ``_done`` flag to prevent double-submit.

    Views that carry a ``_done`` flag should call this at the top of any
    callback that must execute at most once (Send, Confirm, Submit, etc.).

    Usage::

        if is_done(self):
            return
        # ... proceed with one-shot action

    Returns ``True`` if the action has already been performed (caller should
    return early), ``False`` otherwise (flag has been atomically set).
    """
    if getattr(view, '_done', False):
        return True
    setattr(view, '_done', True)
    return False
