"""
Embed builder for ``room_closure`` system messages.

Sent to both parties when an interview room is concluded,
either via agreement signature, a party leaving, or system action.

Expected data keys
------------------
- discord_id (str), Snowflake of the recipient (used by handler).
- room_id (str), The interview room that was closed.
- closure_type (str), ``"agreement"``, ``"leave"``, or ``"system"``.
- client_name (str, optional), Display name of the client.
- freelancer_name (str, optional), Display name of the freelancer.
- agreement_id (str, optional), Agreement ID (agreement closure only).
- leave_reason (str, optional), Reason provided for leaving (leave closure only).
- left_by (str, optional), ``"client"`` or ``"freelancer"`` (leave closure only).
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct a room-closure notification embed for a party."""
    room_id = data.get("room_id", "Unknown Room")
    closure_type = data.get("closure_type", "agreement")

    if closure_type == "leave":
        left_by = data.get("left_by", "A participant")
        leave_reason = data.get("leave_reason", "")

        title = "Interview Room Closed"
        description = (
            f"Your interview room **{room_id}** has been closed "
            f"because **{left_by}** left the interview."
        )
        if leave_reason:
            description += f"\n\n**Reason:** {leave_reason}"
        description += (
            f"\n\nThank you for using Xentra. "
            f"A transcript of your conversation has been attached."
        )
    elif closure_type == "system":
        title = "Interview Room Closed by System"
        description = (
            f"Your interview room **{room_id}** has been closed "
            f"by the system.\n\n"
            f"Another room for this job has reached an agreement. "
            f"Thank you for using Xentra. "
            f"A transcript of your conversation has been attached."
        )
    else:
        title = "Interview Room Concluded"
        description = (
            f"Your interview room **{room_id}** has been concluded."
            f"\n\nThank you for using Xentra to facilitate your agreement. "
            f"The signed Job Agreement has been delivered to both parties."
        )

    return create_embed(
        title=title,
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
