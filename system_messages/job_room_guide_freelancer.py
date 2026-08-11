"""
Embed builder for ``job_room_guide_freelancer`` system messages.

Sent to the freelancer when a client creates a job room for them.

Expected data keys
------------------
- discord_id (str), Snowflake of the freelancer (used by handler).
- room_id (str), The job room ID.
- client_name (str), Name of the client who owns the job.
- job_title (str), Title of the job.
"""

import discord
from system_messages.interview_room_system import create_room_embed

TITLE = "Job Room Invitation"


def build_embed(data: dict) -> tuple[discord.Embed, str, str]:
    """Construct an invitation embed for the freelancer.

    Returns ``(embed, body_text, title)`` where ``body_text`` is the
    transcript-safe version without room headers and ``title`` is the
    message title used when persisting (``"Title\n\nBody"``).
    """
    client_name = data.get("client_name", "A client")

    body = (
        f"> ***You have been invited to a job room.***\n"
        f"\n"
        f"**Client:** `{client_name}`\n"
        f"\n"
        f"> __Use /job message to communicate with the client.__"
    )

    embed = create_room_embed(
        title=TITLE,
        body=body,
        data=data,
        room_type='job',
    )
    return embed, body, TITLE
