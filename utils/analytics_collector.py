"""
Analytics Collector — bot-side utility for sending structured analytics events to the backend.

Provides a fire-and-forget interface for bot cogs and events to log actions
without manually constructing packets or importing HTTP clients.

Usage:
    from utils.analytics_collector import AnalyticsCollector

    # Log a guild event
    AnalyticsCollector.log_guild_event(
        guild=guild,
        event_type="guild_bot_leave",
    )

    # Log a job event
    AnalyticsCollector.log_job_event(
        interaction=interaction,
        job_data={'job_id': job_id, 'job_title': title},
        event_type="job_posted",
        profile_id=profile_id,
        actor_role='client',
    )

    # Log an admin action
    AnalyticsCollector.log_admin_action(
        interaction=interaction,
        target_info={
            'target_role': 'client',
            'target_display_name': 'User Name',
            'target_display_id': 'CLI-XXXXX',
        },
        event_type="guild_allow_execution",
        admin_profile_id='ADM-XXXXX',
    )
"""
import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

from config import BACKEND_URL, WEBHOOK_SECRET
from packet_templates.factory import BotPacketFactory

logger = logging.getLogger('analytics_collector')


class AnalyticsCollector:
    """
    Fire-and-forget analytics event sender for the Discord bot.

    All methods are @classmethod. Send events to the backend analytics API
    without blocking the main cog execution flow. Errors are silently caught.
    """

    _API_ENDPOINT = None

    @classmethod
    def _get_api_endpoint(cls) -> str:
        """Lazy-evaluated API endpoint (avoids import-time evaluation)."""
        if cls._API_ENDPOINT is None:
            from config import BACKEND_URL
            cls._API_ENDPOINT = f"{BACKEND_URL}analytics/log/"
        return cls._API_ENDPOINT

    # ── Internal helpers ─────────────────────────────────────────────

    @classmethod
    def _send(cls, event: dict) -> None:
        """
        Schedule an async HTTP POST to the analytics backend.
        Fire-and-forget: runs via asyncio.ensure_future, never blocks.
        """
        packet = BotPacketFactory.create_packet(
            packet_type="analytics_log",
            data={'event': event},
            provider="bot",
        )
        headers = {'X-Webhook-Token': WEBHOOK_SECRET}

        # Schedule the async POST in the bot's event loop
        try:
            asyncio.ensure_future(cls._post(packet.to_dict(), headers))
        except RuntimeError:
            logger.warning("No running event loop — analytics event dropped")

    @classmethod
    async def _post(cls, payload: dict, headers: dict) -> None:
        """
        Async HTTP POST to the analytics endpoint.
        Silently swallows all exceptions — logging must NEVER break the main flow.
        """
        try:
            from utils.http import get_http_session
            session = get_http_session()
            async with session.post(
                cls._get_api_endpoint(),
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    logger.warning(
                        f"Analytics POST returned {resp.status}: "
                        f"{(await resp.text())[:200]}"
                        )
        except asyncio.TimeoutError:
            logger.debug("Analytics POST timed out (expected under load)")
        except aiohttp.ClientError as exc:
            logger.debug(f"Analytics POST failed (network error): {exc}")
        except Exception as exc:
            logger.debug(f"Analytics POST failed (unexpected): {exc}")

    # ── Public API ───────────────────────────────────────────────────

    @classmethod
    def log_guild_event(
        cls,
        guild,
        event_type: str,
        actor: Optional[dict] = None,
        **context,
    ) -> None:
        """
        Log a guild-related event.

        Args:
            guild: discord.Guild object (or object with .id and .name).
            event_type: One of EventType values (e.g. "guild_bot_join").
            actor: Optional dict with discord_id, display_name, profile_id, role.
            **context: Additional context fields (e.g. profiles_str).
        """
        event = {
            'event_type': event_type,
            'target_type': 'guild',
            'target_id': str(guild.id),
            'actor': actor,
            'context': {
                'guild_name': guild.name,
                **context,
            },
            'metadata': {},
        }
        cls._send(event)

    @classmethod
    def log_job_event(
        cls,
        interaction,
        job_data: dict,
        event_type: str,
        **context,
    ) -> None:
        """
        Log a job-related event (posted, applied, accepted, rejected).

        Args:
            interaction: discord.Interaction (for guild and user context).
            job_data: Dict with job_id, job_title, etc.
            event_type: One of "job_posted", "job_application", etc.
            **context: May include profile_id, actor_role, display_name, display_id, role_display_name.
        """
        profile_id = context.pop('profile_id', None)
        actor_role = context.pop('actor_role', None)
        role_display_name = context.pop('role_display_name', None)

        event = {
            'event_type': event_type,
            'target_type': 'guild',
            'target_id': str(interaction.guild_id) if interaction and interaction.guild_id else '',
            'actor': {
                'discord_id': str(interaction.user.id),
                'display_name': interaction.user.name,
                'profile_id': profile_id or '',
                'role': actor_role or '',
                'role_display_name': role_display_name or '',
            } if interaction else None,
            'context': {
                'guild_name': interaction.guild.name if interaction and interaction.guild else '',
                **job_data,
                **context,
            },
            'metadata': {},
        }
        cls._send(event)

    @classmethod
    def log_custom_event(cls, event: dict) -> None:
        """
        Log a fully-structured custom event via the public API.

        Accepts a pre-built event dictionary (including 'event_type',
        'target_type', 'target_id', 'actor', 'context', 'metadata').

        This is the public alternative to calling ``cls._send(event)``
        directly for events that don't fit the existing log_* helpers.
        """
        cls._send(event)

    @classmethod
    def log_admin_action(
        cls,
        interaction,
        target_info: dict,
        event_type: str,
        admin_profile_id: str,
        **context,
    ) -> None:
        """
        Log an admin action (block/allow execution).

        Args:
            interaction: discord.Interaction.
            target_info: Dict with target_role, target_display_name, target_display_id.
            event_type: One of "guild_block_execution", "guild_allow_execution".
            admin_profile_id: The premium display ID of the admin.
            **context: Additional context fields (may include role_display_name).
        """
        role_display_name = context.pop('role_display_name', None)
        event = {
            'event_type': event_type,
            'target_type': 'guild',
            'target_id': str(interaction.guild_id),
            'actor': {
                'discord_id': str(interaction.user.id),
                'display_name': interaction.user.name,
                'profile_id': admin_profile_id,
                'role': 'server_admin',
                'role_display_name': role_display_name or 'Server Admin',
            },
            'context': {
                'guild_name': interaction.guild.name,
                **target_info,
                **context,
            },
            'metadata': {},
        }
        cls._send(event)
