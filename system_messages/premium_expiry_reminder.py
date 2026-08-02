"""
Embed builder for ``premium_expiry_reminder`` system messages.

DM-only notification (not room-scoped, no room headers).
Mirrors the pattern established by ``security_bypass_attempt.py``:
exports ``build_embed(data) -> discord.Embed``.

Message intent
--------------
The reminder is intentionally **generic** — it does not say "monthly" or
"yearly".  It simply informs the user that their premium benefits are about
to end, so the auto-renew DM works regardless of which billing interval was
purchased.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct a premium-expiry reminder DM embed.

    This is a DM-only message (not room-scoped), so it returns a plain
    embed without room headers.

    Expected payload keys
    ---------------------
    discord_id : str
        Snowflake of the target user (used by system_message_handler).
    discord_username : str
        Discord username (fallback for the greeting).
    display_name : str
        Profile display name (Freelancer/Client ``username``) for the greeting.
    expires_at : str | None
        ISO-8601 timestamp of when premium expires.
    """
    username = data.get("display_name") or data.get("discord_username", "there")
    expires_at = data.get("expires_at")

    description = (
        f"> ***Your premium benefits are about to expire.***\n"
        f"\n"
        f"**User:** `{username}`"
    )

    if expires_at:
        description += f"\n**Expires:** `{expires_at}`"

    description += (
        f"\n\n"
        f"> __Renew your premium plan to continue enjoying exclusive features.__"
    )

    return create_embed(
        title="Premium Expiring Soon",
        description=description,
        color=BrandColor.WARNING,
        footer="Xentra • Premium",
    )
