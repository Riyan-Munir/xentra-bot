"""
``/interview milestone`` — Freelancer manages job milestones in the interview room.

Flow:
  1. Command handler validates role (freelancer only), fetches selected room.
  2. Calls backend to check agreement has final_budget set.
  3. CASE A — no milestones exist:
       a. Count modal (1-10) → sequential milestone form modals.
       b. After last milestone, batch-save to backend, notify client.
  4. CASE B — milestones exist:
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
from utils.system_message_handler import handle_system_message
from utils.failed_delivery import log_failed_delivery

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
            await interaction.response.edit_message(
                embed=error_embed(message='Please enter a valid number (1-10).'),
                view=view,
            )
            return

        if total < 1 or total > 10:
            view = _retry_view(InterviewMilestoneCountModal, {'room_data': self.room_data, 'interaction': self.origin_interaction})
            await interaction.response.edit_message(
                embed=error_embed(message='Number of milestones must be between 1 and 10.'),
                view=view,
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
            placeholder='Max 32 characters',
            max_length=32,
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
            label='Deadline (optional)',
            placeholder='YYYY-MM-DD or ISO format',
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
            await self._fail(interaction, 'Title cannot be empty.',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return

        # --- validate description word count ---
        word_count = len(description.split())
        if word_count < 30:
            await self._fail(interaction,
                             f'Description must be at least 30 words (currently {word_count}).',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return
        if word_count > 600:
            await self._fail(interaction,
                             f'Description must be no more than 600 words (currently {word_count}).',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return

        # --- validate budget ---
        try:
            budget = float(raw_budget)
        except (ValueError, TypeError):
            await self._fail(interaction, 'Budget must be a valid number (e.g. 500.00).',
                             {'title': title, 'description': description,
                              'budget': raw_budget, 'deadline': raw_deadline})
            return

        if budget <= 0:
            await self._fail(interaction, 'Budget must be greater than zero.',
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
            # More milestones to collect — show continue button to open next modal
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
            # All collected — batch-save
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
        await interaction.response.edit_message(
            embed=error_embed(message=message),
            view=view,
        )

    async def _save_all(self, interaction: discord.Interaction) -> None:
        """Batch-save all accumulated milestones to the backend."""
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        url = f'{BACKEND_URL}rooms/bot/save-milestones/'
        payload = {
            'discord_id': str(interaction.user.id),
            'role': 'freelancer',
            'room_id': self.room_data.get('room_id', ''),
            'milestones': self.accumulated,
        }

        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status != 200:
                    await interaction.response.edit_message(
                        embed=error_embed(
                            message=body.get('error', 'Failed to save milestones.'),
                        ),
                        view=None,
                    )
                    return

                count = body.get('milestone_count', 0)
                msg_id = body.get('msg_id', '')
                msg_data = body.get('msg_data', f'{count} milestone(s) configured.')

                # --- Notify client ---
                client_discord_id = self.room_data.get('client_discord_id', '')
                if client_discord_id and msg_id:
                    notify_data = {
                        'discord_id': client_discord_id,
                        'room_id': self.room_data.get('room_id', ''),
                        'job_title': self.room_data.get('job_title', ''),
                        'command_name': 'interview_milestone',
                        'executor_name': self.room_data.get('freelancer_name', 'Freelancer'),
                        'msg_data': msg_data,
                    }
                    delivery_ok = await handle_system_message(
                        message_type='room_interview_message',
                        data=notify_data,
                        bot=interaction.client,
                    )
                    if not delivery_ok:
                        await log_failed_delivery(
                            room_id=self.room_data.get('room_id', ''),
                            message_type='notification',
                            target_discord_id=client_discord_id,
                            msg_id=msg_id,
                            session=session,
                            headers=headers,
                        )

                await interaction.response.edit_message(
                    embed=success_embed(
                        message=(
                            f'**{count} milestone(s)** configured for room '
                            f'`{self.room_data.get("room_id", "")}`.'
                        ),
                    ),
                    view=None,
                )

        except Exception:
            logger.exception('Failed to save milestones to backend')
            await interaction.response.edit_message(
                embed=error_embed(
                    message='Could not save milestones due to a system error. Please try again later.',
                ),
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
            max_length=32,
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
            label='Deadline (optional)',
            placeholder='YYYY-MM-DD or ISO format',
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
            await interaction.response.edit_message(
                embed=error_embed(message='Title cannot be empty.'),
                view=None,
            )
            return

        word_count = len(description.split())
        if word_count < 30:
            await interaction.response.edit_message(
                embed=error_embed(
                    message=f'Description must be at least 30 words (currently {word_count}).',
                ),
                view=None,
            )
            return
        if word_count > 600:
            await interaction.response.edit_message(
                embed=error_embed(
                    message=f'Description must be no more than 600 words (currently {word_count}).',
                ),
                view=None,
            )
            return

        try:
            budget = float(raw_budget)
        except (ValueError, TypeError):
            await interaction.response.edit_message(
                embed=error_embed(message='Budget must be a valid number.'),
                view=None,
            )
            return

        if budget <= 0:
            await interaction.response.edit_message(
                embed=error_embed(message='Budget must be greater than zero.'),
                view=None,
            )
            return

        deadline = _parse_deadline(raw_deadline) if raw_deadline else None

        # --- send update to backend ---
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        url = f'{BACKEND_URL}rooms/bot/update-milestone/'
        payload = {
            'discord_id': str(interaction.user.id),
            'role': 'freelancer',
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
                    await interaction.response.edit_message(
                        embed=error_embed(
                            message=body.get('error', 'Failed to update milestone.'),
                        ),
                        view=None,
                    )
                    return

                msg_id = body.get('msg_id', '')
                msg_data = body.get('msg_data', f'Milestone `{self.milestone_id}` updated.')

                # --- Notify client ---
                client_discord_id = self.room_data.get('client_discord_id', '')
                if client_discord_id and msg_id:
                    notify_data = {
                        'discord_id': client_discord_id,
                        'room_id': self.room_data.get('room_id', ''),
                        'job_title': self.room_data.get('job_title', ''),
                        'command_name': 'interview_milestone',
                        'executor_name': self.room_data.get('freelancer_name', 'Freelancer'),
                        'msg_data': msg_data,
                    }
                    delivery_ok = await handle_system_message(
                        message_type='room_interview_message',
                        data=notify_data,
                        bot=interaction.client,
                    )
                    if not delivery_ok:
                        await log_failed_delivery(
                            room_id=self.room_data.get('room_id', ''),
                            message_type='notification',
                            target_discord_id=client_discord_id,
                            msg_id=msg_id,
                            session=session,
                            headers=headers,
                        )

                await interaction.response.edit_message(
                    embed=success_embed(
                        message=f'Milestone `{self.milestone_id}` updated successfully.',
                    ),
                    view=None,
                )
        except Exception:
            logger.exception('Failed to update milestone')
            await interaction.response.edit_message(
                embed=error_embed(
                    message='Could not update milestone due to a system error.',
                ),
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
    ) -> None:
        super().__init__(timeout=300)
        self.room_data = room_data
        self.milestone_id = milestone_id
        self.author_id: int | None = None

    async def on_timeout(self) -> None:
        """Disable all buttons on timeout to prevent stale-state abuse."""
        for item in self.children:
            item.disabled = True
        self.stop()

    @discord.ui.button(label='Yes, Delete', style=discord.ButtonStyle.danger)
    async def confirm(self, btn_interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(btn_interaction, self):
            return
        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}
        url = f'{BACKEND_URL}rooms/bot/delete-milestone/'
        payload = {
            'discord_id': str(btn_interaction.user.id),
            'role': 'freelancer',
            'room_id': self.room_data.get('room_id', ''),
            'milestone_id': self.milestone_id,
        }

        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await resp.json()
                if resp.status != 200:
                    await btn_interaction.response.edit_message(
                        embed=error_embed(
                            message=body.get('error', 'Failed to delete milestone.'),
                        ),
                        view=None,
                    )
                    return

                msg_id = body.get('msg_id', '')
                msg_data = body.get('msg_data', f'Milestone `{self.milestone_id}` deleted.')

                # --- Notify client ---
                client_discord_id = self.room_data.get('client_discord_id', '')
                if client_discord_id and msg_id:
                    notify_data = {
                        'discord_id': client_discord_id,
                        'room_id': self.room_data.get('room_id', ''),
                        'job_title': self.room_data.get('job_title', ''),
                        'command_name': 'interview_milestone',
                        'executor_name': self.room_data.get('freelancer_name', 'Freelancer'),
                        'msg_data': msg_data,
                    }
                    delivery_ok = await handle_system_message(
                        message_type='room_interview_message',
                        data=notify_data,
                        bot=btn_interaction.client,
                    )
                    if not delivery_ok:
                        await log_failed_delivery(
                            room_id=self.room_data.get('room_id', ''),
                            message_type='notification',
                            target_discord_id=client_discord_id,
                            msg_id=msg_id,
                            session=session,
                            headers=headers,
                        )

                await btn_interaction.response.edit_message(
                    embed=success_embed(
                        message=f'Milestone `{self.milestone_id}` deleted and remaining milestones re-ordered.',
                    ),
                    view=None,
                )
        except Exception:
            logger.exception('Failed to delete milestone')
            await btn_interaction.response.edit_message(
                embed=error_embed(
                    message='Could not delete milestone due to a system error.',
                ),
                view=None,
            )

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, btn_interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
        if not is_author(btn_interaction, self):
            return
        for item in self.children:
            item.disabled = True
        self.stop()
        await btn_interaction.response.edit_message(
            embed=info_embed(message='Delete cancelled.'),
            view=None,
        )


# ── Action View (CASE B — first dropdown) ───────────────────────────────


class InterviewMilestoneSelectView(discord.ui.View):
    """Second step: milestone selection dropdown for Edit or Delete."""

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

        options = []
        for m in milestones:
            label = f'{m["order_number"]}. {m["title"]} (${m["budget"]})'
            desc = None if action == 'delete' else f'Select milestone to {action}'
            options.append(
                discord.SelectOption(
                    label=label[:100],  # Discord max 100 chars
                    value=m['milestone_id'],
                    description=desc,
                ),
            )

        self.milestone_select = discord.ui.Select(
            placeholder=f'Select milestone to {action}…',
            options=options,
        )
        self.milestone_select.callback = self._on_milestone_selected
        self.add_item(self.milestone_select)

    async def on_timeout(self) -> None:
        """Disable all children on timeout to prevent stale-state abuse."""
        for item in self.children:
            item.disabled = True
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, _btn: discord.ui.Button) -> None:
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
        milestone_id = self.milestone_select.values[0]
        milestone_data = next(
            (m for m in self.milestones if m['milestone_id'] == milestone_id),
            None,
        )
        if not milestone_data:
            await interaction.response.edit_message(
                embed=error_embed(message='Milestone not found.'),
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
            # Delete — show confirmation
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
            )
            view.author_id = self.author_id
            await interaction.response.edit_message(embed=embed, view=view)


class InterviewMilestoneActionView(discord.ui.View):
    """First step (CASE B): Action dropdown with Add / Edit / Delete."""

    def __init__(self, room_data: dict, milestones: list[dict]) -> None:
        super().__init__(timeout=300)
        self.room_data = room_data
        self.milestones = milestones
        self.author_id: int | None = None

        options = [
            discord.SelectOption(
                label='Add Milestone',
                value='add',
                description='Add a new milestone (max 10 total)',
            ),
        ]
        if milestones:
            options.append(
                discord.SelectOption(
                    label='Edit Milestone',
                    value='edit',
                    description='Modify an existing milestone',
                ),
            )
            options.append(
                discord.SelectOption(
                    label='Delete Milestone',
                    value='delete',
                    description='Remove a milestone and re-order',
                ),
            )

        self.action_select = discord.ui.Select(
            placeholder='Choose an action…',
            options=options,
        )
        self.action_select.callback = self._on_action
        self.add_item(self.action_select)

    async def on_timeout(self) -> None:
        """Disable all children on timeout to prevent stale-state abuse."""
        for item in self.children:
            item.disabled = True
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not is_author(interaction, self):
            return
        for item in self.children:
            item.disabled = True
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Milestone management cancelled.'),
            view=None,
        )

    async def _on_action(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self):
            return
        value = self.action_select.values[0]

        if value == 'add':
            # Check max before opening form
            if len(self.milestones) >= 10:
                await interaction.response.edit_message(
                    embed=error_embed(
                        message='Maximum 10 milestones already reached. '
                        'Delete one before adding another.',
                    ),
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
    """``/interview milestone`` — Freelancer manages job milestones."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(
        name='interview_milestone',
        description='Manage milestones for the job in the selected interview room.',
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

            # ── 2. Check agreement budget with backend ──────────────────────
            session = get_http_session()
            check_url = f'{BACKEND_URL}rooms/bot/check-agreement-budget/'
            params = {
                'discord_id': str(interaction.user.id),
                'role': active_role,
                'room_id': room_id,
            }

            try:
                async with session.get(check_url, params=params, headers=headers) as resp:
                    body = await resp.json()
                    if resp.status != 200:
                        return error_embed(
                            message=body.get('error', 'Failed to check agreement.'),
                        )

                    if not body.get('has_budget'):
                        return error_embed(
                            message='The client has not set a final budget yet. '
                            'Milestones can only be configured after a budget is agreed upon.',
                        )

                    milestones = body.get('milestones', [])
            except Exception:
                logger.exception('Failed to check agreement budget')
                return error_embed(
                    message='Unable to reach the backend service. Please try again later.',
                )

            # ── 3a. CASE B — Milestones exist → show action view ────────────
            if milestones:
                embed_desc = [
                    f'**Room:** `{room_id}`',
                    f'**Job:** {room_data.get("job_title", "")}',
                    '',
                    f'**Existing Milestones ({len(milestones)}):**',
                ]
                for m in milestones:
                    embed_desc.append(
                        f'• `{m["milestone_id"]}` — {m["title"]} (${m["budget"]})'
                    )

                embed = create_embed(
                    title='Milestone Management',
                    description='\n'.join(embed_desc),
                    color=BrandColor.PRIMARY,
                )

                view = InterviewMilestoneActionView(room_data, milestones)
                view.author_id = interaction.user.id
                return embed, view

            # ── 3b. CASE A — No milestones → show continue button for count modal ──
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
