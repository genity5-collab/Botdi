"""Vyrion AI assistant for DMs and guild mentions.

Uses owner's API keys — never user keys.
Provider chain (all free/cheap): Gemini Flash → Groq → OpenRouter free → Cerebras
Replies in plain text (no embeds — embeds can truncate content).
Keeps responses short and conversational.

Vision: When a user sends an image or video attachment, the AI switches to
google/gemma-4-26b-a4b-it:free on OpenRouter (supports text+image+video input).

IMPORTANT: When a user has an open support ticket, the AI does NOT respond to their DMs.
Messages go to staff via the support cog instead.
"""
from __future__ import annotations

import asyncio
import base64
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
    VISION_MODEL,
    VISION_URL,
)
from data_store import add_memory, check_dm_quota, clear_memory, get_memory, save_memory, use_dm_quota, get_user_open_ticket
from utils import check_pii_tos, check_profanity_at_bot, clean_ai_output, log_action

log = logging.getLogger(__name__)

_gemini = None
if GEMINI_API_KEY:
    try:
        _gemini = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        log.warning("Gemini client init failed (key may be expired): %s", exc)
        _gemini = None

_owners: set[int] = set()

_SYSTEM = """\
You are Vyrion, a sharp, friendly Discord AI assistant. You're real, not robotic.

Personality:
- Be warm, casual, and genuinely helpful. Talk like a friend, not a corporate bot.
- Use natural language with light humor. Don't be try-hard.
- Be concise. Keep replies under 350 characters unless the user explicitly asks for detail.
- Don't use markdown headers, code blocks, or formatting for normal chat — just talk.
- If someone asks a quick question, give a quick answer.
- Have opinions when it matters. Don't be a yes-man.
- Remember context from the conversation — reference things the user said earlier.
- Your name is Vyrion. If someone asks your name, tell them.

Safety:
- Never provide instructions for harm, illegal activity, self-harm, violence, hate, scams, or explicit content.
- Never reveal personal information. No medical/legal/financial advice.
- Do not claim to be human or another AI. You are Vyrion.
"""

_BLOCKED = (
    "how to hack", "how to ddos", "how to dox", "how to make a bomb",
    "how to kill", "suicide method", "ignore your rules", "jailbreak",
)

_FREE_OPENROUTER_MODELS = [
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2.5-7b-instruct:free",
]

# File extensions that trigger vision mode
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


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


async def _try_openrouter_free(messages: list[dict]) -> str | None:
    if not OPENROUTER_API_KEY:
        return None
    for model in _FREE_OPENROUTER_MODELS:
        result = await _compat(OPENROUTER_URL, OPENROUTER_API_KEY, model, messages)
        if result:
            return result
    return None


async def _generate(history: list[dict[str, str]], query: str) -> str | None:
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
                log.warning("Gemini provider failed (key may be expired): %s", exc)
                break

    messages = [{"role": "system", "content": _SYSTEM}, *history[-12:], {"role": "user", "content": query}]

    result = await _compat(GROQ_URL, GROQ_API_KEY, GROQ_MODEL, messages)
    if result:
        return result

    result = await _try_openrouter_free(messages)
    if result:
        return result

    result = await _compat(CEREBRAS_URL, CEREBRAS_API_KEY, CEREBRAS_MODEL, messages)
    if result:
        return result

    return None


# ── Vision: process image/video attachments with Gemma 4 ───────────────────────

async def _download_attachment(url: str, max_size: int = 20 * 1024 * 1024) -> bytes | None:
    """Download an attachment, max 20MB."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return None
                # Check content length
                cl = resp.headers.get("Content-Length", "")
                if cl and int(cl) > max_size:
                    return None
                data = await resp.read()
                if len(data) > max_size:
                    return None
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


def _get_mime_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_map = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
        "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
        "avi": "video/x-msvideo", "mkv": "video/x-matroska",
    }
    return mime_map.get(ext, "application/octet-stream")


async def _generate_vision(
    history: list[dict[str, str]],
    query: str,
    attachments: list[discord.Attachment],
) -> str | None:
    """Use google/gemma-4-26b-a4b-it:free on OpenRouter for image/video input."""
    if not OPENROUTER_API_KEY:
        return None

    # Build multimodal content
    content_parts: list[dict] = []

    # Add conversation context as text
    context = ""
    if history:
        context = "\n".join(f"{item['role']}: {item['content'][:200]}" for item in history[-6:])
        context = f"Previous conversation:\n{context}\n\n"

    # Add the user's text query
    text_prompt = f"{_SYSTEM}\n\n{context}User message: {query or 'What do you see in this image/video?'}"
    content_parts.append({"type": "text", "text": text_prompt})

    # Download and add each attachment
    for att in attachments[:4]:  # Max 4 attachments
        mime = _get_mime_type(att.filename)
        is_image = any(att.filename.lower().endswith(ext) for ext in _IMAGE_EXTS)
        is_video = any(att.filename.lower().endswith(ext) for ext in _VIDEO_EXTS)

        if not (is_image or is_video):
            continue

        # For images: download and send as base64 data URL
        if is_image:
            data = await _download_attachment(att.url)
            if data:
                b64 = base64.b64encode(data).decode("utf-8")
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"}
                })

        # For videos: pass the URL directly (OpenRouter/Gemma supports video URLs)
        elif is_video:
            # Discord attachment URLs are public and accessible
            content_parts.append({
                "type": "image_url",  # OpenRouter uses image_url type for video too in some cases
                "image_url": {"url": att.url}
            })
            # Also add a text note about the video
            content_parts.append({
                "type": "text",
                "text": f"(User sent a video file: {att.filename})"
            })

    if len(content_parts) <= 1:  # Only text, no media
        return None

    messages = [{"role": "user", "content": content_parts}]

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vyrion.app",
        "X-Title": "Vyrion AI",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                VISION_URL,
                headers=headers,
                json={
                    "model": VISION_MODEL,
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("[vision] HTTP %s: %s", resp.status, body[:200])
                    return None
                data = await resp.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return clean_ai_output(text.strip(), max_len=600) if text else None
    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError, TypeError) as exc:
        log.warning("[vision] failed: %s", exc)
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

        # ── Don't respond in DMs if user has an open ticket ────────────────────
        if is_dm:
            existing_ticket = await get_user_open_ticket(message.author.id)
            if existing_ticket is not None:
                return

        if not is_dm and not mentioned and "vyrion" not in message.content.lower() and "botdi" not in message.content.lower():
            return
        user = message.author
        owner = user.id in _owners
        query = message.clean_content
        if self.bot.user:
            query = query.replace(f"@{self.bot.user.display_name}", "")
        query = re.sub(r"(?i)^(?:vyrion|botdi)[,:\s]+", "", query).strip()

        # ── Check for image/video attachments → vision mode ────────────────────
        attachments = message.attachments
        has_media = any(
            any(att.filename.lower().endswith(ext) for ext in _IMAGE_EXTS | _VIDEO_EXTS)
            for att in attachments
        )

        if not query and not has_media:
            await message.reply("Hey! What's up?", delete_after=8)
            return

        if is_dm and not owner:
            allowed, _ = check_dm_quota(user.id)
            if not allowed:
                await message.reply(f"You've hit today's {DM_DAILY_LIMIT}-message DM limit. Resets at midnight UTC.")
                return
        if not owner and (any(pattern in query.lower() for pattern in _BLOCKED) or check_profanity_at_bot(query)):
            await message.reply("I can't help with that. Keep it safe and respectful.", delete_after=12)
            await log_action(self.bot, "Vyrion request blocked", f"**User:** {user.mention}\n**Query:** {query[:300]}", color=COLOR_WARN)
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
            reply = None

            # ── Vision mode: user sent image/video ────────────────────────────
            if has_media and OPENROUTER_API_KEY:
                media_types = []
                for att in attachments:
                    if any(att.filename.lower().endswith(ext) for ext in _IMAGE_EXTS):
                        media_types.append("image")
                    elif any(att.filename.lower().endswith(ext) for ext in _VIDEO_EXTS):
                        media_types.append("video")
                media_str = " + ".join(set(media_types))
                log.info("[vision] User %s sent %s, switching to %s", user.id, media_str, VISION_MODEL)
                reply = await _generate_vision(get_memory(user.id), query, attachments)

            # ── Normal text mode ──────────────────────────────────────────────
            if not reply:
                reply = await _generate(get_memory(user.id), query or "What's in this image/video?")

        if not reply:
            await message.reply("AI services are busy right now — try again in a sec.", delete_after=15)
            return
        add_memory(user.id, "user", query or "(sent an image/video)")
        add_memory(user.id, "assistant", reply)
        await save_memory()
        if remaining is not None and remaining <= 3:
            reply += f"\n\n*({remaining}/{DM_DAILY_LIMIT} DM messages left today)*"
        await message.reply(reply)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
