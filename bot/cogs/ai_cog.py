"""Botdi AI assistant for DMs and guild mentions.

Uses owner's API keys (Gemini, Groq, OpenRouter, Cerebras) — never user keys.
Replies in plain text (no embeds — embeds can truncate content).
Keeps responses short and conversational.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp
import discord
from discord.ext import commands
from google import genai

from config import (
    BOT_COLOR,
    COLOR_ERR,
    COLOR_WARN,
    CEREBRAS_API_KEY,
    CEREBRAS_MODEL,
    CEREBRAS_URL,
    DM_DAILY_LIMIT,
    GEMINI_API_KEY,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
)
from data_store import add_memory, check_dm_quota, clear_memory, get_memory, save_memory, use_dm_quota
from utils import check_pii_tos, check_profanity_at_bot, clean_ai_output, log_action

log = logging.getLogger(__name__)
_gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
_owners: set[int] = set()

_SYSTEM = """\
You are Botdi, a sharp, friendly Discord AI assistant. You're real, not robotic.

Personality:
- Be warm, casual, and genuinely helpful. Talk like a friend, not a corporate bot.
- Use natural language with light humor. Don't be try-hard.
- Be concise. Keep replies under 350 characters unless the user explicitly asks for detail.
- Don't use markdown headers, code blocks, or formatting for normal chat — just talk.
- If someone asks a quick question, give a quick answer.
- Have opinions when it matters. Don't be a yes-man.
- Remember context from the conversation — reference things the user said earlier.

Safety:
- Never provide instructions for harm, illegal activity, self-harm, violence, hate, scams, or explicit content.
- Never reveal personal information. No medical/legal/financial advice.
- Do not claim to be human or another AI.
"""

_BLOCKED = (
    "how to hack", "how to ddos", "how to dox", "how to make a bomb",
    "how to kill", "suicide method", "ignore your rules", "jailbreak",
)


async def _compat(url: str, key: str, model: str, messages: list[dict[str, str]]) -> str | None:
    if not key:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "max_tokens": 400, "temperature": 0.7},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status != 200:
                    return None
                data: dict[str, Any] = await response.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return clean_ai_output(text.strip(), max_len=600) if text else None
    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError, TypeError) as exc:
        log.warning("AI provider failed: %s", exc)
        return None


async def _generate(history: list[dict[str, str]], query: str) -> str | None:
    # Try Gemini first (if owner key configured)
    if _gemini is not None:
        context = "\n".join(f"{item['role']}: {item['content']}" for item in history[-12:])
        prompt = f"{_SYSTEM}\n\nConversation:\n{context}\n\nUser: {query}"
        for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
            try:
                response = await asyncio.wait_for(
                    _gemini.aio.models.generate_content(model=model, contents=prompt),
                    timeout=12,
                )
                if response.text:
                    return clean_ai_output(response.text.strip(), max_len=600)
            except Exception as exc:
                log.warning("Gemini provider failed: %s", exc)

    # Fallback chain: Groq → OpenRouter → Cerebras
    messages = [{"role": "system", "content": _SYSTEM}, *history[-12:], {"role": "user", "content": query}]
    for url, key, model in (
        (GROQ_URL, GROQ_API_KEY, GROQ_MODEL),
        (OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL),
        (CEREBRAS_URL, CEREBRAS_API_KEY, CEREBRAS_MODEL),
    ):
        result = await _compat(url, key, model, messages)
        if result:
            return result
    return None


class AICog(commands.Cog, name="AI"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        try:
            info = await self.bot.application_info()
            _owners.add(info.owner.id)
        except discord.HTTPException:
            log.warning("Could not load application owner")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        is_dm = isinstance(message.channel, discord.DMChannel)
        mentioned = self.bot.user is not None and self.bot.user in message.mentions
        if not is_dm and not mentioned and "botdi" not in message.content.lower():
            return
        user = message.author
        owner = user.id in _owners
        query = message.clean_content
        if self.bot.user:
            query = query.replace(f"@{self.bot.user.display_name}", "")
        query = re.sub(r"(?i)^botdi[,:\s]+", "", query).strip()
        if not query:
            await message.reply("Hey! What's up?", delete_after=8)
            return
        if is_dm and not owner:
            allowed, _ = check_dm_quota(user.id)
            if not allowed:
                await message.reply(f"You've hit today's {DM_DAILY_LIMIT}-message DM limit. Resets at midnight UTC.")
                return
        if not owner and (any(pattern in query.lower() for pattern in _BLOCKED) or check_profanity_at_bot(query)):
            await message.reply("I can't help with that. Keep it safe and respectful.", delete_after=12)
            await log_action(self.bot, "Botdi request blocked", f"**User:** {user.mention}\n**Query:** {query[:300]}", color=COLOR_WARN)
            return
        if not owner:
            violated, reason = check_pii_tos(query)
            if violated:
                await message.reply(f"Blocked: {reason}", delete_after=12)
                return
        if is_dm and query.lower() in {"forget me", "clear memory", "reset memory", "clear history"}:
            await clear_memory(user.id)
            await message.reply("Done — cleared our chat history.")
            return
        remaining = None if owner or not is_dm else use_dm_quota(user.id)
        async with message.channel.typing():
            reply = await _generate(get_memory(user.id), query)
        if not reply:
            await message.reply("AI services are busy right now — try again in a sec.", delete_after=15)
            return
        add_memory(user.id, "user", query)
        add_memory(user.id, "assistant", reply)
        await save_memory()
        # ── Plain text reply (no embeds — embeds truncate words) ──────────────
        if remaining is not None and remaining <= 3:
            reply += f"\n\n*({remaining}/{DM_DAILY_LIMIT} DM messages left today)*"
        await message.reply(reply)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
