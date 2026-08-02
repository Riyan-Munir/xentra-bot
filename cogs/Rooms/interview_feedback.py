"""
``/interview feedback``, Submit feedback about an interview room.

Flow:
  1. ``validate_and_respond`` validates the user, role, and room context.
  2. Fetches closed rooms where the user has NOT yet submitted feedback.
  3. If no rooms found, shows an info message.
  4. Room selection dropdown → "Write Feedback" button → Feedback Modal.
  5. Modal validates: security threat (terminate), empty (retry), word count ≤ 100 (retry).
  6. On modal success → show rating dropdown (1–5) + Submit / Cancel.
  7. On submit → POST to backend to persist feedback + update
     ``{role}_feedback_submitted`` flag.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import BACKEND_URL, WEBHOOK_SECRET
from utils.command_handler import (
    sync_cog_commands,
    validate_and_respond,
    is_author,
)
from utils.embeds import (
    BrandColor,
    create_embed,
    error_embed,
    success_embed,
    info_embed,
)
from utils.http import get_http_session
from utils.retry import validation_fail, security_fail, contains_security_threat

logger = logging.getLogger('bot.rooms.interview_feedback')


# ──────────────────────────────────────────────────────────────────────
# Room Select dropdown
# ──────────────────────────────────────────────────────────────────────


class FeedbackRoomSelect(discord.ui.Select):
    """Dropdown listing closed rooms that are eligible for feedback."""

    def __init__(self, rooms: list) -> None:
        self._all_options = []
        for r in rooms:
            label = f'Room {r["room_id"]}'
            job_title = r.get('job_title', '')
            if job_title:
                description = job_title[:100]  # max 100 chars for select option desc
            else:
                description = 'No job title'
            self._all_options.append(
                discord.SelectOption(
                    label=label,
                    value=r['room_id'],
                    description=description,
                )
            )
        if not self._all_options:
            self._all_options.append(
                discord.SelectOption(
                    label='No rooms available',
                    value='none',
                    description='No closed rooms found needing feedback',
                )
            )
        super().__init__(
            placeholder='Select a room to review…',
            options=self._all_options,
            min_values=0,
            max_values=1,
        )
        self.selected_rooms = rooms

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: "FeedbackStartView" = self.view
        if self.values:
            view._selected_room_id = self.values[0]
            selected_label = next(
                (opt.label for opt in self._all_options if opt.value == self.values[0]),
                self.values[0],
            )
            self.placeholder = f"✓ {selected_label}"
        await interaction.response.defer()


# ──────────────────────────────────────────────────────────────────────
# Start View, room dropdown + Write Feedback / Cancel
# ──────────────────────────────────────────────────────────────────────


class FeedbackStartView(discord.ui.View):
    """View with room dropdown, Write Feedback button, and Cancel."""

    def __init__(
        self,
        user_data: dict,
        rooms: list,
        closed_rooms_data: list,
    ) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self.user_data = user_data
        self.rooms = rooms
        self.closed_rooms_data = closed_rooms_data
        self._original_interaction: discord.Interaction | None = None
        self._selected_room_id: str = rooms[0]['room_id'] if rooms else ''

        # Add the room dropdown
        self.add_item(FeedbackRoomSelect(rooms))

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label='Proceed', style=discord.ButtonStyle.success)
    async def write_feedback(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return

        # Find the dropdown to get the selected room_id
        selected_id = self._selected_room_id
        for child in self.children:
            if isinstance(child, FeedbackRoomSelect):
                if child.values:
                    selected_id = child.values[0]
                break

        if not selected_id or selected_id == 'none':
            await interaction.response.edit_message(
                embed=error_embed(message='Could not proceed without selecting a valid room.'),
                view=None,
            )
            return

        # Find room_data for the selected room
        room_data = None
        for r in self.closed_rooms_data:
            if r['room_id'] == selected_id:
                room_data = r
                break

        if not room_data:
            await interaction.response.edit_message(
                embed=error_embed(message='Could not find selected room data.'),
                view=None,
            )
            return

        modal = FeedbackModal(
            user_data=self.user_data,
            room_data=room_data,
            rooms=self.rooms,
            closed_rooms_data=self.closed_rooms_data,
        )
        modal._original_interaction = self._original_interaction
        await interaction.response.send_modal(modal)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return
        self.stop()
        await interaction.response.edit_message(
            embed=info_embed(message='Feedback cancelled.'),
            view=None,
        )


# ──────────────────────────────────────────────────────────────────────
# Modal, feedback text input
# ──────────────────────────────────────────────────────────────────────


class FeedbackModal(discord.ui.Modal, title='Submit Interview Feedback'):
    """Modal that collects feedback text. Room already selected before opening."""

    feedback = discord.ui.TextInput(
        label='Feedback',
        style=discord.TextStyle.paragraph,
        placeholder='Describe your experience (max 100 words)',
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        user_data: dict,
        room_data: dict,
        rooms: list,
        closed_rooms_data: list,
    ) -> None:
        super().__init__(timeout=300)
        self.user_data = user_data
        self.room_data = room_data
        self.rooms = rooms
        self.closed_rooms_data = closed_rooms_data
        self._original_interaction: discord.Interaction | None = None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        feedback_text = self.feedback.value.strip()

        # --- security check first (no retry) ---
        if contains_security_threat(feedback_text):
            await security_fail(
                interaction,
                message='Could not submit feedback. The feedback contains prohibited content.',
            )
            return

        if not feedback_text:
            await validation_fail(
                interaction,
                message='Could not submit feedback. The feedback text cannot be empty.',
                modal_class=FeedbackModal,
                modal_kwargs={
                    'user_data': self.user_data,
                    'room_data': self.room_data,
                },
                retry_label='Try Again',
            )
            return

        word_count = len(feedback_text.split())
        if word_count > 100:
            await validation_fail(
                interaction,
                message=f'Could not submit feedback. Feedback must be at most 100 words (currently {word_count}).',
                modal_class=FeedbackModal,
                modal_kwargs={
                    'user_data': self.user_data,
                    'room_data': self.room_data,
                },
                retry_label='Try Again',
            )
            return

        # Defer so we can edit the original response later
        await interaction.response.defer()

        # Store feedback text and show rating selection
        view = RatingSelectView(
            user_data=self.user_data,
            room_data=self.room_data,
            feedback_text=feedback_text,
            original_interaction=self._original_interaction,
            rooms=self.rooms,
            closed_rooms_data=self.closed_rooms_data,
        )
        view.author_id = interaction.user.id

        embed = create_embed(
            title='Rate Your Experience',
            description=(
                'Your feedback has been received. Now please rate your '
                'experience in this interview room.\n\n'
                f'**Room:** `{self.room_data.get("room_id", "")}`\n'
                f'**Job:** {self.room_data.get("job_title", "")}'
            ),
            color=BrandColor.PRIMARY,
            footer='Xentra • Rooms',
        )

        await interaction.edit_original_response(
            embed=embed,
            view=view,
        )


# ──────────────────────────────────────────────────────────────────────
# Rating Select View, 1–5 dropdown + Submit / Cancel
# ──────────────────────────────────────────────────────────────────────


class RatingSelect(discord.ui.Select):
    """Dropdown to select a rating from 1 to 5."""

    def __init__(self) -> None:
        self._all_options = [
            discord.SelectOption(label='⭐ 1 – Very Poor', value='1'),
            discord.SelectOption(label='⭐⭐ 2 – Poor', value='2'),
            discord.SelectOption(label='⭐⭐⭐ 3 – Average', value='3'),
            discord.SelectOption(label='⭐⭐⭐⭐ 4 – Good', value='4'),
            discord.SelectOption(label='⭐⭐⭐⭐⭐ 5 – Excellent', value='5'),
        ]
        super().__init__(
            placeholder='Select your rating…',
            options=self._all_options,
            min_values=0,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_author(interaction, self.view):
            return
        view: "RatingSelectView" = self.view
        if self.values:
            view._selected_rating = int(self.values[0])
            selected_label = next(
                (opt.label for opt in self._all_options if opt.value == self.values[0]),
                self.values[0],
            )
            self.placeholder = f"✓ {selected_label}"
        await interaction.response.defer()


class RatingSelectView(discord.ui.View):
    """View with rating dropdown and Submit/← Back buttons."""

    def __init__(
        self,
        user_data: dict,
        room_data: dict,
        feedback_text: str,
        original_interaction: discord.Interaction,
        rooms: list,
        closed_rooms_data: list,
    ) -> None:
        super().__init__(timeout=120)
        self.author_id: int | None = None
        self._done = False
        self.user_data = user_data
        self.room_data = room_data
        self.feedback_text = feedback_text
        self._original_interaction = original_interaction
        self.rooms = rooms
        self.closed_rooms_data = closed_rooms_data
        self._selected_rating: int = 3  # default

        self.add_item(RatingSelect())

    async def on_timeout(self) -> None:
        self.stop()

    @discord.ui.button(label='Submit Feedback', style=discord.ButtonStyle.success)
    async def submit_feedback(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return
        if self._done:
            return
        self._done = True

        await interaction.response.defer()

        session = get_http_session()
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        # POST to backend to save feedback
        save_payload = {
            'discord_id': str(interaction.user.id),
            'room_id': self.room_data.get('room_id', ''),
            'feedback': self.feedback_text,
            'rating': self._selected_rating,
        }

        try:
            async with session.post(
                f'{BACKEND_URL}rooms/bot/save-feedback/',
                json=save_payload,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    err_data = await resp.json()
                    err_msg = err_data.get('error', 'The service is temporarily unavailable.')
                    await interaction.edit_original_response(
                        embed=error_embed(message=err_msg),
                        view=None,
                    )
                    return
                save_data = await resp.json()
                feedback_id = save_data.get('feedback_id', '')
        except Exception:
            logger.exception('Failed to save feedback to backend')
            await interaction.edit_original_response(
                embed=error_embed(
                    message='The service is temporarily unavailable.',
                ),
                view=None,
            )
            return

        # Success
        success_msg = (
            f'Feedback submitted for room `{self.room_data.get("room_id", "")}`. '
            f'Your rating: **{self._selected_rating}/5**'
        )
        if feedback_id:
            success_msg += f'\nFeedback ID: `{feedback_id}`'

        await interaction.edit_original_response(
            embed=success_embed(message=success_msg),
            view=None,
        )

    @discord.ui.button(label='← Back', style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        if not is_author(interaction, self):
            return
        # Go back to FeedbackStartView (room selection)
        start_view = FeedbackStartView(
            user_data=self.user_data,
            rooms=self.rooms,
            closed_rooms_data=self.closed_rooms_data,
        )
        start_view.author_id = interaction.user.id
        start_view._original_interaction = self._original_interaction
        embed = create_embed(
            title='Submit Interview Feedback',
            description=(
                'Select a closed interview room to leave feedback.\n\n'
                f'**Job:** {self.room_data.get("job_title", "")}'
            ),
            color=BrandColor.PRIMARY,
            footer='Xentra • Rooms',
        )
        await interaction.response.edit_message(embed=embed, view=start_view)


# ──────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────


class InterviewFeedback(commands.Cog):
    """``/interview feedback``, Submit feedback about interview room."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        sync_cog_commands(self)

    @app_commands.command(
        name='interview_feedback',
        description='...',
    )
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def interview_feedback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Submit feedback about a closed interview room."""

        async def callback(user_data: dict):
            headers = {'X-Webhook-Token': WEBHOOK_SECRET}
            discord_id = str(interaction.user.id)

            # ── 1. Fetch closed rooms without feedback ────────────────
            params = {
                'discord_id': discord_id,
            }

            session = get_http_session()
            try:
                async with session.get(
                    f'{BACKEND_URL}rooms/bot/closed-rooms/',
                    params=params,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        err_data = await resp.json()
                        err_msg = err_data.get('error', 'Could not load rooms.')
                        return error_embed(message=err_msg), None
                    data = await resp.json()
                    rooms = data.get('rooms', [])
            except Exception:
                logger.exception('Failed to fetch closed rooms')
                return error_embed(
                    message='Could not load rooms awaiting feedback.',
                ), None

            if not rooms:
                return error_embed(
                    message='Could not load any rooms awaiting feedback.',
                ), None

            # ── 2. Show room selection view ────────────────────────
            embed = create_embed(
                title='Interview Feedback',
                description=(
                    '> Please select a closed interview room to submit feedback for.\n\n'
                    f'> You have **{len(rooms)}** room(s) awaiting feedback.'
                ),
                color=BrandColor.PRIMARY,
                footer='Xentra • Rooms',
            )

            view = FeedbackStartView(
                user_data,
                rooms,
                rooms,
            )
            view.author_id = interaction.user.id
            view._original_interaction = interaction

            return embed, view

        await validate_and_respond(interaction, callback)


# ── setup ────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InterviewFeedback(bot))
    logger.info('InterviewFeedback cog loaded')
