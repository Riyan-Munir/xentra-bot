"""
Embed builder for ``interview_room_closure`` system messages.

Sent to both parties when an interview room is closed — either via
agreement signature, a participant leaving, or system action.

This builder reads **everything from the closure record** returned by
``GET /rooms/bot/closure-details/?closure_id=...`` (no dynamic room
context is injected into the body).

Expected data keys (from the closure-details endpoint)
------------------------------------------------------
- discord_id (str)          , Snowflake of the recipient (used by handler).
- closure_id (str)          , The closure record ID.
- closure_type (str)        , ``"agreement"``, ``"leave"``, or ``"system"``.
- closed_by (str)           , ``"client"``, ``"freelancer"``, or ``"system"``.
- reason (str)              , Leave reason (leave closures only; empty otherwise).
- room_id (str)             , The interview room that was closed.
- job_title (str)           , Title of the job.
- client_name (str)         , Display name of the client.
- freelancer_name (str)     , Display name of the freelancer.
"""

import discord
from system_messages.interview_room_system import create_room_embed


def build_embed(data: dict) -> tuple[discord.Embed, str, str]:
    """Construct a room-closure notification embed for a party.

    Returns ``(embed, body_text, title)`` where ``body_text`` is the
    transcript-safe version without room headers and ``title`` is the
    message title used when persisting (``"Title\n\nBody"``).

    Three closure types:
    - ``agreement``: Both parties signed, room concluded.
    - ``leave``: A participant left the room.
    - ``system``: Room auto-closed because another room reached agreement.
    """
    closure_type = data.get("closure_type", "agreement")

    if closure_type == "leave":
        left_by = data.get("closed_by", "A participant")
        reason = data.get("reason", "")

        body_parts = [
            f"> ***A participant has left the interview room.***",
            "",
            f"**Left by:** `{left_by}`",
        ]

        if reason:
            body_parts.append(f"**Reason:** `{reason}`")

        body_parts.append(
            "> __This room has been permanently closed. A transcript will follow shortly.__"
        )

        body = "\n".join(body_parts)
        title = "Room Closed: Participant Left"

    elif closure_type == "system":
        body = (
            f"> ***This room was closed automatically by the system.***\n"
            f"\n"
            f"**Status:** `Agreement reached in another room`\n"
            f"\n"
            f"> __A transcript of this room will be delivered shortly.__"
        )
        title = "Room Closed"

    else:
        # agreement
        body = (
            f"> ***Agreement has been reached between both parties.***\n"
            f"\n"
            f"**Status:** `Agreement Signed`\n"
            f"\n"
            f"> __The signed Job Agreement has been delivered. A transcript will follow shortly.__"
        )
        title = "Room Closed: Agreement Signed"

    embed = create_room_embed(
        title=title,
        body=body,
        data=data,
    )
    return embed, body, title
