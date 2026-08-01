"""
Embed builder for ``room_job_cancelled`` system messages.

Sent to the freelancer when a client cancels a job that had an active
agreement.  The freelancer's application is set to rejected and the
linked agreement is expired.

Expected data keys
------------------
- discord_id (str), Snowflake of the freelancer (used by handler).
- job_id (str), The cancelled job ID.
- job_title (str), Title of the cancelled job.
- client_name (str), Display name of the client who cancelled.
- application_id (str, optional), The freelancer's application ID.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct a job-cancelled notification embed for the freelancer."""
    job_id = data.get("job_id", "N/A")
    job_title = data.get("job_title", "a job")
    client_name = data.get("client_name", "The client")
    application_id = data.get("application_id", "")

    description_parts = [
        f"**Job ID:** `{job_id}`",
        f"**Job Title:** **{job_title}**",
        f"**Cancelled By:** **{client_name}**",
    ]

    if application_id:
        description_parts.append(f"**Application ID:** `{application_id}`")

    description_parts.extend([
        "",
        "The client has cancelled this job. Your application has been "
        "set to **rejected** and any linked agreement has been expired.",
        "",
        "You may browse open jobs with `/jobs discover`.",
    ])

    return create_embed(
        title="Job Cancelled by Client",
        description="\n".join(description_parts),
        color=BrandColor.PRIMARY,
        footer="Xentra • Job system",
    )
