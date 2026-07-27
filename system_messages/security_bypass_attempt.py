"""
Security bypass attempt notification.

Injected as a system message DM when a user triggers an advanced
security event.  Built as a tiered embed with escalating urgency.
"""

from typing import Any

import discord

from utils.embeds import create_embed, BrandColor

__all__ = ["build_embed"]

# ═══════════════════════════════════════════════════════════════════
#  Tier-level metadata (unused values left for readability)
# ═══════════════════════════════════════════════════════════════════

_TIER_COLORS: dict[int, int] = {
    1: BrandColor.WARNING,
    2: BrandColor.WARNING,
    3: BrandColor.ERROR,
    4: BrandColor.ERROR,
    5: BrandColor.BAN,
}

_TIER_TITLES: dict[int, str] = {
    1: "Low Concern — Security Alert",
    2: "Elevated Concern — Security Alert",
    3: "Serious Concern — Security Alert",
    4: "Critical Concern — Security Alert",
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

    event_line = (
        f"**Event:** `{event_type}`\n"
        f"**IP Address:** `{ip}`\n"
        f"**Path:** `{path}`\n"
        f"**Detail:** {detail}"
    )

    # ── Tier-specific label & action ────────────────────────────────
    if tier == 5:
        tier_label = "**Auto-ban:**"
        action = "Please log into the **Xentra Dashboard** to appeal this suspension."
    elif tier == 4:
        tier_label = "**Critical:**"
        action = "Please log into the **Xentra Dashboard** to acknowledge this notification immediately."
    elif tier == 3:
        tier_label = "**Serious:**"
        action = "Please log into the **Xentra Dashboard** to acknowledge this notification."
    elif tier == 2:
        tier_label = "**Elevated:**"
        action = "Please log into the **Xentra Dashboard** to review your account activity."
    else:  # Tier 1 (default)
        tier_label = ""
        action = "If you believe this is a mistake, please log into the **Xentra Dashboard** to submit an appeal."

    return (
        f"A security bypass attempt was detected on your account.\n\n"
        f"{event_line}\n\n"
        f"**Total attempts:** `{attempt_count}`\n\n"
        f"{tier_label} {tier_msg}\n\n"
        f"> {action}"
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
