"""
Embed builder for ``room_closure`` system messages.

Sent to both parties when an interview room is concluded,
either via agreement signature, a party leaving, or system action.

Expected data keys
------------------
- discord_id (str), Snowflake of the recipient (used by handler).
- room_id (str), The interview room that was closed.
- job_title (str), Title of the job.
- closure_type (str), ``"agreement"``, ``"leave"``, or ``"system"``.
- client_name (str, optional), Display name of the client.
- freelancer_name (str, optional), Display name of the freelancer.
- agreement_id (str, optional), Agreement ID (agreement closure only).
- leave_reason (str, optional), Reason provided for leaving (leave closure only).
- left_by (str, optional), ``"client"`` or ``"freelancer"`` (leave closure only).
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct a room-closure notification embed for a party.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.

    Three closure types:
    - ``agreement``: Both parties signed, room concluded.
    - ``leave``: A party left the room.
    - ``system``: Room auto-closed because another room reached agreement.
    """
    room_id = data.get("room_id", "Unknown Room")
    job_title = data.get("job_title", "N/A")
    closure_type = data.get("closure_type", "agreement")

    if closure_type == "leave":
        left_by = data.get("left_by", "A participant")
        leave_reason = data.get("leave_reason", "")

        body_parts = [
            f"> ***A participant has left the interview room.***",
            "",
            f"**Left by:** `{left_by}`",
        ]

        if leave_reason:
            body_parts.append(f"**Reason:** `{leave_reason}`")

        body_parts.extend([
            "",
            "> __This room has been permanently closed. A transcript will follow shortly.__",
        ])

        body = "\n".join(body_parts)
        title = "Room Closed"

    elif closure_type == "system":
        body = (
            f"> ***This room was closed automatically by the system.***\n"
            f"\n"
            f"**Reason:** `Agreement reached in another room`\n"
            f"\n"
            f"> __A transcript of this room will be delivered shortly.__"
        )
        title = "Room Closed by System"

    else:
        # agreement
        body = (
            f"> ***Agreement has been reached between both parties.***\n"
            f"\n"
            f"**Status:** `Agreement Signed`\n"
            f"\n"
            f"> __The signed Job Agreement has been delivered. A transcript will follow shortly.__"
        )
        title = "Room Concluded"

    description = (
        f"> ***Room: `{room_id}`***\n"
        f"> ***Job: `{job_title}`***\n"
        f"\n"
        f"{body}"
    )

    embed = create_embed(
        title=title,
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
    return embed, body
