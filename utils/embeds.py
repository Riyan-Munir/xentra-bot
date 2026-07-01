import discord
from enum import IntEnum
from typing import Optional

class BrandColor(IntEnum):
    PRIMARY = 0x6366f1   # Indigo
    SUCCESS = 0x10b981   # Emerald
    ERROR = 0xef4444     # Red
    WARNING = 0xf59e0b   # Amber
    PREMIUM = 0xffd700   # Gold
    ACCENT = 0x8b5cf6    # Violet
    DARK = 0x030712      # Deep Black/Blue


def format_description(msg: str) -> str:
    """Return the message as-is without adding pipe prefixes."""
    if not msg:
        return msg
    return msg


def create_embed(
    title: str = None,
    description: str = None,
    color: BrandColor = BrandColor.PRIMARY,
    thumbnail: str = None,
    footer: str = "Xentra • Premium Dashboard Ecosystem",
    author_name: str = "Xentra",
    author_icon: str = None,
    image: str = None,
) -> discord.Embed:
    """Base factory for Xentra embeds with business mark.

    All specialized embed types (error, success, info, etc.) build on this
    pattern.  The author and footer together form the Xentra business mark.
    """
    formatted_desc = format_description(description) if description else None
    embed = discord.Embed(
        title=title,
        description=formatted_desc,
        color=color.value if isinstance(color, BrandColor) else color,
    )

    # Business mark: Author
    embed.set_author(name=author_name, icon_url=author_icon)

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    if image:
        embed.set_image(url=image)

    if footer:
        embed.set_footer(text=footer)

    return embed


def error_embed(message: str) -> discord.Embed:
    """Standardized error response.

    The message is shown as the embed description.  No title is set —
    the red colour and ``Xentra • Error`` footer are sufficient
    to communicate the severity.
    """
    embed = discord.Embed(
        description=message,
        color=BrandColor.ERROR.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Error")
    return embed


def success_embed(message: str) -> discord.Embed:
    """Standardized success response.

    The message is shown as the embed description.  No title is set —
    the green colour and ``Xentra • Success`` footer communicate
    the positive outcome.
    """
    embed = discord.Embed(
        description=message,
        color=BrandColor.SUCCESS.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Success")
    return embed


def info_embed(message: str) -> discord.Embed:
    """Standardized information response.

    The message is shown as the embed description.  No title is set —
    the indigo colour and ``Xentra • Information`` footer communicate
    the informational nature.
    """
    embed = discord.Embed(
        description=message,
        color=BrandColor.PRIMARY.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Information")
    return embed


def warning_embed(message: str, title: str = "Warning") -> discord.Embed:
    """Standardized warning response with bold heading."""
    embed = discord.Embed(
        title=title,
        description=message,
        color=BrandColor.WARNING.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Warning")
    return embed


def loading_embed(
    description: str = "Processing your request\u2026",
    emoji: str = "\u23f3",
) -> discord.Embed:
    """Standardized loading-state embed for in-progress operations.

    This replaces all ``content="..."`` loading strings across every command.
    The emoji + description together form the bold heading so the user knows
    what stage the command is in.
    """
    embed = discord.Embed(
        title=f"{emoji} {description}",
        color=BrandColor.PRIMARY.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Processing")
    return embed


def throttled_embed(wait_seconds: float, title: str = "Too Many Requests") -> discord.Embed:
    """Standardized rate-limit notice with bold heading."""
    embed = discord.Embed(
        title=title,
        description=(
            f"You are being rate limited. "
            f"Please wait **{wait_seconds:.0f} seconds** before trying again."
        ),
        color=BrandColor.WARNING.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Rate Limited")
    return embed


def premium_embed(message: str, title: str = "Premium Feature") -> discord.Embed:
    """Gold-themed premium notification with bold heading."""
    embed = discord.Embed(
        title=title,
        description=message,
        color=BrandColor.PREMIUM.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Premium Feature")
    return embed


def dm_blocked_embed(attempted_action: str, receiver_name: str) -> discord.Embed:
    """Standardized embed when a DM cannot be delivered — receiver has DMs disabled or blocked the bot.

    Display this to the command executor (or the other party who *can* receive DMs)
    so they are informed and can ask the blocked user to enable DMs.

    Parameters
    ----------
    attempted_action : str
        Human-readable description of what failed to deliver
        (e.g. ``"your interview message"``, ``"the job details"``, ``"the room rules"``).
    receiver_name : str
        Display name of the user who could not be reached.
    """
    embed = discord.Embed(
        description=(
            f"Xentra could not deliver {attempted_action} to **{receiver_name}**.\n\n"
            f"They may have DMs disabled or have blocked the bot. "
            f"Please ask **{receiver_name}** to enable DMs from Xentra "
            f"and use command `\\interview_delivery`.\n"
            f"\n"
            f"*Attachments will not be sent automatically* "
        ),
        color=BrandColor.ERROR.value,
    )
    embed.set_author(name="Xentra")
    embed.set_footer(text="Xentra • Delivery Failed")
    return embed
