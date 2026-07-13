"""
AI Cog — Responds to bot @mentions via Gemini.
Enforces 60 s per-user cooldown and filters all input for PII / TOS.
A violation triggers a strike via the moderation cog.
"""

from __future__ import annotations

import time
import discord
from discord.ext import commands

from google import genai
from google.genai import types as genai_types

from config import GEMINI_API_KEY, GEMINI_MODEL, AI_COOLDOWN_SECONDS, BOT_COLOR
from utils import check_pii_tos, log_action

_gemini = genai.Client(api_key=GEMINI_API_KEY)

# System prompt fed to Gemini on every request
_SYSTEM_PROMPT = (
    "You are a helpful, friendly, and concise Discord bot assistant. "
    "Never reveal personal information. Keep responses under 1 800 characters. "
    "Refuse requests that violate Discord's Terms of Service."
)


class AICog(commands.Cog, name="AI"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # user_id -> last response UNIX timestamp
        self._cooldowns: dict[int, float] = {}

    # ── Listener ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        # Only react to direct @mentions of this bot
        if self.bot.user not in message.mentions:
            return

        user = message.author
        now = time.monotonic()

        # ── Cooldown check ────────────────────────────────────────────────────
        last = self._cooldowns.get(user.id, 0.0)
        remaining = AI_COOLDOWN_SECONDS - (now - last)
        if remaining > 0:
            await message.reply(
                f"⏳ Please wait **{remaining:.0f}s** before pinging me again.",
                delete_after=10,
            )
            return

        # Strip the bot mention from the query
        query = message.clean_content
        for m in message.mentions:
            query = query.replace(f"@{m.display_name}", "").strip()
        query = query.strip()

        if not query:
            await message.reply("Hey! Ask me something.", delete_after=10)
            return

        # ── PII / TOS filter ──────────────────────────────────────────────────
        violated, reason = check_pii_tos(query)
        if violated:
            await message.reply(
                f"🚫 Your message was blocked: **{reason}**. A strike has been issued.",
                delete_after=15,
            )
            await log_action(
                self.bot,
                "🚫 AI Filter Violation",
                f"**User:** {user.mention} (`{user.id}`)\n"
                f"**Reason:** {reason}\n"
                f"**Content:** ||{discord.utils.escape_markdown(query[:300])}||",
            )
            # Delegate strike issuance to the moderation cog
            mod_cog = self.bot.cogs.get("Moderation")
            if mod_cog:
                await mod_cog.apply_strike(
                    guild=message.guild,
                    target=user,
                    reason=f"AI filter violation: {reason}",
                    moderator=self.bot.user,
                )
            return

        # ── Gemini response ───────────────────────────────────────────────────
        self._cooldowns[user.id] = now

        async with message.channel.typing():
            try:
                response = await _gemini.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=f"{_SYSTEM_PROMPT}\n\nUser: {query}",
                )
                reply_text = response.text.strip()[:1800]
            except Exception as exc:
                await message.reply(f"⚠️ Gemini error: `{exc}`", delete_after=20)
                return

        embed = discord.Embed(description=reply_text, color=BOT_COLOR)
        embed.set_footer(text=f"Requested by {user.display_name}")
        await message.reply(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
