"""
Nexus — main entrypoint.
Loads all cogs, syncs slash commands, sets presence.
"""
from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

from config import DISCORD_TOKEN, BOT_PREFIX
from log_handler import setup_logging

setup_logging()
log = logging.getLogger("nexus")


INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True
INTENTS.guilds = True


class Nexus(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=BOT_PREFIX, intents=INTENTS, help_command=None)

    async def setup_hook(self) -> None:
        for ext in (
            "cogs.ai_cog",
            "cogs.general",
            "cogs.fun",
            "cogs.moderation",
            "cogs.admin",
            "cogs.support",
            "cogs.subagent",
            "cogs.systems",
        ):
            try:
                await self.load_extension(ext)
                log.info("Loaded %s", ext)
            except Exception as e:
                log.exception("Failed to load %s: %s", ext, e)

        try:
            synced = await self.tree.sync()
            log.info("Synced %d slash commands", len(synced))
        except Exception as e:
            log.exception("Slash sync failed: %s", e)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Nexus | /help",
            )
        )


async def main() -> None:
    bot = Nexus()
    try:
        await bot.start(DISCORD_TOKEN)
    except KeyboardInterrupt:
        await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
