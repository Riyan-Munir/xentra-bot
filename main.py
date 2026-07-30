import discord
from discord.ext import commands
import os
import asyncio
import logging
import aiohttp
from discord.ext import tasks
from config import (
    DISCORD_TOKEN,
    BACKEND_URL,
    WEBHOOK_HOST,
    WEBHOOK_PORT,
    CLUSTER_ENABLED,
    CLUSTER_TOTAL_SHARDS,
    AUTO_SHARD,
    SHARD_COUNT,
)
from webhook_server import WebhookServer
from utils.http import init_http_session, get_http_session

# Setup logging, writes to Logs.txt in the bot directory (append mode with rotation)
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s',
    handlers=[
        logging.FileHandler('Logs.txt', mode='a', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('bot')


class Xentra(commands.AutoShardedBot if AUTO_SHARD and not CLUSTER_ENABLED else commands.Bot):
    def __init__(self, *, shard_count=None, shard_ids=None, cluster_worker=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        kwargs = {
            'command_prefix': '!',
            'intents': intents,
            'help_command': None,
        }
        if shard_count is not None:
            # Cluster mode: explicit shard count from backend
            kwargs['shard_count'] = shard_count
            if shard_ids is not None:
                kwargs['shard_ids'] = shard_ids
        elif AUTO_SHARD:
            # Legacy manual-shard mode
            kwargs['shard_count'] = SHARD_COUNT

        super().__init__(**kwargs)
        self.webhook_server = WebhookServer(self)

        # ── Shared aiohttp session ────────────────────────────────────
        # Single persistent connection pool shared across all cogs.
        # Eliminates the ~48 separate TCP connections opened per command.
        self.http_session = aiohttp.ClientSession()
        init_http_session(self.http_session)

        # ── Cluster worker ────────────────────────────────────────────
        if cluster_worker is not None:
            # Worker was already created and started by main()
            self.cluster_worker = cluster_worker
            logger.info(
                "ClusterWorker attached, node_id=%s (pre-started)",
                self.cluster_worker.node_id,
            )
        elif CLUSTER_ENABLED:
            from cluster_worker import ClusterWorker
            self.cluster_worker = ClusterWorker()
            logger.info("ClusterWorker instantiated, node_id=%s", self.cluster_worker.node_id)
        else:
            self.cluster_worker = None

        # ── Intercept tree-level errors to handle cooldown gracefully ──
        self._patch_tree_error_handler()

        # ── Track last known guild count for throttled presence updates ──
        self._last_heartbeat_guild_count = 0

    def _patch_tree_error_handler(self):
        """
        Replace tree.on_error to intercept CommandOnCooldown before it reaches
        the default handler which logs at ERROR level.

        The default tree._call() → tree.on_error() path does NOT dispatch the
        bot-level 'on_app_command_error' event, so we patch the tree directly.
        CommandOnCooldown is logged at INFO (expected flow), all other errors
        fall through to the original handler which logs at ERROR if no
        command-level error handler exists.
        """
        original_on_error = self.tree.on_error

        async def custom_on_error(
            interaction: discord.Interaction,
            error: discord.app_commands.AppCommandError,
        ) -> None:
            if isinstance(error, discord.app_commands.CommandOnCooldown):
                from utils.embeds import error_embed
                logger.info(
                    "Command '%s' on cooldown for user %s (retry_after=%.1fs)",
                    interaction.command.qualified_name if interaction.command else '?',
                    interaction.user.id,
                    error.retry_after,
                )
                embed = error_embed(
                    f"Command on cooldown. Try again in **{error.retry_after:.1f}s**."
                )
                try:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                except discord.errors.InteractionResponded:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                except (discord.errors.NotFound, discord.errors.Forbidden):
                    # Interaction window expired, nothing we can do
                    pass
                return

            # Fall through to default handler (logs at ERROR if no command-local handler)
            await original_on_error(interaction, error)

        self.tree.on_error = custom_on_error

    async def setup_hook(self):
        """Automatically loads all cogs from cogs/ and events/."""
        # ── Load cogs ──────────────────────────────────────────────
        cogs_dir = 'cogs'
        loaded_cogs = set()
        for root, dirs, files in os.walk(cogs_dir):
            for filename in files:
                # Skip private/shared modules (e.g. _shared.py) and __init__.py
                if not filename.endswith('.py') or filename.startswith('_'):
                    continue
                rel_path = os.path.relpath(os.path.join(root, filename[:-3]), cogs_dir)
                module_path = rel_path.replace(os.sep, '.')
                if module_path in loaded_cogs:
                    continue
                try:
                    await self.load_extension(f'cogs.{module_path}')
                    loaded_cogs.add(module_path)
                except Exception as e:
                    logger.error(f"Failed to load extension cogs.{module_path}: {e}")

        # ── Load events ────────────────────────────────────────────
        events_dir = 'events'
        for root, dirs, files in os.walk(events_dir):
            for filename in files:
                if filename.endswith('.py') and not filename.startswith('__'):
                    rel_path = os.path.relpath(os.path.join(root, filename[:-3]), events_dir)
                    module_path = rel_path.replace(os.sep, '.')
                    try:
                        await self.load_extension(f'events.{module_path}')
                    except Exception as e:
                        logger.error(f"Failed to load extension events.{module_path}: {e}")

        # ── Sync commands ─────────────────────────────────────────
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands globally.")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        if self.shard_count and self.shard_count > 1:
            logger.info(
                "Bot is sharded, %d shard(s), shard_ids=%s",
                self.shard_count,
                self.shard_ids,
            )
        logger.info("------")

    @tasks.loop(seconds=300)  # 5 minutes, presence rarely changes
    async def heartbeat_task(self):
        """Pings the backend to verify connectivity and updates bot status.

        Throttled to 5-minute intervals. ``change_presence()`` is called
        **only** when the guild count actually changes, avoiding unnecessary
        Discord API calls that eat into global rate-limit budget.
        """
        health_url = f"{BACKEND_URL}health/"
        session = get_http_session()
        try:
            async with session.get(health_url, timeout=5) as resp:
                # Fully consume response to avoid unclosed connections
                await resp.read()
                if resp.status == 200:
                    # Determine the current guild count
                    current_guild_count = len(self.guilds)
                    # Was there a *real* change?  Also covers the very first run
                    # (when _last_heartbeat_guild_count = 0).
                    if current_guild_count != self._last_heartbeat_guild_count:
                        self._last_heartbeat_guild_count = current_guild_count
                        activity = discord.Game(name=f"/help | {current_guild_count} servers")
                        await self.change_presence(activity=activity, status=discord.Status.online)
                # else: backend returned non-200, silently skip presence update.
                # No need to call change_presence(idle) because the presence
                # change itself is a Discord API call.
        except Exception as e:
            logger.warning(f"Backend health check failed: {e}")
            # Don't call change_presence(dnd) on failure, that's another API
            # call we don't need to waste on a transient backend issue.

    @heartbeat_task.before_loop
    async def before_heartbeat(self):
        await self.wait_until_ready()

async def main():
    # ── Cluster bootstrap (before constructing bot) ────────────────
    shard_count = None
    shard_ids = None
    cluster_worker = None

    if CLUSTER_ENABLED:
        from cluster_worker import ClusterWorker
        cluster_worker = ClusterWorker()
        logger.info(
            "Cluster mode: created ClusterWorker, node_id=%s",
            cluster_worker.node_id,
        )

        # 1. Register with backend
        await cluster_worker.start()
        logger.info("Cluster mode: registered and heartbeat started.")

        # 2. Fetch shard config from backend
        shard_config = await cluster_worker.get_shard_config()
        if "error" in shard_config:
            logger.critical(
                "Cluster mode: failed to fetch shard config: %s",
                shard_config["error"],
            )
            # Fall back to CLUSTER_TOTAL_SHARDS env var or 1
            shard_count = CLUSTER_TOTAL_SHARDS or SHARD_COUNT
            shard_ids = None
            logger.warning(
                "Cluster mode: falling back to CLUSTER_TOTAL_SHARDS=%d",
                shard_count,
            )
        else:
            total = shard_config.get("total_shards", 1)
            assigned = shard_config.get("assigned_shard_ids", [])
            logger.info(
                "Cluster mode: backend reports total_shards=%d, "
                "assigned_shard_ids=%s",
                total,
                assigned,
            )

            if assigned:
                # Already have shards assigned from a previous claim
                shard_count = total
                shard_ids = assigned
                logger.info(
                    "Cluster mode: using pre-assigned shards %s of %d",
                    shard_ids,
                    shard_count,
                )
            else:
                # Need to claim shards first
                claim_result = await cluster_worker.claim_shards()
                if "error" in claim_result:
                    logger.critical(
                        "Cluster mode: failed to claim shards: %s",
                        claim_result["error"],
                    )
                    shard_count = total
                    shard_ids = None
                else:
                    shard_count = total
                    shard_ids = claim_result.get("shard_ids", [])
                    logger.info(
                        "Cluster mode: claimed shards %s of %d "
                        "(from_dead=%d, unassigned=%d)",
                        shard_ids,
                        shard_count,
                        claim_result.get("claimed_from_dead", 0),
                        claim_result.get("claimed_unassigned", 0),
                    )

    # ── Construct bot with resolved shard parameters ───────────────
    bot = Xentra(
        shard_count=shard_count,
        shard_ids=shard_ids,
        cluster_worker=cluster_worker,
    )

    async with bot:
        logger.info("Starting bot process...")
        bot.heartbeat_task.start()
        logger.info("Heartbeat task started.")

        # Start the webhook server for receiving backend push notifications
        try:
            await bot.webhook_server.start()
            logger.info("Webhook server started successfully.")
        except Exception as e:
            logger.warning(f"Webhook server failed to start (non-fatal): {e}")

        # Cluster worker was already started during bootstrap, so
        # we do NOT start it again here.  The ClusterWorker is started
        # before the bot so that registration + shard claim happens
        # before the Discord connection.

        try:
            await bot.start(DISCORD_TOKEN)
        finally:
            # ── Cleanup on shutdown ─────────────────────────────────
            # This block runs even when Ctrl+C cancels bot.start(), because
            # asyncio.run() will cancel the main() coroutine, and the finally
            # ensures cleanup always executes.
            await _cleanup(bot)


async def _cleanup(bot: Xentra) -> None:
    """Gracefully shut down all components to avoid unclosed sessions/connectors."""
    from utils.http import close_http_session

    logger.info("Shutting down bot components...")

    # 1. Stop the heartbeat task first so no new requests fire
    if bot.heartbeat_task.is_running():
        bot.heartbeat_task.cancel()

    # 2. Stop webhook server & cluster worker (releases TCP connectors)
    await bot.webhook_server.stop()
    if bot.cluster_worker:
        await bot.cluster_worker.stop()

    # 3. Give pending in-flight connections a real chance to drain
    await asyncio.sleep(0.5)

    # 4. Close the shared aiohttp session & clear module-level reference
    if not bot.http_session.closed:
        await bot.http_session.close()
    await close_http_session()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
