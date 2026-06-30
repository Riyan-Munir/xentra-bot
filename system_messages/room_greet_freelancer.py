"""
Embed builder for ``room_greet_freelancer`` system messages.

Sent to the freelancer when a client creates an interview room for them.

Expected data keys
------------------
- discord_id (str) — Snowflake of the freelancer (used by handler).
- client_name (str) — Name of the client who owns the job.
- job_title (str) — Title of the job.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct an invitation embed for the freelancer."""
    client_name = data.get("client_name", "A client")
    job_title = data.get("job_title", "a job")

    return create_embed(
        title="Interview Invitation",
        description=(
            f"You have been invited to an interview room for **{job_title}** "
            f"by **{client_name}**.\n\n"
            f"Please be responsive and professional. The client will conduct "
            f"the interview within the room."
        ),
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
