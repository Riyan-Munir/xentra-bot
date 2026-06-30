"""
Central handler for bot system messages (non-command notifications).

Mirrors the pattern established by ``command_handler.py`` / ``commands.json``:

  * ``system_messages.json`` — metadata registry (context, fields, description).
  * ``system_messages/<name>.py`` — individual embed builders exporting
    ``build_embed(data) -> discord.Embed``.
  * ``system_message_handler.handle()`` — the single entry point called by
    the webhook server.

Usage
-----
    from utils.system_message_handler import handle_system_message

    await handle_system_message("security_bypass_attempt", payload, bot)
"""

import logging
import json
import os
import importlib
from typing import Callable, Optional

import discord

logger = logging.getLogger("bot.system_msg")

# ---------------------------------------------------------------------------
# JSON metadata cache (mirrors command_handler.load_commands_data)
# ---------------------------------------------------------------------------

_SYSTEM_MESSAGES_DATA = None


def _load_system_messages_data() -> list:
    global _SYSTEM_MESSAGES_DATA
    if _SYSTEM_MESSAGES_DATA is None:
        path = os.path.join(
            os.path.dirname(__file__), "..", "data", "system_messages.json"
        )
        with open(path, "r") as f:
            _SYSTEM_MESSAGES_DATA = json.load(f)
    return _SYSTEM_MESSAGES_DATA


# ---------------------------------------------------------------------------
# Handler module cache (build_embed functions)
# ---------------------------------------------------------------------------

_HANDLER_CACHE: dict = {}


def _load_handler(message_type: str) -> Optional[Callable]:
    """Lazy-load and cache ``build_embed`` from ``system_messages/<type>.py``."""
    if message_type in _HANDLER_CACHE:
        return _HANDLER_CACHE[message_type]

    try:
        mod = importlib.import_module(f"system_messages.{message_type}")
        builder = getattr(mod, "build_embed", None)
        if builder is None:
            logger.error(
                "system_messages.%s has no build_embed() function", message_type
            )
            return None
        _HANDLER_CACHE[message_type] = builder
        return builder
    except ModuleNotFoundError:
        logger.error("No handler module for system message '%s'", message_type)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def handle_system_message(
    message_type: str,
    data: dict,
    bot: discord.Client,
    files: list[discord.File] | None = None,
) -> bool:
    """
    Route a system message to its embed builder and send the DM.

    Parameters
    ----------
    message_type : str
        Machine-readable name (e.g. ``"security_bypass_attempt"``).
    data : dict
        Payload fields.  Required keys depend on the message type (see
        ``system_messages.json``).
    bot : discord.Client
        The running bot instance, used to fetch users and send DMs.

    Returns
    -------
    bool
        ``True`` if the DM was sent successfully.
    """
    # 1. Validate against metadata
    metadata = _load_system_messages_data()
    type_meta = next((m for m in metadata if m["name"] == message_type), None)
    if type_meta is None:
        logger.warning("Unknown system message type '%s' — skipping.", message_type)
        return False

    # 2. Resolve the target user
    requires_user = type_meta.get("requires_user", False)
    user: discord.User | None = None
    if requires_user:
        discord_id = data.get("discord_id")
        if not discord_id:
            logger.warning(
                "%s requires discord_id but none was provided", message_type
            )
            return False

        try:
            user_id = int(discord_id)
        except (ValueError, TypeError):
            logger.warning("Invalid discord_id '%s'", discord_id)
            return False

        user = bot.get_user(user_id)
        if not user:
            try:
                user = await bot.fetch_user(user_id)
            except discord.NotFound:
                logger.warning("Discord user %s not found", user_id)
                return False
            except Exception:
                logger.exception("Failed to fetch user %s", user_id)
                return False

    # 3. Build the embed
    builder = _load_handler(message_type)
    if builder is None:
        return False

    try:
        embed = builder(data)
    except Exception:
        logger.exception("build_embed(%s) raised an exception", message_type)
        return False

    # 4. Validate mandatory fields
    if type_meta.get("fields"):
        for field in type_meta["fields"]:
            if field.get("mandatory") and field["name"] not in data:
                logger.warning(
                    "Mandatory field '%s' missing from %s payload",
                    field["name"],
                    message_type,
                )
                return False

    # 5. Send the DM
    if user is None:
        logger.warning("No user resolved for %s — cannot send DM", message_type)
        return False

    try:
        kwargs = {"embed": embed}
        if files:
            kwargs["files"] = files
        await user.send(**kwargs)
        logger.info("Sent %s DM to user %s", message_type, user.id)
        return True
    except discord.Forbidden:
        logger.warning("Cannot DM user %s — DMs disabled or blocked", user.id)
        return False
    except Exception:
        logger.exception("Failed to send %s DM to user %s", message_type, user.id)
        return False
