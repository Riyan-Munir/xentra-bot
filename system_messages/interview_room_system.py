"""
Shared helpers for interview-room system message builders.

Every interview-room system message module keeps its own ``build_embed()``
(so the handler routes and logs each message under its own name), but the
common parts are delegated to this shared utility:

1. ``build_headers``      — reads ``room_id`` / ``job_title`` from the data
   dict and builds the Room / Job header block.  The Room line is skipped
   when ``room_id`` is not obtainable (e.g. guide messages sent before the
   room session exists), matching the guide-message behaviour.
2. ``create_room_embed``  — embed factory with the standard room-system
   styling (``BrandColor.PRIMARY`` and the "Xentra • Room system" footer).

Each message definition is responsible for its own title and body; the
title is passed to ``create_room_embed`` from the message's own
``build_embed()``.
"""

import discord
from utils.embeds import create_embed, BrandColor


def build_headers(data: dict) -> str:
    """Extract ``room_id`` / ``job_title`` from ``data`` and build the header block.

    The Room header is only added when ``room_id`` is obtainable — guide
    messages sent before the room session exists have no room id yet, so
    they get a Job-only header (or no header at all).
    """
    room_id = data.get("room_id", "")
    job_title = data.get("job_title", "")

    header_lines = []
    if room_id:
        header_lines.append(f"> ***Room: `{room_id}`***")
    if job_title:
        header_lines.append(f"> ***Job: `{job_title}`***")
    if header_lines:
        return "\n".join(header_lines) + "\n\n"
    return ""


def create_room_embed(*, title: str, body: str, data: dict) -> discord.Embed:
    """Build a room-system embed from a headerless ``body``.

    The Room / Job headers are read from ``data`` (``room_id`` and
    ``job_title``) and prepended by :func:`build_headers`.  Omit
    ``room_id`` from ``data`` when it is not obtainable so that only the
    Job header (or none) is added.
    """
    description = build_headers(data) + body
    return create_embed(
        title=title,
        description=description,
        color=BrandColor.PRIMARY,
        footer="Xentra • Room system",
    )
