from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

log = logging.getLogger("arrbridge")

CLEANUP_CHANNEL_ID = int(os.environ["CLEANUP_CHANNEL_ID"])
CLEANUP_MAX_AGE_HOURS = float(os.environ.get("CLEANUP_MAX_AGE_HOURS", "6"))
CLEANUP_INTERVAL_MINUTES = 30

# purge() walks the channel history and deletes what it finds. Without
# read_message_history Discord hands back an empty history instead of an error,
# so the sweep quietly deletes nothing - check up front rather than no-op.
REQUIRED_PERMISSIONS = ("view_channel", "read_message_history", "manage_messages")


class Cleanup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_loop.start()

    def cog_unload(self):
        self.cleanup_loop.cancel()

    async def _resolve_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(CLEANUP_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(CLEANUP_CHANNEL_ID)
            except discord.HTTPException:
                log.warning("Cleanup channel %s is not reachable by the bot", CLEANUP_CHANNEL_ID)
                return None

        if not isinstance(channel, discord.TextChannel):
            log.warning("Cleanup channel %s is not a text channel (%s)", CLEANUP_CHANNEL_ID, type(channel).__name__)
            return None
        return channel

    @tasks.loop(minutes=CLEANUP_INTERVAL_MINUTES)
    async def cleanup_loop(self):
        channel = await self._resolve_channel()
        if channel is None:
            return

        perms = channel.permissions_for(channel.guild.me)
        missing = [name for name in REQUIRED_PERMISSIONS if not getattr(perms, name)]
        if missing:
            log.warning(
                "Cleanup skipped for #%s - bot is missing: %s. Grant these in the channel's "
                "permission settings, otherwise the sweep silently deletes nothing.",
                channel.name,
                ", ".join(missing),
            )
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=CLEANUP_MAX_AGE_HOURS)
        try:
            deleted = await channel.purge(limit=1000, before=cutoff)
        except discord.Forbidden:
            log.warning(
                "Missing permission to delete messages in #%s - bot needs 'Manage Messages' there",
                channel.name,
            )
            return
        except discord.HTTPException:
            log.exception("Failed to purge #%s", channel.name)
            return

        log.info(
            "Cleanup swept #%s - deleted %d message(s) older than %gh",
            channel.name,
            len(deleted),
            CLEANUP_MAX_AGE_HOURS,
        )

    @cleanup_loop.before_loop
    async def before_cleanup_loop(self):
        await self.bot.wait_until_ready()

    @cleanup_loop.error
    async def cleanup_loop_error(self, exc: BaseException):
        # An unhandled error stops a tasks.loop permanently, which is how this
        # sweep can go missing without anything in the log saying so.
        log.error("Cleanup loop crashed - restarting it", exc_info=exc)
        self.cleanup_loop.restart()


async def setup(bot: commands.Bot):
    await bot.add_cog(Cleanup(bot))
