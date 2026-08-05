"""
Embed builder for ``interview_room_transcript`` system messages.

Fire-and-forget notification sent when the interview transcript has been
generated.  Constant text only — no receiver name, any ID, or room
reference in the body.  The Room / Job headers are added by the shared
helpers in ``interview_room_system``.

Expected data keys
------------------
- discord_id (str)      , Snowflake of the receiver (used by handler).
- room_id (str, opt)    , The interview room ID (adds the Room header).
- job_title (str, opt)  , Title of the job (adds the Job header).
"""

import discord
from system_messages.interview_room_system import create_room_embed

TITLE = "Interview Transcript"


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct the transcript-generated system message for the receiver.

    Returns ``(embed, title)``.  The embed contains the Room / Job headers
    for the DM; the title is returned separately so callers can persist
    ``"Title\n\nBody"`` (headerless body) for the transcript record.
    """
    body = (
        "> ***Your interview transcript has been generated.***\n"
        "\n"
        "> __The full transcript of your interview conversation is ready. "
        "You can download it from the interview room.__"
    )

    embed = create_room_embed(
        title=TITLE,
        body=body,
        data=data,
    )
    return embed, TITLE
