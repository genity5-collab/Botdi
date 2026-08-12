"""
Discord Bot — Entry point

Starts both the Discord bot and the preview HTTP server.
The preview server serves /<project_id> for App Engineering previews.
Railway health check hits /health.
"""
from __future__ import annotations
import asyncio
import datetime
import json
import logging
import os
import time
from pathlib import Path

import discord
from discord.ext import commands, tasks
from aiohttp import web

from config import DISCORD_TOKEN, BOT_PREFIX
from cogs.support import SupportView
import log_handler as _log_handler
from preview_server import create_preview_app

DATA_DIR = Path(__file__).parent / "data"
STATUS_FILE = DATA_DIR / "status.json"
DATA_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log_handler.install()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True

PREVIEW_PORT = int(os.environ.get("PORT", 8080))


class Bot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=BOT_PREFIX, intents=intents, help_command=None)
        self._start_time = time.monotonic()

    async def setup_hook(self) -> None:
        self.add_view(SupportView())
        for ext in (
            "cogs.ai_cog",
            "cogs.moderation",
            "cogs.support",
            "cogs.admin",
            "cogs.general",
            "cogs.fun",
            "cogs.site_cog",
        ):
            try:
                await self.load_extension(ext)
                logging.info("Loaded: %s", ext)
            except Exception as exc:
                logging.error("Failed to load %s: %s", ext, exc, exc_info=True)
        self._write_status.start()

    async def on_ready(self) -> None:
        logging.info(
            "Online as %s (%s) — %d guilds",
            self.user, self.user.id, len(self.guilds),
        )
        for guild in self.guilds:
            try:
                synced = await self.tree.sync(guild=guild)
                logging.info("Guild-synced %d command(s) to '%s'.", len(synced), guild.name)
            except Exception as exc:
                logging.warning("Guild sync failed for '%s': %s", guild.name, exc)
        try:
            global_synced = await self.tree.sync()
            logging.info("Global-synced %d command(s).", len(global_synced))
        except Exception as exc:
            logging.warning("Global sync failed: %s", exc)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the server | /help",
            )
        )

    @tasks.loop(seconds=5)
    async def _write_status(self) -> None:
        try:
            STATUS_FILE.write_text(json.dumps({
                "online": True,
                "bot_name": str(self.user) if self.user else "unknown",
                "bot_id": str(self.user.id) if self.user else "",
                "guild_count": len(self.guilds),
                "uptime_seconds": time.monotonic() - self._start_time,
                "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2))
        except Exception as exc:
            logging.warning("Status write failed: %s", exc)

    @_write_status.before_loop
    async def _before_write(self) -> None:
        await self.wait_until_ready()


async def main() -> None:
    # Start preview HTTP server
    app = create_preview_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PREVIEW_PORT)
    await site.start()
    logging.info("Preview server listening on :%d", PREVIEW_PORT)

    # Start Discord bot
    bot = Bot()
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    finally:
        await runner.cleanup()
        try:
            STATUS_FILE.write_text(json.dumps({
                "online": False, "bot_name": "", "bot_id": "",
                "guild_count": 0, "uptime_seconds": 0,
                "started_at": "", "last_updated":
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }, indent=2))
        except Exception:
            pass


asyncio.run(main())
