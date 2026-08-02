"""
Embed builder for ``room_interview_message`` system messages.

This builder handles FOUR modes:

1. **Regular interview message**, sent when someone sends a message in an
   interview room (sender → receiver).
2. **Command notification**, sent when a user runs a room command
   (e.g. ``/interview budget`` or ``/interview milestone``), notifies the other party.
3. **Complaint notification** (``command_name`` = ``"interview_complain"``).
4. **Leave notification** (``command_name`` = ``"interview_leave"``).

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

**Command notification** (``command_name`` present, not ``interview_complain``)
- command_name (str)        , The command that was run (e.g. "interview_budget").
- executor_name (str)       , Display name of the person who ran the command.
- msg_data (str)            , The exact same text shown to the executor
                             (success or error message).  Callers must build
                             this string once and pass it to both the executor
                             embed and this field.

**Complaint notification** (``command_name`` = ``"interview_complain"``)
- All of the above, plus:
- complaint_id (str)        , The ID of the filed complaint.
- complaint_data (str)      , The complaint text content.
- target_msg_id (str, opt)  , If the complaint targets a specific message ID.
- target_complain_id (str, opt), If the complaint targets a specific complaint ID.

**Leave notification** (``command_name`` = ``"interview_leave"``)
- executor_name (str)       , Display name of the person who left.
- closure_id (str)          , The closure record ID.
- reason (str, opt)         , The reason for leaving.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> tuple[discord.Embed, str]:
    """Construct an interview-room notification for the receiver.

    Returns ``(embed, body_text)`` where ``body_text`` is the
    transcript-safe version without room headers.
    """
    room_id = data.get("room_id", "N/A")
    job_title = data.get("job_title", "a job")
    command_name = data.get("command_name")

    if command_name:
        if command_name == 'interview_complain':
            # ── Complaint notification mode ────────────────────────────
            executor_name = data.get("executor_name", "Someone")
            complaint_id = data.get("complaint_id", "N/A")
            complaint_data = data.get("complaint_data", "")
            target_msg_id = data.get("target_msg_id", "")
            target_complain_id = data.get("target_complain_id", "")

            body_parts = [
                f"> ***A complaint has been filed in your interview room.***",
                "",
                f"**Filed by:** `{executor_name}`",
                f"**Complaint ID:** `{complaint_id}`",
            ]

            if target_msg_id:
                body_parts.append(f"**Target Message ID:** `{target_msg_id}`")
            if target_complain_id:
                body_parts.append(f"**Target Complaint ID:** `{target_complain_id}`")

            body_parts.extend([
                "",
                "**Complaint:**",
                f"> {complaint_data}" if complaint_data else "> _No details_",
            ])

            body = "\n".join(body_parts)
            title = "Complaint Filed"

        elif command_name == 'interview_leave':
            # ── Leave notification mode ────────────────────────────
            executor_name = data.get("executor_name", "Someone")
            closure_id = data.get("closure_id", "N/A")
            reason = data.get("reason", "")

            body_parts = [
                f"> ***A participant has left the interview room.***",
                "",
                f"**Left by:** `{executor_name}`",
                f"**Closure ID:** `{closure_id}`",
            ]

            if reason:
                body_parts.append(f"**Reason:** `{reason}`")

            body_parts.extend([
                "",
                "> __The room will be closed and a transcript will be delivered shortly.__",
            ])

            body = "\n".join(body_parts)
            title = "Participant Left"

        else:
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

        role_label = "Client" if sender_role == "client" else "Freelancer"

        body_parts = [
            f"> ***Message received in your interview room.***",
            "",
            f"**From:** `{role_label}` — `{sender_name}`",
            f"**Message ID:** `{msg_id}`",
            "",
            "**Message:**",
        ]

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
