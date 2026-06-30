"""
``/interview message`` — Send a message in the interview chat.

Flow:
  1. Command handler opens the message Modal directly — no intermediate
     "Write Message" button block.
  2. Modal collects text; on submit it fetches user + room data from the
     backend and shows a confirmation embed with buttons.
  3. Confirmation is **not** ephemeral in DMs.
  4. On Send, the *same* embed is edited to show success/error and all
     buttons are removed (instead of sending a new followup message).
  5. **➕** button to add files, per-attachment **✖** buttons to remove before sending.
"""

import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import sync_cog_commands
from utils.embeds import (
    BrandColor,
    create_embed,
    error_embed,
    success_embed,
    info_embed,
    loading_embed,
    dm_blocked_embed,
)
from utils.http import get_http_session
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery

logger = logging.getLogger('bot.rooms.interview_message')

MAX_ATTACHMENTS = 10
MAX_TOTAL_SIZE_MB = 10
MAX_TOTAL_SIZE_BYTES = MAX_TOTAL_SIZE_MB * 1024 * 1024
ALLOWED_EXTENSIONS = {'.zip', '.ppt', '.pptx', '.pdf', '.doc', '.docx'}


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _sanitise_filename(filename: str) -> str:
    """Strip path separators and replace dangerous characters."""
    safe = re.sub(r'[^\w.\-() ]', '_', filename)
    if len(safe) > 100:
        name, dot, ext = safe.rpartition('.')
        safe = name[:95] + dot + ext
    return safe


def _build_confirm_embed(
    msg_text: str,
    attachments: list,
    word_count: int,
) -> discord.Embed:
    """Build the confirmation embed showing message preview + attachments."""
    desc = (
        f'**Message preview:**\n{msg_text[:500]}'
        f'{"…" if len(msg_text) > 500 else ""}\n\n'
        f'**Word count:** `{word_count}` / 1000  |  '
        f'**Attachments:** `{len(attachments)}`\n'
    )
    if attachments:
        names = ', '.join(_sanitise_filename(a.filename) for a in attachments)
        desc += f'> {names}\n'
    desc += '\nClick **Attach** to upload files. Click **Remove** next to a file to remove it.'

    return create_embed(
        title='Interview Message',
        description=desc,
        color=BrandColor.PRIMARY,
        footer='Xentra • Room System',
    )


# ──────────────────────────────────────────────────────────────────────
# Modal — message text input (opens directly on command)
# ──────────────────────────────────────────────────────────────────────


class InterviewMessageModal(discord.ui.Modal, title='Send Interview Message'):
    """Modal that collects message text.  Opens immediately on /interview message."""

    msg = discord.ui.TextInput(
        label='Message',
        style=discord.TextStyle.paragraph,
        placeholder='Type your message here… (max 1000 words)',
        required=True,
        max_length=4000,
    )

    def __init__(self) -> None:
        super().__init__(timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        msg_text = self.msg.value.strip()
        word_count = len(msg_text.split()) if msg_text else 0

        if word_count > 1000:
            await interaction.response.send_message(
                embed=error_embed(
                    message=f'Message exceeds 1000 words ({word_count} words). Please shorten it.'
                ),
                ephemeral=True,
            )
            return

        is_dm = interaction.guild is None
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        # 1 Fetch user data
        try:
            async with session.get(
                f'{BACKEND_URL}users/bot/{interaction.user.id}/',
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    await interaction.response.send_message(
                        embed=error_embed(message='Authentication failed. Please try again.'),
                        ephemeral=not is_dm,
                    )
                    return
                user_data = await resp.json()
        except Exception:
            logger.exception('Failed to fetch user data')
            await interaction.response.send_message(
                embed=error_embed(message='System error. Please try again later.'),
                ephemeral=not is_dm,
            )
            return

        # 2 Security checks (mirrors validate_and_respond logic)
        if user_data.get('has_pending_hacking'):
            from config import FRONTEND_URL
            await interaction.response.send_message(
                embed=error_embed(
                    message=(
                        f'A security notification requires your attention on the Xentra '
                        f'Dashboard.\n'
                        f'Visit **{FRONTEND_URL}** and acknowledge the alert '
                        f'to restore access to all bot commands.'
                    ),
                ),
                ephemeral=not is_dm,
            )
            return

        if not user_data.get('is_allowed_executor', True):
            await interaction.response.send_message(
                embed=error_embed(
                    message='You are not permitted to execute commands in this server. '
                    'Contact moderators for more information.'
                ),
                ephemeral=not is_dm,
            )
            return

        active_role = user_data.get('active_role', '')
        if not active_role or active_role == 'non_bot_user':
            await interaction.response.send_message(
                embed=error_embed(
                    message='You need a registered client or freelancer profile to send '
                    'interview messages. Visit the Xentra Dashboard to set up your account.'
                ),
                ephemeral=not is_dm,
            )
            return

        # 3 Channel restriction check (server only)
        if not is_dm:
            assigned_channel_id = user_data.get('assigned_channel_id')
            if assigned_channel_id and str(interaction.channel_id) != str(assigned_channel_id):
                target = interaction.guild.get_channel(int(assigned_channel_id))
                ch_name = target.mention if target else f'ID {assigned_channel_id}'
                await interaction.response.send_message(
                    embed=error_embed(
                        message=f'Commands are restricted to {ch_name}.'
                    ),
                    ephemeral=not is_dm,
                )
                return

        # 4 Fetch selected room via shared resolver
        from utils.command_handler import fetch_selected_room

        room_data = await fetch_selected_room(
            discord_id=interaction.user.id,
            role=active_role,
            room_type='interview',
            headers=headers,
        )

        if room_data is None:
            await interaction.response.send_message(
                embed=error_embed(
                    message='No selected interview room found. '
                    'Use `/switch room` to select one.',
                ),
                ephemeral=not is_dm,
            )
            return

        # Merge profile display name into user_data
        if active_role == 'client':
            user_data['client_name'] = room_data.get('client_name', 'Client')
        else:
            user_data['freelancer_name'] = room_data.get('freelancer_name', 'Freelancer')

        # 5 Show confirmation view
        view = InterviewMessageConfirmView(
            interaction,
            user_data,
            room_data,
            msg_text,
            word_count,
        )

        embed = _build_confirm_embed(msg_text, view.attachments, word_count)

        # In DMs: visible (not ephemeral).  In servers: ephemeral to avoid clutter.
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=not is_dm,
        )


# ──────────────────────────────────────────────────────────────────────
# Confirm / Send / Cancel view  (with file management)
# ──────────────────────────────────────────────────────────────────────


class InterviewMessageConfirmView(discord.ui.View):
    """Confirmation view: Attach / per-attachment Remove buttons / Send / Cancel.

    Row 0: Attach button to add files.
    Row 1: Dynamic Remove {filename} buttons — one per attached file.
    Row 2: Send / Cancel.

    When no files are attached, no Remove buttons appear.
    On Send, the same embed is edited to a success/error state
    and every button is removed.
    """

    def __init__(
        self,
        modal_interaction: discord.Interaction,
        user_data: dict,
        room_data: dict,
        msg_text: str,
        word_count: int,
    ) -> None:
        super().__init__(timeout=300)
        self.author_id = modal_interaction.user.id
        self.is_dm = modal_interaction.guild is None
        self.user_data = user_data
        self.room_data = room_data
        self.msg_text = msg_text
        self.word_count = word_count
        self.attachments: list[discord.Attachment] = []
        self._original_interaction = modal_interaction  # for editing the confirm embed
        self._done = False
        # Track the visible instruction message so we can delete it after
        self._instruction_msg: discord.Message | None = None

    async def on_timeout(self) -> None:
        self.stop()

    # ── helpers ──────────────────────────────────────────────────────

    async def _refresh_embed(self) -> None:
        """Rebuild remove buttons and edit embed + view to reflect current state."""
        self._rebuild_remove_buttons()
        embed = _build_confirm_embed(self.msg_text, self.attachments, self.word_count)
        try:
            await self._original_interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            pass

    async def _toggle_add_files_button(self, disabled: bool, label: str) -> None:
        """Enable/disable the ➕ button and update its label in-place."""
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label in (
                'Attach', 'Wait...',
            ):
                child.disabled = disabled
                child.label = label
                break
        try:
            await self._original_interaction.edit_original_response(view=self)
        except Exception:
            pass

    async def _cleanup_instruction(self) -> None:
        """Delete the visible instruction message if it exists."""
        if self._instruction_msg is not None:
            try:
                await self._instruction_msg.delete()
            except Exception:
                pass
            self._instruction_msg = None

    # ── buttons ──────────────────────────────────────────────────────

    @discord.ui.button(label='Attach', style=discord.ButtonStyle.secondary, row=0)
    async def add_files(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.author_id:
            return

        # ── 1. Disable button & show "Wait..." state ───────────────
        await self._toggle_add_files_button(disabled=True, label='⏳')

        # ── 2. Post a visible (non-ephemeral) instruction embed ──────
        self._instruction_msg = await interaction.channel.send(
            embed=info_embed(
                title='File Upload',
                message='Drag & drop your files here or use the Discord attachment button.\n\n'
                f'Allowed: **{", ".join(sorted(ALLOWED_EXTENSIONS))}**\n'
                f'Max **{MAX_ATTACHMENTS}** files · Under **{MAX_TOTAL_SIZE_MB} MB** total\n\n'
                '*(This message will auto-delete after capture)*',
            ),
        )

        # Acknowledge the button press (no ephemeral followup needed)
        await interaction.response.defer()

        # ── 3. Wait for the user's file message ──────────────────────
        def check(msg: discord.Message) -> bool:
            return (
                msg.author.id == self.author_id
                and msg.channel.id == interaction.channel_id
                and len(msg.attachments) > 0
            )

        try:
            file_msg: discord.Message = await interaction.client.wait_for(
                'message', timeout=120.0, check=check,
            )
        except asyncio.TimeoutError:
            await self._cleanup_instruction()
            await self._toggle_add_files_button(disabled=False, label='Attach')
            await interaction.followup.send(
                embed=error_embed(
                    message='File upload timed out. Press **Attach** again to retry.'
                ),
                ephemeral=True,
            )
            return

        # ── 4. Validate count ────────────────────────────────────────
        combined = self.attachments + file_msg.attachments
        if len(combined) > MAX_ATTACHMENTS:
            await self._cleanup_instruction()
            await self._toggle_add_files_button(disabled=False, label='Attach')
            await interaction.followup.send(
                embed=error_embed(
                    message=f'Too many files (max {MAX_ATTACHMENTS}). '
                    f'You tried to add {len(file_msg.attachments)} but only '
                    f'{MAX_ATTACHMENTS - len(self.attachments)} slot(s) remain.'
                ),
                ephemeral=True,
            )
            return

        # ── 5. Validate total size ───────────────────────────────────
        total_size = sum(a.size for a in combined)
        if total_size > MAX_TOTAL_SIZE_BYTES:
            await self._cleanup_instruction()
            await self._toggle_add_files_button(disabled=False, label='Attach')
            await interaction.followup.send(
                embed=error_embed(
                    message=f'Combined file size exceeds {MAX_TOTAL_SIZE_MB} MB '
                    f'({total_size / (1024 * 1024):.1f} MB). '
                    f'Please select smaller files.'
                ),
                ephemeral=True,
            )
            return

        # ── 6. Accept files ──────────────────────────────────────────
        self.attachments.extend(file_msg.attachments)

        # Delete the user's file message + the instruction embed
        try:
            await file_msg.delete()
        except Exception:
            pass
        await self._cleanup_instruction()

        # Re-enable the ➕ button locally (will be sent with _refresh_embed)
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == 'Wait...':
                child.disabled = False
                child.label = 'Attach'
                break

        # Refresh embed + rebuild remove buttons in one edit
        await self._refresh_embed()

        await interaction.followup.send(
            embed=success_embed(
                message=f'Added {len(file_msg.attachments)} file(s).'
            ),
            ephemeral=True,
        )

    # ── per-attachment remove buttons ────────────────────────────────

    def _rebuild_remove_buttons(self) -> None:
        """Rebuild per-attachment Remove buttons based on current attachments."""
        # Remove existing dynamic remove buttons
        to_remove = []
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id and child.custom_id.startswith('rm_'):
                to_remove.append(child)
        for child in to_remove:
            self.remove_item(child)

        # Add a Remove button for each attachment
        for idx, a in enumerate(self.attachments):
            label = _sanitise_filename(a.filename)
            if len(label) > 40:
                label = label[:37] + '...'
            button = discord.ui.Button(
                label=f'Remove {label}',
                style=discord.ButtonStyle.primary,
                row=1,
                custom_id=f'rm_{idx}',
            )

            async def _remove_callback(interaction: discord.Interaction, i=idx, attach=a) -> None:
                if interaction.user.id != self.author_id:
                    return
                self.attachments.pop(i)
                await self._refresh_embed()
                await interaction.response.send_message(
                    embed=success_embed(
                        message=f'Removed **{_sanitise_filename(attach.filename)}**.'
                    ),
                    ephemeral=True,
                )

            button.callback = _remove_callback
            self.add_item(button)

    @discord.ui.button(label='Send', style=discord.ButtonStyle.success, row=2)
    async def send_msg(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.author_id:
            return
        if self._done:
            return
        self._done = True

        await interaction.response.defer(ephemeral=not self.is_dm)
        await self._do_send(interaction)

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger, row=2)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.author_id:
            return
        self._done = True
        await _edit_msg_done(self, info_embed(message='Message cancelled.'))
        self.stop()

    # ── core send logic ──────────────────────────────────────────────

    async def _do_send(self, interaction: discord.Interaction) -> None:
        room = self.room_data
        role: str = self.user_data.get('active_role', '')
        my_discord_id = str(interaction.user.id)

        # Determine receiver's discord_id
        if room.get('client_discord_id') == my_discord_id:
            receiver_discord_id = room.get('freelancer_discord_id')
        else:
            receiver_discord_id = room.get('client_discord_id')

        if not receiver_discord_id:
            await _edit_msg_done(
                self,
                error_embed(message='Could not determine the receiver. Please try again.'),
            )
            self.stop()
            return

        # Build attachment metadata string
        attachment_metadata = ''
        if self.attachments:
            file_names = [_sanitise_filename(a.filename) for a in self.attachments]
            attachment_metadata = (
                f'Shared {len(self.attachments)} file(s) '
                f'({", ".join(file_names)})'
            )

        # Sender display name (profile display name, NOT discord username)
        sender_name = (
            self.user_data.get('client_name')
            or self.user_data.get('freelancer_name')
            or interaction.user.display_name
        )

        # ── 1. Save to backend FIRST to get msg_id ───────────────────
        session = get_http_session()
        save_url = f'{BACKEND_URL}rooms/bot/save-message/'
        payload = {
            'discord_id': my_discord_id,
            'role': role,
            'room_id': room.get('room_id', ''),
            'msg_data': self.msg_text,
            'attachment_metadata': attachment_metadata,
        }
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        try:
            async with session.post(save_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    err_data = await resp.json()
                    err_msg = err_data.get('error', 'Unknown error')
                    await _edit_msg_done(
                        self,
                        error_embed(message=f'Failed to save message: {err_msg}'),
                    )
                    self.stop()
                    return
                save_data = await resp.json()
                msg_id = save_data.get('msg_id', 'N/A')
        except Exception:
            logger.exception('Failed to save message to backend')
            await _edit_msg_done(
                self,
                error_embed(
                    message='Could not save the message due to a system error. '
                    'Please try again later.',
                ),
            )
            self.stop()
            return

        # ── 2. Build system data with msg_id included ────────────────
        system_data = {
            'discord_id': receiver_discord_id,
            'room_id': room.get('room_id', ''),
            'job_title': room.get('job_title', ''),
            'sender_role': role,
            'sender_name': sender_name,
            'msg_id': msg_id,
            'msg_text': self.msg_text,
            'attachments': ', '.join(
                _sanitise_filename(a.filename) for a in self.attachments
            )
            if self.attachments
            else '',
        }

        # Convert attachments to discord.File objects for the DM
        discord_files: list[discord.File] = []
        for a in self.attachments:
            try:
                discord_files.append(await a.to_file())
            except Exception:
                logger.exception("Failed to convert attachment %s", a.filename)

        # ── 3. Deliver via system message handler ────────────────────
        delivery_ok = await handle_system_message(
            message_type='room_interview_message',
            data=system_data,
            bot=interaction.client,
            files=discord_files or None,
        )

        # Determine receiver name for the response message
        if role == 'client':
            receiver_name = room.get('freelancer_name', 'Freelancer')
        else:
            receiver_name = room.get('client_name', 'Client')

        if not delivery_ok:
            # Message saved but DM delivery failed — log for retry
            await log_failed_delivery(
                room_id=room.get('room_id', ''),
                message_type='interview_message',
                target_discord_id=receiver_discord_id,
                msg_id=msg_id,
            )

            await _edit_msg_done(
                self,
                dm_blocked_embed(
                    attempted_action="your interview message",
                    receiver_name=receiver_name,
                ),
            )
            self.stop()
            return

        # ── 4. Success ───────────────────────────────────────────────
        await _edit_msg_done(
            self,
            success_embed(
                message=f'Message sent to **{receiver_name}** in room '
                f'`{room.get("room_id", "")}` for job '
                f'**{room.get("job_title", "")}**. '
                f'(ID: `{msg_id}`)',
            ),
        )

        self.stop()


# ── helper: edit confirmation embed to final state ───────────────────


async def _edit_msg_done(
    view: discord.ui.View,
    embed: discord.Embed,
) -> None:
    """Remove all buttons from *view* and edit the original confirmation embed.

    This is used after Send or Cancel so that the same message shows the
    result instead of sending a brand-new followup.
    """
    for child in view.children.copy():
        view.remove_item(child)
    view.stop()
    try:
        await view._original_interaction.edit_original_response(
            embed=embed, view=view,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────


class InterviewMessage(commands.Cog):
    """``/interview message`` — Send a message in the interview chat."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(
        name='interview_message',
        description='...',
    )
    @app_commands.checks.cooldown(1, 15, key=lambda i: i.user.id)
    async def interview_message(self, interaction: discord.Interaction) -> None:
        """Send a message to the other party in the selected interview room."""
        modal = InterviewMessageModal()
        await interaction.response.send_modal(modal)


# ── setup ──────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewMessage(bot))
    logger.info('InterviewMessage cog loaded')
