# CI/registry update-check test commit
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("arrbridge")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.environ.get("GUILD_ID")

intents = discord.Intents.default()


class ArrBridgeBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.load_extension("cogs.addshow")
        await self.load_extension("cogs.addmovie")
        await self.load_extension("cogs.cleanup")
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = ArrBridgeBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, log_handler=None)
