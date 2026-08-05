"""
Embed builder for ``interview_room_review_agreement`` system messages.

Fire-and-forget notification sent when the interview agreement is ready for
review.  Constant text only — no receiver name, any ID, or room reference
in the body.  The Room / Job headers are added by the shared helpers in
``interview_room_system``.

Expected data keys
------------------
- discord_id (str)      , Snowflake of the receiver (used by handler).
- room_id (str, opt)    , The interview room ID (adds the Room header).
- job_title (str, opt)  , Title of the job (adds the Job header).
"""

import discord
from system_messages.interview_room_system import create_room_embed

TITLE = "Agreement Ready for Review"


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct the agreement-ready-for-review system message for the receiver.

    Returns ``(embed, title)``.  The embed contains the Room / Job headers
    for the DM; the title is returned separately so callers can persist
    ``"Title\n\nBody"`` (headerless body) for the transcript record.
    """
    body = (
        "> ***The interview agreement is ready for review.***\n"
        "\n"
        "> __Please review the agreement carefully. Once you are satisfied "
        "with the terms, you can sign it.__"
    )

    embed = create_room_embed(
        title=TITLE,
        body=body,
        data=data,
    )
    return embed, TITLE
