"""
Embed builder for ``room_job_cancelled`` system messages.

Sent to the freelancer when a client cancels a job that had an active
agreement.  The freelancer's application is set to rejected and the
linked agreement is expired.

Expected data keys
------------------
- discord_id (str), Snowflake of the freelancer (used by handler).
- room_id (str), The interview room ID.
- job_id (str), The cancelled job ID.
- job_title (str), Title of the cancelled job.
- client_name (str), Display name of the client who cancelled.
- application_id (str, optional), The freelancer's application ID.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct a job-cancelled notification embed for the freelancer.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    room_id = data.get("room_id", "N/A")
    job_title = data.get("job_title", "a job")
    job_id = data.get("job_id", "N/A")
    client_name = data.get("client_name", "The client")
    application_id = data.get("application_id", "")

    body_parts = [
        f"> ***A job with an active agreement has been cancelled.***",
        "",
        f"**Job ID:** `{job_id}`",
        f"**Client:** `{client_name}`",
    ]

    if application_id:
        body_parts.append(f"**Application:** `{application_id}`")

    body_parts.extend([
        "",
        "> __If you had an active agreement, the room will be closed.__",
    ])

    body = "\n".join(body_parts)

    description = (
        f"> ***Room: `{room_id}`***\n"
        f"> ***Job: `{job_title}`***\n"
        f"\n"
        f"{body}"
    )

    embed = create_embed(
        title="Job Cancelled",
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Jobs",
    )
    return embed, body
