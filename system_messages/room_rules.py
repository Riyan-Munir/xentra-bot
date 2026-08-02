"""
Embed builder for ``room_rules`` system messages.

Sent to both parties when an interview room is created, outlining the
rules of conduct.

Expected data keys
------------------
- discord_id (str), Snowflake of the receiver (used by handler).
- room_id (str)   , The interview room ID.
- job_title (str) , Title of the job.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct a rules-of-conduct embed for interview room participants.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    room_id = data.get("room_id", "N/A")
    job_title = data.get("job_title", "N/A")

    rules = (
        "`1.` Be respectful and professional at all times.\n"
        "`2.` Discuss only matters related to the job.\n"
        "`3.` Do not share personal contact information.\n"
        "`4.` Use /interview message to communicate.\n"
        "`5.` Use /interview complain to report violations.\n"
        "`6.` Do not attempt to bypass system restrictions."
    )

    body = (
        f"> ***Please review and follow these rules.***\n"
        f"\n"
        f"{rules}\n"
        f"\n"
        f"> __Violations may result in room closure or account restrictions.__"
    )

    description = (
        f"> ***Room: `{room_id}`***\n"
        f"> ***Job: `{job_title}`***\n"
        f"\n"
        f"{body}"
    )

    embed = create_embed(
        title="Interview Room Rules",
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
    return embed, body
