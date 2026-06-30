"""
Embed builder for ``room_rules`` system messages.

Sent to both parties when an interview room is created, outlining the
rules of conduct.

Expected data keys
------------------
- discord_id (str) — Snowflake of the receiver (used by handler).
- room_id (str)    — The interview room ID.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct a rules-of-conduct embed for interview room participants."""
    room_id = data.get("room_id", "N/A")

    rules = [
        "1. **Be Respectful** — Treat the other party with professionalism.",
        "2. **Stay on Topic** — Keep discussions relevant to the job.",
        "3. **No Harassment** — Zero tolerance for abusive behaviour.",
        "4. **Confidentiality** — Do not share room content outside Xentra.",
        "5. **Timely Responses** — Reply within a reasonable timeframe.",
        "6. **Report Issues** — Use \`/interview complain\` if you experience problems.",
    ]

    return create_embed(
        title="📋 Interview Room Rules",
        description=(
            f"**Room:** `{room_id}`\n\n"
            "Please follow these guidelines to ensure a smooth interview process:\n\n"
            + "\n".join(rules) +
            "\n\n_By participating in this room you agree to abide by these rules._"
        ),
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
