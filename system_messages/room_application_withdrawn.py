"""
Embed builder for ``room_application_withdrawn`` system messages.

Sent to the client when a freelancer withdraws an application that was
in **accepted** status.  The linked agreement is expired and the job
is reopened.

Expected data keys
------------------
- discord_id (str), Snowflake of the client (used by handler).
- room_id (str), The interview room ID.
- job_id (str), The job ID.
- job_title (str), Title of the job.
- freelancer_name (str), Display name of the freelancer who withdrew.
- application_id (str), The withdrawn application ID.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct an application-withdrawn notification embed for the client.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    room_id = data.get("room_id", "N/A")
    job_title = data.get("job_title", "a job")
    job_id = data.get("job_id", "N/A")
    freelancer_name = data.get("freelancer_name", "The freelancer")
    application_id = data.get("application_id", "N/A")

    body = (
        f"> ***An accepted application has been withdrawn.***\n"
        f"\n"
        f"**Job ID:** `{job_id}`\n"
        f"**Freelancer:** `{freelancer_name}`\n"
        f"**Application:** `{application_id}`\n"
        f"\n"
        f"> __The interview room will be closed.__"
    )

    description = (
        f"> ***Room: `{room_id}`***\n"
        f"> ***Job: `{job_title}`***\n"
        f"\n"
        f"{body}"
    )

    embed = create_embed(
        title="Application Withdrawn",
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Job system",
    )
    return embed, body
