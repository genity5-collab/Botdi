"""
Discord Bot — Entry point
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from config import DISCORD_TOKEN, BOT_PREFIX
from cogs.support import SupportView

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True


class Bot(commands.Bot):
    async def setup_hook(self) -> None:
        # Register persistent views BEFORE on_ready so buttons survive restarts
        self.add_view(SupportView())

        # Load cogs
        for extension in (
            "cogs.ai_cog",
            "cogs.moderation",
            "cogs.support",
            "cogs.admin",
        ):
            await self.load_extension(extension)
            logging.info("Loaded extension: %s", extension)

    async def on_ready(self) -> None:
        logging.info("Logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the server | !help",
            )
        )


async def main() -> None:
    bot = Bot(command_prefix=BOT_PREFIX, intents=intents)
    async with bot:
        await bot.start(DISCORD_TOKEN)


asyncio.run(main())
