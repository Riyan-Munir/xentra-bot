"""
Embed builder for ``premium_expiry_reminder`` system messages.

Structure
---------
This module mirrors the pattern established by ``security_bypass_attempt.py``:
it exports a ``build_embed(data)`` function that returns a ``discord.Embed``.

Callers (via ``system_message_handler``) pass the full payload dict after
unwrapping.  Expected keys are defined in ``data/system_messages.json``.

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

    Expected payload keys
    ---------------------
    discord_id : str
        Snowflake of the target user (used by system_message_handler).
    discord_username : str
        Display name for the greeting.
    expires_at : str | None
        ISO-8601 timestamp of when premium expires.
    """
    username = data.get("discord_username", "there")
    expires_at = data.get("expires_at")

    description = (
        f"Hey **{username}**, your **Xentra Premium** benefits are about to end."
    )

    if expires_at:
        description += (
            f"\n\nYour subscription will expire on **{expires_at}**."
        )

    description += (
        "\n\nRenew now to keep enjoying unlimited access to premium features:\n"
        "‣ Unlimited job listings & applications\n"
        "‣ Priority discovery in search results\n"
        "‣ Extended portfolio with up to 6 skill tags\n"
        "‣ And more…"
    )

    description += (
        f"\n\n> Head over to the **Xentra Dashboard** to renew your subscription."
    )

    return create_embed(
        title="⏰ Premium Benefits Ending Soon",
        description=description,
        color=BrandColor.WARNING,
        footer="Xentra • Premium",
    )
