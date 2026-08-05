"""
Embed builder for ``interview_room_job_details`` system messages.

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
from system_messages.interview_room_system import create_room_embed

TITLE = "Job Details"


def build_embed(data: dict) -> tuple[discord.Embed, str, str]:
    """Construct a job-details embed for interview room participants.

    Returns ``(embed, body_text, title)`` where ``body_text`` is the
    transcript-safe version without room headers and ``title`` is the
    message title used when persisting (``"Title\n\nBody"``).
    """
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

    embed = create_room_embed(
        title=TITLE,
        body=body,
        data=data,
    )
    return embed, body, TITLE
