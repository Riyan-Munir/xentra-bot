"""
Embed builder for ``room_interview_message`` system messages.

This builder handles TWO modes:

1. **Regular interview message** — sent when someone sends a message in an
   interview room (sender → receiver).
2. **Command notification** — sent when a user runs a room command
   (e.g. ``/interview budget`` or ``/interview milestone``) — notifies the other party.

Expected data keys
------------------
**Both modes**
- discord_id (str)           — Snowflake of the receiver (used by handler).
- room_id (str)              — The interview room ID.
- job_title (str)            — Title of the job linked to this room.

**Regular message** (``command_name`` absent)
- sender_role (str)          — "client" or "freelancer".
- sender_name (str)          — Profile display name of the sender.
- msg_id (str)               — Saved message ID.
- msg_text (str)             — The message content.
- attachments (str, opt)     — Comma-separated list of filenames.

**Command notification** (``command_name`` present, not ``interview_complain``)
- command_name (str)         — The command that was run (e.g. "interview_budget").
- executor_name (str)        — Display name of the person who ran the command.
- msg_data (str)             — Execution details text (e.g. "3 milestone(s) configured.").

**Complaint notification** (``command_name`` = ``"interview_complain"``)
- All of the above, plus:
- complaint_id (str)         — The ID of the filed complaint.
- complaint_data (str)       — The complaint text content.
- target_msg_id (str, opt)   — If the complaint targets a specific message ID.
- target_complain_id (str, opt) — If the complaint targets a specific complaint ID.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_embed(data: dict) -> discord.Embed:
    """Construct an interview-room notification for the receiver."""
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

            description_parts = [
                f"**Interview Room:** `{room_id}`",
                f"**Job Title:** {job_title}",
                f"**Executor:** **{executor_name}**",
                f"**Complaint ID:** `{complaint_id}`",
            ]

            if target_msg_id:
                description_parts.append(
                    f"**Target Message ID:** `{target_msg_id}`"
                )
            if target_complain_id:
                description_parts.append(
                    f"**Target Complaint ID:** `{target_complain_id}`"
                )

            description_parts.extend([
                "",
                "**Complain:**",
                complaint_data if complaint_data else "_No details_",
            ])

            return create_embed(
                title="New Interview Complaint",
                description="\n".join(description_parts),
                color=BrandColor.PRIMARY,
                footer="Xentra • Room system",
            )

        if command_name == 'interview_leave':
            # ── Leave notification mode — uses leave model data ────────────
            executor_name = data.get("executor_name", "Someone")
            leave_id = data.get("leave_id", "N/A")
            reason = data.get("reason", "")

            description_parts = [
                f"**Interview Room:** `{room_id}`",
                f"**Job Title:** {job_title}",
                f"**Executor:** **{executor_name}**",
                f"**Leave ID:** `{leave_id}`",
                "",
                "**Reason:**",
                reason if reason else "_No reason provided_",
            ]

            return create_embed(
                title="Room Leave Notification",
                description="\n".join(description_parts),
                color=BrandColor.PRIMARY,
                footer="Xentra • Room system",
            )

        # ── Command notification mode (generic) ──────────────────────────
        executor_name = data.get("executor_name", "Someone")
        msg_data = data.get("msg_data", "")

        description_parts = [
            f"**Interview Room:** `{room_id}`",
            f"**Job Title:** {job_title}",
            f"**Executor:** **{executor_name}**",
            f"**Command:** `{command_name}`",
            "",
            "**Execution details:**",
            msg_data if msg_data else "_No details_",
        ]

        return create_embed(
            title="New Interview Notification",
            description="\n".join(description_parts),
            color=BrandColor.PRIMARY,
            footer="Xentra • Room system",
        )

    # ── Regular interview message mode ────────────────────────────────
    sender_role = data.get("sender_role", "sender")
    sender_name = data.get("sender_name", "Someone")
    msg_id = data.get("msg_id", "N/A")
    msg_text = data.get("msg_text", "")
    attachments = data.get("attachments", "")

    # Capitalise the role label for display
    role_label = "Client" if sender_role == "client" else "Freelancer"

    description_parts = [
        f"**Interview Room:** `{room_id}`",
        f"**Job Title:** {job_title}",
        f"**From {role_label}:** **{sender_name}**",
        f"**Message Id:** `{msg_id}`",
        "",
        "**Message:**",
    ]

    # Truncate msg_text to ensure total description stays under Discord's 4096 limit.
    # Build the description *without* msg_text to calculate boilerplate overhead.
    boilerplate = "\n".join(description_parts) + "\n"
    if attachments:
        boilerplate += "\n" + f"**Attachments:** {attachments}"

    max_msg_len = 4096 - len(boilerplate) - 10  # 10-char safety margin
    display_text = msg_text if msg_text else "_Empty_"
    if len(display_text) > max_msg_len and max_msg_len > 50:
        display_text = display_text[: max_msg_len - 40] + (
            "\n\n_... (message truncated, view in interview room for full text)_"
        )

    description_parts.append(display_text)

    if attachments:
        description_parts.append("")
        description_parts.append(f"**Attachments:** {attachments}")

    return create_embed(
        title="New Interview Message",
        description="\n".join(description_parts),
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
