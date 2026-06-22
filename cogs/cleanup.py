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


class Cleanup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_loop.start()

    def cog_unload(self):
        self.cleanup_loop.cancel()

    @tasks.loop(minutes=CLEANUP_INTERVAL_MINUTES)
    async def cleanup_loop(self):
        channel = self.bot.get_channel(CLEANUP_CHANNEL_ID)
        if channel is None:
            log.warning("Cleanup channel %s not found", CLEANUP_CHANNEL_ID)
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

        if deleted:
            log.info("Cleaned up %d message(s) in #%s", len(deleted), channel.name)

    @cleanup_loop.before_loop
    async def before_cleanup_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Cleanup(bot))
