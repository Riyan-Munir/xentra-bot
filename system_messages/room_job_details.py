"""
Embed builder for ``room_job_details`` system messages.

Sent to both parties when an interview room is created, showing the
job's title, description, budget range, and deadline (if set).

Expected data keys
------------------
- discord_id (str)     , Snowflake of the receiver (used by handler).
- room_id (str)        , The interview room ID.
- job_title (str)      , Title of the job.
- job_description (str), Description/body of the job.
- budget_min (str)     , Minimum budget.
- budget_max (str)     , Maximum budget.
- deadline (str, opt)  , Job deadline date.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct a job-details embed for interview room participants.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    room_id = data.get("room_id", "N/A")
    job_title = data.get("job_title", "N/A")
    job_description = data.get("job_description", "No description provided.")
    budget_min = data.get("budget_min", "—")
    budget_max = data.get("budget_max", "—")
    deadline = data.get("deadline")

    body_parts = [
        f"> ***Details for the job linked to this interview room.***",
        "",
        f"**Budget:** `${budget_min} - ${budget_max}`",
    ]

    if deadline:
        body_parts.append(f"**Deadline:** `{deadline}`")

    body_parts.extend([
        "",
        "**Description:**",
        f"> {job_description}",
    ])

    body = "\n".join(body_parts)

    description = (
        f"> ***Room: `{room_id}`***\n"
        f"> ***Job: `{job_title}`***\n"
        f"\n"
        f"{body}"
    )

    embed = create_embed(
        title="Job Details",
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
    return embed, body
