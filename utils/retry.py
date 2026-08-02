"""
Centralized retry mechanism for model-backed commands.

Provides reusable helpers so every validation failure across all 30+ commands
uses a consistent ``info_embed`` + retry-button pattern instead of a dead-end
``error_embed``.

Usage
-----
    from utils.retry import validation_fail, security_fail, retry_view

    # Validation error (empty data, word count exceeded, etc.), retry allowed
    await validation_fail(
        interaction,
        message='Title cannot be empty.',
        modal_class=MyModal,
        modal_kwargs={'prefill': {...}},
    )

    # Security threat (XSS, injection), command killed, no retry
    await security_fail(interaction, message='Invalid input detected.')

    # Custom retry view for non-modal contexts
    view = retry_view(modal_class=MyModal, kwargs={...})
    await interaction.response.edit_message(embed=embed, view=view)
"""

from __future__ import annotations

import discord
import logging
from typing import Any, Type

from utils.embeds import info_embed, error_embed

logger = logging.getLogger('bot.utils.retry')


# ---------------------------------------------------------------------------
#  RetryView, a view with a single button that re-opens a modal
# ---------------------------------------------------------------------------

def retry_view(
    modal_class: Type[discord.ui.Modal],
    kwargs: dict[str, Any],
    label: str = 'Try Again',
    style: discord.ButtonStyle = discord.ButtonStyle.primary,
) -> discord.ui.View:
    """Return a View whose only button opens *modal_class* with *kwargs*.

    Parameters
    ----------
    modal_class:
        The modal class to instantiate.
    kwargs:
        Keyword arguments passed to the modal constructor.
    label:
        Button label (default ``'Try Again'``).  Use ``'Continue'`` or
        ``'Next Milestone'`` for modal-chaining workarounds.
    style:
        Button colour (default ``ButtonStyle.primary``).
    """
    view = discord.ui.View(timeout=300)

    class _RetryButton(discord.ui.Button):
        def __init__(self) -> None:
            super().__init__(label=label, style=style)

        async def callback(self, btn_interaction: discord.Interaction) -> None:
            try:
                modal = modal_class(**kwargs)
                await btn_interaction.response.send_modal(modal)
            except Exception:
                logger.exception('Failed to open retry modal')
                await btn_interaction.response.edit_message(
                    embed=error_embed(message='Could not re-open the form.'),
                    view=None,
                )

    view.add_item(_RetryButton())
    return view


# ---------------------------------------------------------------------------
#  Validation failure, info embed + retry button
# ---------------------------------------------------------------------------

async def validation_fail(
    interaction: discord.Interaction,
    message: str,
    modal_class: Type[discord.ui.Modal] | None = None,
    modal_kwargs: dict[str, Any] | None = None,
    retry_label: str = 'Try Again',
    *,
    ephemeral: bool = True,
) -> None:
    """Show an info embed with a retry button that re-opens the modal.

    Use for **every** validation failure: empty data, word-count exceeded,
    budget too low, unparseable date, etc.

    Parameters
    ----------
    interaction:
        The Discord interaction to respond to.
    message:
        User-friendly message explaining what went wrong.
    modal_class:
        The modal class to re-open.  When ``None``, no retry button is added
        (for non-modal commands such as ``/interview budget``).
    modal_kwargs:
        Keyword arguments for the modal constructor.  **Must include pre-filled
        fields** so the user doesn't lose their input.
    retry_label:
        Label for the retry button.
    ephemeral:
        Whether the response should be ephemeral (default ``False``).
    """
    embed = info_embed(message=message)
    view: discord.ui.View | None = None

    if modal_class is not None:
        view = retry_view(modal_class, modal_kwargs or {}, label=retry_label)

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
    except Exception:
        logger.exception('validation_fail: failed to send response')


# ---------------------------------------------------------------------------
#  Security threat, error embed + kill (no retry)
# ---------------------------------------------------------------------------

async def security_fail(
    interaction: discord.Interaction,
    message: str = 'Security violation detected. The command has been terminated.',
    *,
    ephemeral: bool = True,
) -> None:
    """Show an error embed with **no** retry button, command is terminated.

    Use when input contains XSS, SQL injection, or other malicious patterns.
    """
    embed = error_embed(message=message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.edit_message(embed=embed, view=None)
    except Exception:
        logger.exception('security_fail: failed to send response')


# ---------------------------------------------------------------------------
#  Basic security-threat detection (shared helper)
# ---------------------------------------------------------------------------

# Patterns that are NEVER legitimate in any text field
_SECURITY_PATTERNS = [
    '<script', '</script>', 'javascript:', 'onclick=', 'onerror=',
    'onload=', 'onmouseover=', 'onfocus=', 'onchange=',
    '<!--', '-->', '<?php', '<%', '%>',
    'DROP TABLE', 'ALTER TABLE', 'DELETE FROM',
    ' UNION ', ' UNION ALL ', '-- ',
    '\\\\',  # SQL injection variants
]

def contains_security_threat(text: str) -> bool:
    """Return ``True`` if *text* contains known malicious patterns.

    All commands **must** call this on raw user input **before** sending it
    to the backend.  If it returns ``True``, call ``security_fail()`` instead
    of ``validation_fail()``.
    """
    if not text:
        return False
    lower = text.lower()
    for pattern in _SECURITY_PATTERNS:
        if pattern in lower:
            logger.warning('Security threat detected: pattern=%r in text=%r', pattern, text[:100])
            return True
    return False
