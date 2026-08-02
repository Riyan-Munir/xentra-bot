"""
Embed builder for ``room_guide_client`` system messages.

Sent to the client when an interview room has been successfully created.

Expected data keys
------------------
- discord_id (str), Snowflake of the client (used by handler).
- room_id (str), The interview room ID.
- freelancer_name (str), Name of the freelancer.
- job_title (str), Title of the job.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct a confirmation embed for the client.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    freelancer_name = data.get("freelancer_name", "Freelancer")
    job_title = data.get("job_title", "the job")

    body = (
        f"> ***A new interview room has been opened for your application.***\n"
        f"\n"
        f"**Freelancer:** `{freelancer_name}`\n"
        f"\n"
        f"> __Use /interview message to communicate with the freelancer.__"
    )

    description = (
        f"> ***Job: `{job_title}`***\n"
        f"\n"
        f"{body}"
    )

    embed = create_embed(
        title="Interview Room Created",
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
    return embed, body
