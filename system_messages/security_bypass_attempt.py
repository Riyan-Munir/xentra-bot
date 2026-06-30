"""
Embed builder for ``security_bypass_attempt`` system messages.

Structure
---------
This module mirrors the commands pattern: one file per message type, each
exporting a ``build_embed(data)`` function that returns a ``discord.Embed``.

Callers (via ``system_message_handler``) pass the full payload dict after
unwrapping.  Expected keys are defined in ``data/system_messages.json``.

Tier-based messaging
--------------------
The embed content changes based on ``bypass_tier`` (1-5):

+-------+-------------------+-------------------------------------------+
| Tier  | Label             | Message                                   |
+-------+-------------------+-------------------------------------------+
| 1     | Low concern       | Informational warning                     |
| 2     | Elevated concern  | Warning with rate-limit notice            |
| 3     | Serious concern   | Feature restriction notice                |
| 4     | Critical concern  | Near-ban warning — contact admin          |
| 5     | Auto-ban          | Account suspended                         |
+-------+-------------------+-------------------------------------------+

The ``auto_ban_at`` threshold is **never** revealed to the user — only the
current attempt count and the tier-appropriate message are shown.
"""

import discord
from utils.embeds import create_embed, BrandColor


# ── Tier-specific embed colours ───────────────────────────────────────
_TIER_COLORS: dict[int, int] = {
    1: BrandColor.INFO,       # Blue — informational
    2: BrandColor.WARNING,    # Yellow — caution
    3: BrandColor.WARNING,    # Yellow — serious
    4: BrandColor.ERROR,      # Red — critical
    5: 0x2F3136,              # Dark grey — banned/suspended
}

# ── Tier-specific titles ──────────────────────────────────────────────
_TIER_TITLES: dict[int, str] = {
    1: "Security Notice",
    2: "Security Warning",
    3: "Security Alert — Action Required",
    4: "Critical Security Alert",
    5: "Account Suspended",
}


def _tier_embed_body(tier: int, data: dict) -> str:
    """Generate the embed description body for a given tier."""
    event_type = data.get("event_type", "unknown")
    ip = data.get("ip", "unknown")
    path = data.get("path", "unknown")
    detail = data.get("detail", "No additional details provided.")
    attempt_count = data.get("total_attempts", 1)
    tier_msg = data.get("tier_msg", "")

    event_line = f"**Event:** `{event_type}`\n**IP Address:** `{ip}`\n**Path:** `{path}`\n**Detail:** {detail}"

    if tier == 5:
        # Account suspended — no event details, just ban info
        return (
            f"Your account has been **automatically suspended** due to "
            f"repeated security violations.\n\n"
            f"**Total attempts recorded:** `{attempt_count}`\n\n"
            f"> If you believe this is a mistake, please contact a server "
            f"administrator to appeal the suspension."
        )

    if tier == 4:
        return (
            f"A security bypass attempt was detected on your account.\n\n"
            f"{event_line}\n\n"
            f"**Total attempts:** `{attempt_count}`\n\n"
            f"**Critical:** {tier_msg}\n\n"
            f"> Please log in to the **Xentra Dashboard** to acknowledge this "
            f"notification immediately. Contact a server administrator if you "
            f"need assistance."
        )

    if tier == 3:
        return (
            f"A security bypass attempt was detected on your account.\n\n"
            f"{event_line}\n\n"
            f"**Total attempts:** `{attempt_count}`\n\n"
            f"**{tier_msg}**\n\n"
            f"> Some dashboard features may be restricted. Please log in to "
            f"the **Xentra Dashboard** to acknowledge this notification."
        )

    if tier == 2:
        return (
            f"A security bypass attempt was detected on your account.\n\n"
            f"{event_line}\n\n"
            f"**Total attempts:** `{attempt_count}`\n\n"
            f"**{tier_msg}**\n\n"
            f"> Please log in to the **Xentra Dashboard** to review your "
            f"account activity."
        )

    # Tier 1 (default)
    return (
        f"A **security bypass attempt** was detected on your account.\n\n"
        f"{event_line}\n\n"
        f"**Total attempts:** `{attempt_count}`\n\n"
        f"{tier_msg}\n\n"
        f"> Please log in to the **Xentra Dashboard** to acknowledge this "
        f"notification and restore full access to your account. If you "
        f"believe this is a mistake, contact a server administrator."
    )


def build_embed(data: dict) -> discord.Embed:
    """Construct a tier-aware security-alert DM embed for a bypass attempt."""
    tier = data.get("bypass_tier", 1)
    title = _TIER_TITLES.get(tier, "Security Alert")
    color = _TIER_COLORS.get(tier, BrandColor.ERROR)
    description = _tier_embed_body(tier, data)

    return create_embed(
        title=title,
        description=description,
        color=color,
        footer="Xentra • Security system",
    )
