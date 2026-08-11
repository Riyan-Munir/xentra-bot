"""
Embed builder for ``job_room_guide_client`` system messages.

Sent to the client when a job room has been successfully created.

Expected data keys
------------------
- discord_id (str), Snowflake of the client (used by handler).
- room_id (str), The job room ID.
- freelancer_name (str), Name of the freelancer.
- job_title (str), Title of the job.
"""

import discord
from system_messages.interview_room_system import create_room_embed

TITLE = "Job Room Created"


def build_embed(data: dict) -> tuple[discord.Embed, str, str]:
    """Construct a confirmation embed for the client.

    Returns ``(embed, body_text, title)`` where ``body_text`` is the
    transcript-safe version without room headers and ``title`` is the
    message title used when persisting (``"Title\n\nBody"``).
    """
    freelancer_name = data.get("freelancer_name", "Freelancer")

    body = (
        f"> ***A new job room has been opened for your signed agreement.***\n"
        f"\n"
        f"**Freelancer:** `{freelancer_name}`\n"
        f"\n"
        f"> __Use /job message to communicate with the freelancer.__"
    )

    embed = create_room_embed(
        title=TITLE,
        body=body,
        data=data,
        room_type='job',
    )
    return embed, body, TITLE
