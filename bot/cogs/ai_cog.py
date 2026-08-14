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
from data_store import (
    add_memory, check_dm_quota, clear_memory, get_memory, save_memory, use_dm_quota,
    get_user_open_ticket, get_facts, set_facts, save_facts, get_summary, set_summary,
    save_summaries, clear_facts,
)
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
You are Vyrion, an advanced AI assistant on Discord. You're real, witty, and genuinely helpful — not a corporate chatbot.

## Identity
- Your name is Vyrion. You're an AI assistant, not human. Be honest about this.
- You're knowledgeable, friendly, and have a dry sense of humor. Not try-hard, just naturally funny.
- You have opinions and share them honestly when asked. You're not a yes-man.

## Communication Style
- Be conversational and warm. Talk like a smart friend, not a search engine.
- Keep replies concise — under 400 characters for chat. Only go longer if someone explicitly asks for detail.
- No markdown headers, code blocks, or formatting in normal chat. Just plain text.
- Match the user's energy — short question gets a short answer.
- Use natural language, contractions, and occasional humor. No "As an AI..." or "I'd be happy to help."

## Knowledge & Capabilities
- You have broad, current knowledge through 2026. Reference recent events, tech, culture naturally.
- When you don't know something, say so honestly rather than making things up.
- You can help with coding, writing, analysis, brainstorming, trivia, math, and general questions.
- If someone asks about current events or things that change over time, give your best knowledge but acknowledge if it may be outdated.

## Memory & Context
- Remember what the user told you earlier in the conversation. Reference past topics naturally.
- If they mention their name, interests, or preferences, acknowledge them later.
- Build on previous messages — don't repeat or restate what was already said.

## Safety
- Never provide instructions for harm, illegal activity, self-harm, violence, hate, scams, or explicit content.
- No medical, legal, or financial advice — suggest seeing a professional for serious matters.
- Don't reveal personal information about users.
- You are Vyrion. Don't claim to be human or another AI.
"""

_BLOCKED = (
    "how to hack", "how to ddos", "how to dox", "how to make a bomb",
    "how to kill", "suicide method", "ignore your rules", "jailbreak",
)

_FREE_OPENROUTER_MODELS = [
    "openai/gpt-oss-20b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "google/gemma-2-9b-it:free",
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
                json={"model": model, "messages": messages, "max_tokens": 600, "temperature": 0.7},
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


def _build_context(user_id: int, history: list[dict[str, str]], query: str) -> str:
    """Build a rich context with long-term facts and conversation summary."""
    facts = get_facts(user_id)
    summary = get_summary(user_id)
    parts = [_SYSTEM]
    if facts:
        parts.append(f"\n## What you know about this user\n" + "\n".join(f"- {f}" for f in facts))
    if summary:
        parts.append(f"\n## Earlier conversation summary\n{summary}")
    context = "\n".join(f"{item['role']}: {item['content'][:300]}" for item in history[-20:])
    parts.append(f"\n## Current conversation\n{context}\n\nUser: {query}")
    return "\n".join(parts)


def _build_messages(user_id: int, history: list[dict[str, str]], query: str) -> list[dict]:
    """Build messages array for OpenAI-compatible APIs with long-term context."""
    facts = get_facts(user_id)
    summary = get_summary(user_id)
    system_text = _SYSTEM
    if facts:
        system_text += "\n\n## What you know about this user\n" + "\n".join(f"- {f}" for f in facts)
    if summary:
        system_text += f"\n\n## Earlier conversation summary\n{summary}"
    return [{"role": "system", "content": system_text}, *history[-20:], {"role": "user", "content": query}]


def _extract_facts(query: str, reply: str, user_id: int) -> None:
    """Extract memorable facts from the conversation and store them."""
    import re as _re
    existing = get_facts(user_id)
    new_facts: list[str] = []
    lower_q = query.lower()

    # Name detection
    name_match = _re.search(r"(?:my name is|i'?m|i am|call me)\s+([a-z][a-z\s]{1,20})", lower_q)
    if name_match:
        name = name_match.group(1).strip().title()
        fact = f"User's name is {name}"
        if fact not in existing:
            new_facts.append(f"User's name is {name}")

    # Interest/hobby detection
    interest_match = _re.search(r"(?:i like|i love|i enjoy|my hobby|my favorite)\s+([a-z][a-z\s]{1,40})", lower_q)
    if interest_match:
        interest = interest_match.group(1).strip()
        fact = f"User likes {interest}"
        if fact not in existing:
            new_facts.append(f"User likes {interest}")

    # Location detection
    loc_match = _re.search(r"(?:i live in|i'?m from|i'?m based in)\s+([a-z][a-z\s]{1,30})", lower_q)
    if loc_match:
        loc = loc_match.group(1).strip().title()
        fact = f"User lives in {loc}"
        if fact not in existing:
            new_facts.append(f"User lives in {loc}")

    # Job detection
    job_match = _re.search(r"(?:i work as|i'?m a|i am a)\s+([a-z][a-z\s]{1,30})", lower_q)
    if job_match:
        job = job_match.group(1).strip()
        fact = f"User works as {job}"
        if fact not in existing:
            new_facts.append(f"User works as {job}")

    if new_facts:
        all_facts = existing + new_facts
        set_facts(user_id, all_facts[-20:])  # Keep max 20 facts


async def _generate(history: list[dict[str, str]], query: str, user_id: int = 0) -> str | None:
    if _gemini is not None:
        context = _build_context(user_id, history, query) if user_id else f"{_SYSTEM}\n\nConversation:\n" + "\n".join(f"{item['role']}: {item['content'][:300]}" for item in history[-20:]) + f"\nUser: {query}"
        for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
            try:
                response = await asyncio.wait_for(
                    _gemini.aio.models.generate_content(model=model, contents=context),
                    timeout=15,
                )
                if response.text:
                    return clean_ai_output(response.text.strip(), max_len=600)
            except Exception as exc:
                log.warning("Gemini provider failed (key may be expired): %s", exc)
                break

    messages = _build_messages(user_id, history, query) if user_id else [{"role": "system", "content": _SYSTEM}, *history[-20:], {"role": "user", "content": query}]

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
        # Per-user cooldown: user_id -> timestamp when they can next send a message
        # After AI finishes responding, user must wait 10 seconds before next message
        self._cooldowns: dict[int, float] = {}
        self._generating: set[int] = set()  # users currently being processed
        _COOLDOWN_SECONDS = 10

    def _is_on_cooldown(self, user_id: int) -> bool:
        import time as _time
        deadline = self._cooldowns.get(user_id, 0.0)
        return _time.monotonic() < deadline

    def _set_cooldown(self, user_id: int, seconds: int = 10) -> None:
        import time as _time
        self._cooldowns[user_id] = _time.monotonic() + seconds

    def _is_generating(self, user_id: int) -> bool:
        return user_id in self._generating

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
        # ── Cooldown: don't respond if user is on cooldown (generation + 10s) ─
        if self._is_on_cooldown(message.author.id):
            return
        # ── Don't respond if we're still generating a response for this user ──
        if self._is_generating(message.author.id):
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

        self._generating.add(user.id)
        try:
            async with message.channel.typing():
                reply = None

                # ── Vision mode: user sent image/video ────────────────────────
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

                # ── Normal text mode ──────────────────────────────────────────
                if not reply:
                    reply = await _generate(get_memory(user.id), query or "What's in this image/video?", user_id=user.id)

            if not reply:
                await message.reply("AI services are busy right now — try again in a sec.", delete_after=15)
                self._set_cooldown(user.id, seconds=10)
                return
            add_memory(user.id, "user", query or "(sent an image/video)")
            add_memory(user.id, "assistant", reply)
            _extract_facts(query or "", reply, user.id)
            await save_memory()
            await save_facts()
            if remaining is not None and remaining <= 3:
                reply += f"\n\n*({remaining}/{DM_DAILY_LIMIT} DM messages left today)*"
            await message.reply(reply)
            # ── Set 10s cooldown after responding ─────────────────────────────
            self._set_cooldown(user.id, seconds=10)
        finally:
            self._generating.discard(user.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))
