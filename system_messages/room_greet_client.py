"""
Embed builder for ``room_greet_client`` system messages.

Sent to the client when an interview room has been successfully created.

Expected data keys
------------------
- discord_id (str) — Snowflake of the client (used by handler).
- freelancer_name (str) — Name of the freelancer.
- job_title (str) — Title of the job.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct a confirmation embed for the client."""
    freelancer_name = data.get("freelancer_name", "Freelancer")
    job_title = data.get("job_title", "the job")

    return create_embed(
        title="Interview Room Created",
        description=(
            f"An interview room has been created for your application with "
            f"**{freelancer_name}** for the job **{job_title}**.\n\n"
            f"The interview process will take place within the room. "
            f"Stay responsive to ensure a smooth experience."
        ),
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
