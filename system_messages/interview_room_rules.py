"""
Embed builder for ``interview_room_rules`` system messages.

Sent to both parties when an interview room is created, outlining the
rules of conduct.

Expected data keys
------------------
- discord_id (str), Snowflake of the receiver (used by handler).
- room_id (str)   , The interview room ID.
- job_title (str) , Title of the job.
"""

import discord
from system_messages.interview_room_system import create_room_embed

TITLE = "Interview Room Rules"


def build_embed(data: dict) -> tuple[discord.Embed, str, str]:
    """Construct a rules-of-conduct embed for interview room participants.

    Returns ``(embed, body_text, title)`` where ``body_text`` is the
    transcript-safe version without room headers and ``title`` is the
    message title used when persisting (``"Title\n\nBody"``).
    """
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

    embed = create_room_embed(
        title=TITLE,
        body=body,
        data=data,
    )
    return embed, body, TITLE
