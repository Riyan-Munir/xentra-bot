"""
Embed builder for ``interview_room_message`` system messages.

This builder handles TWO modes:

1. **Regular interview message**, sent when someone sends a message in an
   interview room (sender → receiver).
2. **Command notification**, sent when a user runs a room command
   (e.g. ``/interview budget`` or ``/interview milestone``), notifies the other party.

Expected data keys
------------------
**Both modes**
- discord_id (str)          , Snowflake of the receiver (used by handler).
- room_id (str)             , The interview room ID.
- job_title (str)           , Title of the job linked to this room.

**Regular message** (``command_name`` absent)
- sender_role (str)         , "client" or "freelancer".
- sender_name (str)         , Profile display name of the sender.
- msg_id (str)              , Saved message ID.
- msg_text (str)            , The message content.
- attachments (str, opt)    , Comma-separated list of filenames.

**Command notification** (``command_name`` present)
- command_name (str)        , The command that was run (e.g. "interview_budget").
- executor_name (str)       , Display name of the person who ran the command.
- msg_data (str)            , The exact same text shown to the executor
                             (success or error message).  Callers must build
                             this string once and pass it to both the executor
                             embed and this field.
"""

import discord
from system_messages.interview_room_system import create_room_embed


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct an interview-room notification for the receiver.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    command_name = data.get("command_name")

    if command_name:
        # ── Command notification mode (generic) ──────────────────────────
        executor_name = data.get("executor_name", "Someone")
        msg_data = data.get("msg_data", "")

        body = (
            f"> ***A command was executed in your interview room.***\n"
            f"\n"
            f"**Executor:** `{executor_name}`\n"
            f"**Command:** `{command_name}`\n"
            f"\n"
            f"**Execution details:**\n"
            f"> {msg_data}" if msg_data else f"> _No details_"
        )
        title = "Command Executed"

    else:
        # ── Regular interview message mode ────────────────────────────────
        sender_role = data.get("sender_role", "sender")
        sender_name = data.get("sender_name", "Someone")
        msg_id = data.get("msg_id", "N/A")
        msg_text = data.get("msg_text", "")
        attachments = data.get("attachments", "")
        target_msg_id = data.get("target_msg_id", "")
        target_complain_id = data.get("target_complain_id", "")

        role_label = "Client" if sender_role == "client" else "Freelancer"

        body_parts = [
            f"> ***Message received in your interview room.***",
            "",
            f"**From:** `{role_label}` — `{sender_name}`",
            f"**Message ID:** `{msg_id}`",
        ]

        if target_msg_id:
            body_parts.append(f"**Reply to Message ID:** `{target_msg_id}`")
        if target_complain_id:
            body_parts.append(f"**Reply to Complaint ID:** `{target_complain_id}`")

        body_parts.extend([
            "",
            "**Message:**",
        ])

        # Truncate msg_text to ensure total description stays under Discord's 4096 limit.
        boilerplate = "\n".join(body_parts) + "\n"
        if attachments:
            boilerplate += f"\n**Attachments:** {attachments}"

        max_msg_len = 4096 - len(boilerplate) - 10  # 10-char safety margin
        display_text = msg_text if msg_text else "_Empty_"
        if len(display_text) > max_msg_len and max_msg_len > 50:
            display_text = display_text[: max_msg_len - 40] + (
                "\n\n_... (message truncated, view in interview room for full text)_"
            )

        body_parts.append(f"> {display_text}")

        if attachments:
            body_parts.append("")
            body_parts.append(f"**Attachments:** {attachments}")

        body = "\n".join(body_parts)
        title = "New Message"

    embed = create_room_embed(
        title=title,
        body=body,
        data=data,
    )
    return embed, body
