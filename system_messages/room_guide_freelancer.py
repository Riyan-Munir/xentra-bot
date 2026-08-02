"""
Embed builder for ``room_guide_freelancer`` system messages.

Sent to the freelancer when a client creates an interview room for them.

Expected data keys
------------------
- discord_id (str), Snowflake of the freelancer (used by handler).
- room_id (str), The interview room ID.
- client_name (str), Name of the client who owns the job.
- job_title (str), Title of the job.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct an invitation embed for the freelancer.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    room_id = data.get("room_id", "N/A")
    client_name = data.get("client_name", "A client")
    job_title = data.get("job_title", "a job")

    body = (
        f"> ***You have been invited to an interview room.***\n"
        f"\n"
        f"**Client:** `{client_name}`\n"
        f"\n"
        f"> __Use /interview message to communicate with the client.__"
    )

    description = (
        f"> ***Room: `{room_id}`***\n"
        f"> ***Job: `{job_title}`***\n"
        f"\n"
        f"{body}"
    )

    embed = create_embed(
        title="Interview Invitation",
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
    return embed, body
