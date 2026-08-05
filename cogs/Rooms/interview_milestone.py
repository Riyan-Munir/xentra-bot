"""
``/interview milestone``, Freelancer manages job milestones in the interview room.

Flow:
  1. Command handler validates role (freelancer only), fetches selected room.
  2. Calls backend to check agreement has final_budget set.
  3. CASE A, no milestones exist:
       a. Count modal (1-10) → sequential milestone form modals.
       b. After last milestone, batch-save to backend, notify client.
  4. CASE B, milestones exist:
       a. Action dropdown: Add / Edit / Delete.
       b. Add  → form modal (inline, checks max 10).
       c. Edit → milestone-select dropdown → pre-filled edit modal.
       d. Delete → milestone-select dropdown → confirmation → backend archive + re-order.
  5. On validation error the form re-opens with pre-filled data (keeps command alive).
"""

import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import sync_cog_commands, validate_and_respond, is_author
from utils.embeds import (
    BrandColor,
    create_embed,
    error_embed,
    success_embed,
    info_embed,
)
from utils.http import get_http_session
from ._shared import record_and_notify

logger = logging.getLogger('bot.rooms.interview_milestone')


# ── Helpers ─────────────────────────────────────────────────────────────


def _parse_deadline(value: str) -> str | None:
    """Try to parse a deadline string (YYYY-MM-DD or ISO 8601). Return ISO
    format or None if empty."""
    stripped = value.strip()
    if not stripped:
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            dt = datetime.strptime(stripped, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return stripped


# ── Count Modal (CASE A) ────────────────────────────────────────────────


class InterviewMilestoneCountModal(discord.ui.Modal, title='Milestone Count'):
    """Modal to ask how many milestones the freelancer wants to create (1-10)."""

    count = discord.ui.TextInput(
        label='Number of Milestones',
        placeholder='Enter a number between 1 and 10',
        max_length=2,
        required=True,
    )

    def __init__(self, room_data: dict, interaction: discord.Interaction) -> None:
        super().__init__(timeout=300)
        self.room_data = room_data
        self.origin_interaction = interaction

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.count.value.strip()

        try:
            total = int(raw)
        except (ValueError, TypeError):
            # Re-open the same modal via retry button
            view = _retry_view(InterviewMilestoneCountModal, {'room_data': self.room_data, 'interaction': self.origin_interaction})
            await interaction.response.send_message(
                embed=error_embed(message='Enter a valid milestone count.'),
                view=view,
                ephemeral=True,
            )
            return

        if total < 1 or total > 10:
            view = _retry_view(InterviewMilestoneCountModal, {'room_data': self.room_data, 'interaction': self.origin_interaction})
            await interaction.response.send_message(
                embed=error_embed(message='Enter a valid milestone count.'),
                view=view,
                ephemeral=True,
            )
            return

        # Open the first milestone form modal via continue button
        # (Discord does not allow send_modal() from within modal.on_submit())
        view = _retry_view(
            InterviewMilestoneFormModal,
            {
                'milestone_num': 1,
                'total_count': total,
                'accumulated': [],
                'room_data': self.room_data,
            },
            label='Continue',
        )
        await interaction.response.edit_message(
            embed=info_embed(
                message=f'Creating **{total}** milestone(s). Click **Continue** to start.',
            ),
            view=view,
        )


# ── Milestone Form Modal (CASE A + Add) ─────────────────────────────────


class InterviewMilestoneFormModal(discord.ui.Modal):
    """Modal for a single milestone's fields.  Shown sequentially for CASE A
    or standalone for Add (CASE B)."""

    def __init__(
        self,
        milestone_num: int,
        total_count: int,
        accumulated: list[dict],
        room_data: dict,
        prefill: dict | None = None,
    ) -> None:
        title_str = f'Milestone {milestone_num} of {total_count}' if total_count > 1 else 'Add Milestone'
        super().__init__(title=title_str, timeout=300)
        self.milestone_num = milestone_num
        self.total_count = total_count
        self.accumulated = accumulated
        self.room_data = room_data

        self.title_inp = discord.ui.TextInput(
            label='Title',
            placeholder='Max 64 characters',
            max_length=64,
            required=True,
            default=prefill.get('title', '') if prefill else '',
        )
        self.desc_inp = discord.ui.TextInput(
            label='Description',
            style=discord.TextStyle.paragraph,
            placeholder='30-600 words describing this milestone',
            max_length=4000,
            required=True,
            default=prefill.get('description', '') if prefill else '',
        )
        self.budget_inp = discord.ui.TextInput(
            label='Budget ($)',
            placeholder='e.g. 500.00',
            max_length=10,
            required=True,
            default=prefill.get('budget', '') if prefill else '',
        )
        self.deadline_inp = discord.ui.TextInput(
            label='Deadline',
            placeholder='YYYY-MM-DD or ISO format (optional)',
            max_length=30,
            required=False,
            default=prefill.get('deadline', '') if prefill else '',
        )

        self.add_item(self.title_inp)
        self.add_item(self.desc_inp)
        self.add_item(self.budget_inp)
        self.add_item(self.deadline_inp)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = self.title_inp.value.strip()
        description = self.desc_inp.value.strip()
        raw_budget = self.budget_inp.value.strip()
        raw_deadline = self.deadline_inp.value.strip()

        # --- validate title ---
        if not title:
            await self._fail(interaction, 'Title must not be empty.',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return

        # --- validate description word count ---
        word_count = len(description.split())
        if word_count < 30 or word_count > 600:
            await self._fail(interaction,
                             f'Description must be 30-600 words (currently {word_count}).',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return

        # --- validate budget ---
        try:
            budget = float(raw_budget)
        except (ValueError, TypeError):
            await self._fail(interaction, 'Enter a valid budget.',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return

        if budget <= 0:
            await self._fail(interaction, 'Enter a valid budget.',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return

        # --- parse deadline ---
        deadline = _parse_deadline(raw_deadline) if raw_deadline else None

        milestone_data = {
            'title': title,
            'description': description,
            'budget': str(budget),
        }
        if deadline:
            milestone_data['deadline'] = deadline

        self.accumulated.append(milestone_data)

        if self.milestone_num < self.total_count:
            # More milestones to collect, show continue button to open next modal
            # (Discord does not allow send_modal() from within modal.on_submit())
            view = _retry_view(
                InterviewMilestoneFormModal,
                {
                    'milestone_num': self.milestone_num + 1,
                    'total_count': self.total_count,
                    'accumulated': self.accumulated,
                    'room_data': self.room_data,
                },
                label='Next Milestone',
            )
            next_num = self.milestone_num + 1
            await interaction.response.edit_message(
                embed=info_embed(
                    message=(
                        f'Milestone **{self.milestone_num}** saved. Click **Continue** '
                        f'for milestone **{next_num}** of **{self.total_count}**.'
                    ),
                ),
                view=view,
            )
        else:
            # All collected, batch-save
            await self._save_all(interaction)

    async def _fail(
        self,
        interaction: discord.Interaction,
        message: str,
        prefill: dict,
    ) -> None:
        """Show an error embed with a 'Try Again' button that re-opens the modal."""
        view = _retry_view(
            InterviewMilestoneFormModal,
            {
                'milestone_num': self.milestone_num,
                'total_count': self.total_count,
                'accumulated': self.accumulated,
                'room_data': self.room_data,
                'prefill': prefill,
            },
        )
        await interaction.response.send_message(
            embed=error_embed(message=message),
            view=view,
            ephemeral=True,
        )

    async def _save_all(self, interaction: discord.Interaction) -> None:
        """Batch-save all accumulated milestones to the backend."""
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        url = f'{BACKEND_URL}rooms/bot/save-milestones/'
        payload = {
            'discord_id': str(interaction.user.id),
            'room_id': self.room_data.get('room_id', ''),
            'milestones': self.accumulated,
        }

        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status != 200:
                    error_msg = 'The service is temporarily unavailable.'
                    await record_and_notify(
                        room_id=self.room_data.get('room_id', ''),
                        sender_role='freelancer',
                        msg_data=error_msg,
                        command_name='interview_milestone',
                        target_discord_id='',
                        executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                        job_title=self.room_data.get('job_title', ''),
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    await interaction.response.edit_message(
                        embed=error_embed(message=error_msg),
                        view=None,
                    )
                    return

                count = body.get('milestone_count', 0)

                # Build the single final response text (used for both
                # the executor embed and the DM notification).
                success_msg = (
                    f'**{count} milestone(s)** configured for job '
                    f'**{self.room_data.get("job_title", "")}**.'
                )

                # --- Record + notify client ---
                client_discord_id = self.room_data.get('client_discord_id', '')
                await record_and_notify(
                    room_id=self.room_data.get('room_id', ''),
                    sender_role='freelancer',
                    msg_data=success_msg,
                    command_name='interview_milestone',
                    target_discord_id=client_discord_id,
                    executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                    job_title=self.room_data.get('job_title', ''),
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )

                await interaction.response.edit_message(
                    embed=success_embed(message=success_msg),
                    view=None,
                )

        except Exception:
            logger.exception('Failed to save milestones to backend')
            error_msg = 'The service is temporarily unavailable.'
            await record_and_notify(
                room_id=self.room_data.get('room_id', ''),
                sender_role='freelancer',
                msg_data=error_msg,
                command_name='interview_milestone',
                target_discord_id='',
                executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                job_title=self.room_data.get('job_title', ''),
                bot=interaction.client,
                session=session,
                headers=headers,
            )
            await interaction.response.edit_message(
                embed=error_embed(message=error_msg),
                view=None,
            )


# ── Edit Modal (CASE B) ─────────────────────────────────────────────────


class InterviewMilestoneEditModal(discord.ui.Modal):
    """Pre-filled modal for editing an existing milestone."""

    def __init__(
        self,
        milestone_id: str,
        existing_data: dict,
        room_data: dict,
    ) -> None:
        super().__init__(title='Edit Milestone', timeout=300)
        self.milestone_id = milestone_id
        self.existing_data = existing_data
        self.room_data = room_data

        self.title_inp = discord.ui.TextInput(
            label='Title',
            max_length=64,
            required=True,
            default=existing_data.get('title', ''),
        )
        self.desc_inp = discord.ui.TextInput(
            label='Description',
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
            default=existing_data.get('description', ''),
        )
        self.budget_inp = discord.ui.TextInput(
            label='Budget ($)',
            max_length=10,
            required=True,
            default=existing_data.get('budget', ''),
        )
        deadline = existing_data.get('deadline') or ''
        self.deadline_inp = discord.ui.TextInput(
            label='Deadline',
            placeholder='YYYY-MM-DD or ISO format (optional)',
            max_length=30,
            required=False,
            default=deadline,
        )

        self.add_item(self.title_inp)
        self.add_item(self.desc_inp)
        self.add_item(self.budget_inp)
        self.add_item(self.deadline_inp)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title = self.title_inp.value.strip()
        description = self.desc_inp.value.strip()
        raw_budget = self.budget_inp.value.strip()
        raw_deadline = self.deadline_inp.value.strip()

        # --- validate ---
        if not title:
            await interaction.response.send_message(
                embed=error_embed(message='Title must not be empty.'),
                view=_retry_view(
                    InterviewMilestoneEditModal,
                    dict(
                        milestone_id=self.milestone_id,
                        existing_data=dict(title=title, description=description, budget=raw_budget, deadline=raw_deadline),
                        room_data=self.room_data,
                    ),
                ),
                ephemeral=True,
            )
            return

        word_count = len(description.split())
        if word_count < 30 or word_count > 600:
            await interaction.response.send_message(
                embed=error_embed(
                    message=f'Description must be 30-600 words (currently {word_count}).',
                ),
                view=_retry_view(
                    InterviewMilestoneEditModal,
                    dict(
                        milestone_id=self.milestone_id,
                        existing_data=dict(title=title, description=description, budget=raw_budget, deadline=raw_deadline),
                        room_data=self.room_data,
                    ),
                ),
                ephemeral=True,
            )
            return

        try:
            budget = float(raw_budget)
        except (ValueError, TypeError):
            await interaction.response.send_message(
                embed=error_embed(message='Enter a valid budget.'),
                view=_retry_view(
                    InterviewMilestoneEditModal,
                    dict(
                        milestone_id=self.milestone_id,
                        existing_data=dict(title=title, description=description, budget=raw_budget, deadline=raw_deadline),
                        room_data=self.room_data,
                    ),
                ),
                ephemeral=True,
            )
            return

        if budget <= 0:
            await interaction.response.send_message(
                embed=error_embed(message='Enter a valid budget.'),
                view=_retry_view(
                    InterviewMilestoneEditModal,
                    dict(
                        milestone_id=self.milestone_id,
                        existing_data=dict(title=title, description=description, budget=raw_budget, deadline=raw_deadline),
                        room_data=self.room_data,
                    ),
                ),
                ephemeral=True,
            )
            return

        deadline = _parse_deadline(raw_deadline) if raw_deadline else None

        # --- send update to backend ---
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        url = f'{BACKEND_URL}rooms/bot/update-milestone/'
        payload = {
            'discord_id': str(interaction.user.id),
            'room_id': self.room_data.get('room_id', ''),
            'milestone_id': self.milestone_id,
            'title': title,
            'description': description,
            'budget': str(budget),
        }
        if deadline is not None:
            payload['deadline'] = deadline
        else:
            payload['deadline'] = None

        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status != 200:
                    error_msg = body.get('error', 'The service is temporarily unavailable.')
                    await record_and_notify(
                        room_id=self.room_data.get('room_id', ''),
                        sender_role='freelancer',
                        msg_data=error_msg,
                        command_name='interview_milestone',
                        target_discord_id='',
                        executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                        job_title=self.room_data.get('job_title', ''),
                        bot=interaction.client,
                        session=session,
                        headers=headers,
                    )
                    await interaction.response.edit_message(
                        embed=error_embed(message=error_msg),
                        view=None,
                    )
                    return

                # Build the single final response text (used for both
                # the executor embed and the DM notification).
                success_msg = (
                    f'Milestone `{self.milestone_id}` updated successfully.'
                )

                # --- Record + notify client ---
                client_discord_id = self.room_data.get('client_discord_id', '')
                await record_and_notify(
                    room_id=self.room_data.get('room_id', ''),
                    sender_role='freelancer',
                    msg_data=success_msg,
                    command_name='interview_milestone',
                    target_discord_id=client_discord_id,
                    executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                    job_title=self.room_data.get('job_title', ''),
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )

                await interaction.response.edit_message(
                    embed=success_embed(message=success_msg),
                    view=None,
                )
        except Exception:
            logger.exception('Failed to update milestone')
            error_msg = 'The service is temporarily unavailable.'
            await record_and_notify(
                room_id=self.room_data.get('room_id', ''),
                sender_role='freelancer',
                msg_data=error_msg,
                command_name='interview_milestone',
                target_discord_id='',
                executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                job_title=self.room_data.get('job_title', ''),
                bot=interaction.client,
                session=session,
                headers=headers,
            )
            await interaction.response.edit_message(
                embed=error_embed(message=error_msg),
                view=None,
            )


# ── Retry Helper ────────────────────────────────────────────────────────


def _retry_view(
    modal_class: type[discord.ui.Modal],
    kwargs: dict,
    label: str = 'Try Again',
) -> discord.ui.View:
    """Return a View with a single button that opens the specified modal.

    Args:
        modal_class: The modal class to instantiate.
        kwargs: Keyword arguments passed to the modal constructor.
        label: Button label (default 'Try Again'). Use 'Continue' or
               'Next Milestone' for modal chaining workaround.
    """
    view = discord.ui.View(timeout=300)

    class RetryButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label=label, style=discord.ButtonStyle.primary)

        async def callback(self, btn_interaction: discord.Interaction):
            modal = modal_class(**kwargs)
            await btn_interaction.response.send_modal(modal)

    view.add_item(RetryButton())
    return view


# ── Delete Confirmation View (top-level) ────────────────────────────────


class InterviewMilestoneDeleteView(discord.ui.View):
    """Confirmation view for deleting a milestone."""

    def __init__(
        self,
        room_data: dict,
        milestone_id: str,
        milestones: list[dict],
        action: str,
    ) -> None:
        super().__init__(timeout=300)
        self.room_data = room_data
        self.milestone_id = milestone_id
        self.milestones = milestones
        self.action = action
        self.author_id: int | None = None
        self._done = False

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label='Yes, Delete', style=discord.ButtonStyle.danger)
    async def confirm(self, btn_interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(btn_interaction, self):
            return
        if self._done:
            return
        self._done = True
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        url = f'{BACKEND_URL}rooms/bot/delete-milestone/'
        payload = {
            'discord_id': str(btn_interaction.user.id),
            'room_id': self.room_data.get('room_id', ''),
            'milestone_id': self.milestone_id,
        }

        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status != 200:
                    error_msg = body.get('error', 'The service is temporarily unavailable.')
                    await record_and_notify(
                        room_id=self.room_data.get('room_id', ''),
                        sender_role='freelancer',
                        msg_data=error_msg,
                        command_name='interview_milestone',
                        target_discord_id='',
                        executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                        job_title=self.room_data.get('job_title', ''),
                        bot=btn_interaction.client,
                        session=session,
                        headers=headers,
                    )
                    await btn_interaction.response.edit_message(
                        embed=error_embed(message=error_msg),
                        view=None,
                    )
                    return

                # Build the single final response text (used for both
                # the executor embed and the DM notification).
                success_msg = (
                    f'Milestone `{self.milestone_id}` deleted. '
                    f'Remaining milestones re-ordered.'
                )

                # --- Record + notify client ---
                client_discord_id = self.room_data.get('client_discord_id', '')
                await record_and_notify(
                    room_id=self.room_data.get('room_id', ''),
                    sender_role='freelancer',
                    msg_data=success_msg,
                    command_name='interview_milestone',
                    target_discord_id=client_discord_id,
                    executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                    job_title=self.room_data.get('job_title', ''),
                    bot=btn_interaction.client,
                    session=session,
                    headers=headers,
                )

                await btn_interaction.response.edit_message(
                    embed=success_embed(message=success_msg),
                    view=None,
                )
        except Exception:
            logger.exception('Failed to delete milestone')
            error_msg = 'The service is temporarily unavailable.'
            await record_and_notify(
                room_id=self.room_data.get('room_id', ''),
                sender_role='freelancer',
                msg_data=error_msg,
                command_name='interview_milestone',
                target_discord_id='',
                executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                job_title=self.room_data.get('job_title', ''),
                bot=btn_interaction.client,
                session=session,
                headers=headers,
            )
            await btn_interaction.response.edit_message(
                embed=error_embed(message=error_msg),
                view=None,
            )

    @discord.ui.button(label='← Back', style=discord.ButtonStyle.secondary)
    async def cancel(self, btn_interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(btn_interaction, self):
            return
        # Go back to milestone selection
        select_view = InterviewMilestoneSelectView(
            room_data=self.room_data,
            milestones=self.milestones,
            action=self.action,
        )
        select_view.author_id = self.author_id
        await btn_interaction.response.edit_message(view=select_view)


# ── Action View (CASE B, first dropdown) ───────────────────────────────


class InterviewMilestoneSelectView(discord.ui.View):
    """Second step: milestone selection dropdown with Proceed/← Back."""

    def __init__(
        self,
        room_data: dict,
        milestones: list[dict],
        action: str,  # 'edit' or 'delete'
    ) -> None:
        super().__init__(timeout=300)
        self.room_data = room_data
        self.milestones = milestones
        self.action = action
        self.author_id: int | None = None
        self._done = False
        self._selected_milestone_id: str | None = None

        self._all_options = []
        for m in milestones:
            label = f'{m["order_number"]}. {m["title"]} (${m["budget"]})'
            desc = None if action == 'delete' else f'Select milestone to {action}'
            self._all_options.append(
                discord.SelectOption(
                    label=label[:100],  # Discord max 100 chars
                    value=m['milestone_id'],
                    description=desc,
                ),
            )

        self.milestone_select = discord.ui.Select(
            placeholder=f'Select milestone to {action}…',
            options=self._all_options,
        )
        self.milestone_select.callback = self._on_milestone_selected
        self.add_item(self.milestone_select)

        proceed = discord.ui.Button(label='Proceed', style=discord.ButtonStyle.success, row=1)
        proceed.callback = self._on_proceed
        self.add_item(proceed)

        back = discord.ui.Button(label='← Back', style=discord.ButtonStyle.secondary, row=1)
        back.callback = self._on_back
        self.add_item(back)

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        """Go back to the action-selection view."""
        back_view = InterviewMilestoneActionView(
            room_data=self.room_data,
            milestones=self.milestones,
        )
        back_view.author_id = self.author_id
        # Preserve the existing embed (the milestone list) but swap the view
        await interaction.response.edit_message(view=back_view)

    async def _on_milestone_selected(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self._selected_milestone_id = self.milestone_select.values[0]
        selected_label = next(
            (opt.label for opt in self._all_options if opt.value == self.milestone_select.values[0]),
            self.milestone_select.values[0],
        )
        self.milestone_select.placeholder = f"✓ {selected_label}"
        await interaction.response.defer()

    async def _on_proceed(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        milestone_id = self._selected_milestone_id or (self.milestone_select.values[0] if self.milestone_select.values else None)
        if not milestone_id:
            await interaction.response.send_message(
                embed=error_embed(message='Select a milestone first.'),
                ephemeral=True,
            )
            return
        self._done = True

        milestone_data = next(
            (m for m in self.milestones if m['milestone_id'] == milestone_id),
            None,
        )
        if not milestone_data:
            error_msg = 'Could not find the milestone.'
            session = get_http_session()
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            await record_and_notify(
                room_id=self.room_data.get('room_id', ''),
                sender_role='freelancer',
                msg_data=error_msg,
                command_name='interview_milestone',
                target_discord_id='',
                executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                job_title=self.room_data.get('job_title', ''),
                bot=interaction.client,
                session=session,
                headers=headers,
            )
            await interaction.response.edit_message(
                embed=error_embed(message=error_msg),
                view=None,
            )
            return

        if self.action == 'edit':
            modal = InterviewMilestoneEditModal(
                milestone_id=milestone_id,
                existing_data=milestone_data,
                room_data=self.room_data,
            )
            await interaction.response.send_modal(modal)
        else:
            # Delete, show confirmation
            embed = create_embed(
                title='Confirm Delete',
                description=(
                    f'Are you sure you want to delete milestone '
                    f'`{milestone_id}` (**{milestone_data.get("title", "?")}**)?\n\n'
                    f'Remaining milestones will be re-ordered automatically.'
                ),
                color=BrandColor.ERROR,
            )
            view = InterviewMilestoneDeleteView(
                room_data=self.room_data,
                milestone_id=milestone_id,
                milestones=self.milestones,
                action=self.action,
            )
            view.author_id = self.author_id
            await interaction.response.edit_message(embed=embed, view=view)


class InterviewMilestoneActionView(discord.ui.View):
    """First step (CASE B): Action dropdown with Proceed / Cancel."""

    def __init__(self, room_data: dict, milestones: list[dict]) -> None:
        super().__init__(timeout=300)
        self.room_data = room_data
        self.milestones = milestones
        self.author_id: int | None = None
        self._done = False
        self._selected_action: str | None = None

        self._all_options = [
            discord.SelectOption(
                label='Add Milestone',
                value='add',
                description='Add a new milestone',
            ),
        ]
        if milestones:
            self._all_options.append(
                discord.SelectOption(
                    label='Edit Milestone',
                    value='edit',
                    description='Modify an existing milestone',
                ),
            )
            self._all_options.append(
                discord.SelectOption(
                    label='Delete Milestone',
                    value='delete',
                    description='Remove a milestone',
                ),
            )

        self.action_select = discord.ui.Select(
            placeholder='Choose an action…',
            options=self._all_options,
        )
        self.action_select.callback = self._on_action
        self.add_item(self.action_select)

        proceed = discord.ui.Button(label='Proceed', style=discord.ButtonStyle.success, row=1)
        proceed.callback = self._on_proceed
        self.add_item(proceed)

        cancel = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.danger, row=1)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def on_timeout(self) -> None:
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        cancel_msg = 'Milestone management cancelled.'
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        await record_and_notify(
            room_id=self.room_data.get('room_id', ''),
            sender_role='freelancer',
            msg_data=cancel_msg,
            command_name='interview_milestone',
            target_discord_id='',
            executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
            job_title=self.room_data.get('job_title', ''),
            bot=interaction.client,
            session=session,
            headers=headers,
        )
        await interaction.response.edit_message(
            embed=info_embed(message=cancel_msg),
            view=None,
        )

    async def _on_action(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        self._selected_action = self.action_select.values[0]
        selected_label = next(
            (opt.label for opt in self._all_options if opt.value == self.action_select.values[0]),
            self.action_select.values[0],
        )
        self.action_select.placeholder = f"✓ {selected_label}"
        await interaction.response.defer()

    async def _on_proceed(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        value = self._selected_action or (self.action_select.values[0] if self.action_select.values else None)
        if not value:
            await interaction.response.send_message(
                embed=error_embed(message='Select an action first.'),
                ephemeral=True,
            )
            return
        self._done = True

        if value == 'add':
            # Check max before opening form
            if len(self.milestones) >= 10:
                error_msg = 'Could not add milestone. Maximum of 10 milestones reached.'
                session = get_http_session()
                headers = {'X-Webhook-Token': WEBHOOK_SECRET}
                await record_and_notify(
                    room_id=self.room_data.get('room_id', ''),
                    sender_role='freelancer',
                    msg_data=error_msg,
                    command_name='interview_milestone',
                    target_discord_id='',
                    executor_name=self.room_data.get('freelancer_name', 'Freelancer'),
                    job_title=self.room_data.get('job_title', ''),
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                await interaction.response.edit_message(
                    embed=error_embed(message=error_msg),
                    view=None,
                )
                return

            # Open a single-milestone form
            modal = InterviewMilestoneFormModal(
                milestone_num=1,
                total_count=1,
                accumulated=[],
                room_data=self.room_data,
            )
            await interaction.response.send_modal(modal)

        elif value == 'edit':
            # Replace current view with milestone selection for edit
            select_view = InterviewMilestoneSelectView(
                room_data=self.room_data,
                milestones=self.milestones,
                action='edit',
            )
            select_view.author_id = self.author_id
            await interaction.response.edit_message(view=select_view)

        elif value == 'delete':
            # Replace current view with milestone selection for delete
            select_view = InterviewMilestoneSelectView(
                room_data=self.room_data,
                milestones=self.milestones,
                action='delete',
            )
            select_view.author_id = self.author_id
            await interaction.response.edit_message(view=select_view)


# ── Cog ─────────────────────────────────────────────────────────────────


class InterviewMilestone(commands.Cog):
    """``/interview milestone``, Freelancer manages job milestones."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(
        name='interview_milestone',
        description='...',
    )
    @app_commands.checks.cooldown(1, 15, key=lambda i: i.user.id)
    async def interview_milestone(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Manage milestones for the job in the selected interview room (freelancer only)."""

        async def callback(user_data: dict) -> tuple:
            active_role = user_data.get('active_role')
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}

            room_data = user_data['_selected_room']

            room_id = room_data.get('room_id', '')

            # ── Determine sender display name ────────────────────────────
            if active_role == 'client':
                sender_name = room_data.get('client_name', 'Client')
            else:
                sender_name = room_data.get('freelancer_name', 'Freelancer')

            # ── 2. Check agreement budget with backend ──────────────────────
            session = get_http_session()
            check_url = f'{BACKEND_URL}rooms/bot/check-agreement-budget/'
            params = {
                'discord_id': str(interaction.user.id),
                'room_id': room_id,
            }

            try:
                async with session.get(check_url, params=params, headers=headers) as resp:
                    body = await resp.json()
                    if resp.status != 200:
                        error_msg = body.get('error', 'Could not check agreement.')
                        await record_and_notify(
                            room_id=room_id,
                            sender_role=active_role,
                            msg_data=error_msg,
                            command_name='interview_milestone',
                            target_discord_id='',
                            executor_name=sender_name,
                            job_title=room_data.get('job_title', ''),
                            bot=interaction.client,
                            session=session,
                            headers=headers,
                        )
                        return error_embed(message=error_msg)

                    if not body.get('has_budget'):
                        error_msg = 'Could not configure milestones. The job has no final budget.'
                        await record_and_notify(
                            room_id=room_id,
                            sender_role=active_role,
                            msg_data=error_msg,
                            command_name='interview_milestone',
                            target_discord_id='',
                            executor_name=sender_name,
                            job_title=room_data.get('job_title', ''),
                            bot=interaction.client,
                            session=session,
                            headers=headers,
                        )
                        return error_embed(message=error_msg)

                    milestones = body.get('milestones', [])
            except Exception:
                logger.exception('Failed to check agreement budget')
                error_msg = 'The service is temporarily unavailable.'
                await record_and_notify(
                    room_id=room_id,
                    sender_role=active_role,
                    msg_data=error_msg,
                    command_name='interview_milestone',
                    target_discord_id='',
                    executor_name=sender_name,
                    job_title=room_data.get('job_title', ''),
                    bot=interaction.client,
                    session=session,
                    headers=headers,
                )
                return error_embed(message=error_msg)

            # ── 3a. CASE B, Milestones exist → show action view ────────────
            if milestones:
                embed_desc = [
                    f'**Room:** `{room_id}`',
                    f'**Job:** {room_data.get("job_title", "")}',
                    '',
                    f'**Existing Milestones ({len(milestones)}):**',
                ]
                for m in milestones:
                    embed_desc.append(
                        f'• `{m["milestone_id"]}`, {m["title"]} (${m["budget"]})'
                    )

                embed = create_embed(
                    title='Milestone Management',
                    description='\n'.join(embed_desc),
                    color=BrandColor.PRIMARY,
                )

                view = InterviewMilestoneActionView(room_data, milestones)
                view.author_id = interaction.user.id
                return embed, view

            # ── 3b. CASE A, No milestones → show continue button for count modal ──
            view = _retry_view(
                InterviewMilestoneCountModal,
                {
                    'room_data': room_data,
                    'interaction': interaction,
                },
                label='Set Milestones',
            )
            return info_embed(
                message=(
                    f'No milestones configured yet for room `{room_id}`.\n'
                    f'Click **Set Milestones** to create them.'
                ),
            ), view

        await validate_and_respond(interaction, callback)


# ── Setup ───────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewMilestone(bot))
    logger.info('InterviewMilestone cog loaded')
