"""
Unified validation-failure handling for modal/dropdown commands.

Rule: a command's 1st message is the ONLY interactive surface and stays
alive until the command genuinely ends (success / cancel).  Every validation
failure is reported as a 2nd ephemeral message with NO buttons, so the user
simply re-clicks the 1st message's button (Proceed / Write …) to re-open the
modal with their previous input preserved.

Usage
-----
    from utils.retry import validation_fail, security_fail

    # Validation error (empty data, word count exceeded, etc.) → ephemeral, no buttons
    await validation_fail(interaction, message='Title cannot be empty.')

    # Security threat (XSS, injection), input rejected, no further processing
    await security_fail(interaction, message='Invalid input detected.')
"""

from __future__ import annotations

import discord
import logging

from utils.embeds import error_embed

logger = logging.getLogger('bot.utils.retry')


# ---------------------------------------------------------------------------
#  Validation failure, ephemeral error embed with NO buttons
# ---------------------------------------------------------------------------

async def validation_fail(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    """Send a validation-error embed as a 2nd ephemeral message with no buttons.

    The command's 1st message (with its view) stays alive so the user can
    re-open the modal or proceed after fixing their input.  When a modal is
    re-opened from the 1st message it must restore the previously entered
    data (prefill) so nothing is lost.
    """
    embed = error_embed(message=message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
    except Exception:
        logger.exception('validation_fail: failed to send response')


# ---------------------------------------------------------------------------
#  Security threat, ephemeral error embed (input rejected)
# ---------------------------------------------------------------------------

async def security_fail(
    interaction: discord.Interaction,
    message: str = 'Security violation detected. The command has been terminated.',
    *,
    ephemeral: bool = True,
) -> None:
    """Show an error embed with no buttons when input is malicious.

    The 1st message stays interactive so the user may cancel the command;
    no further processing of the malicious input is performed.
    """
    embed = error_embed(message=message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
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
