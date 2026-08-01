"""
Embed builder for ``room_application_withdrawn`` system messages.

Sent to the client when a freelancer withdraws an application that was
in **accepted** status.  The linked agreement is expired and the job
is reopened.

Expected data keys
------------------
- discord_id (str), Snowflake of the client (used by handler).
- job_id (str), The job ID.
- job_title (str), Title of the job.
- freelancer_name (str), Display name of the freelancer who withdrew.
- application_id (str), The withdrawn application ID.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct an application-withdrawn notification embed for the client."""
    job_id = data.get("job_id", "N/A")
    job_title = data.get("job_title", "a job")
    freelancer_name = data.get("freelancer_name", "The freelancer")
    application_id = data.get("application_id", "N/A")

    description_parts = [
        f"**Job ID:** `{job_id}`",
        f"**Job Title:** **{job_title}**",
        f"**Freelancer:** **{freelancer_name}**",
        f"**Application ID:** `{application_id}`",
        "",
        "The freelancer has withdrawn their application. The linked "
        "agreement has been expired and the job has been **reopened**.",
        "",
        "You may review new applications with `/job applications`.",
    ]

    return create_embed(
        title="Application Withdrawn by Freelancer",
        description="\n".join(description_parts),
        color=BrandColor.PRIMARY,
        footer="Xentra • Job system",
    )
