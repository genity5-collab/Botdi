import re
import logging
from typing import Any

log = logging.getLogger("vyrion.utils")

# ── PII / sensitive-content detection ──────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email address",    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # Phone: requires + or () or explicit separators to avoid matching Discord IDs / 10-digit numbers
    ("phone number",     re.compile(r"(?:\+\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b|\+\d{10,15}")),
    ("SSN",              re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit card",      re.compile(r"\b(?:\d[ \-]?){13,16}\b")),
    ("IP address",       re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("home address",     re.compile(r"\d{1,5}\s+\w[\w\s]*\b(street|st|avenue|ave|road|rd|blvd|lane|ln|drive|dr|court|ct)\b", re.I)),
]

# TOS-violating content
_TOS_KEYWORDS: list[str] = [
    "buy account", "sell account", "account trade", "doxx", "dox ",
    "swat", "raid server", "ddos", "botnet", "self harm", "kill yourself",
    "kys ", "csam", "cp link",
    "loli", "shota", "underage nsfw", "minor nsfw",
    "how to make bomb", "bomb instructions", "meth recipe", "drug recipe",
    "stolen credit card", "carding", "fullz", "cvv dump",
    "phishing kit", "malware download", "rat trojan", "keylogger download",
    "whatsapp hack", "instagram hack", "account hack tool",
    "nitro scam", "steam scam", "crypto scam",
    "hitman", "assassination", "murder for hire",
    "human trafficking", "organ harvesting",
    "leaked nudes", "revenge porn", "deepfake nude",
    "self-harm", "suicide method", "cutting yourself",
    "school shooting", "mass shooting",
]

# Words that trigger a warning when aimed at the bot (genuine profanity only)
_PROFANITY: set[str] = {
    "fuck", "fucker", "fucking", "fuk", "f**k", "f*ck",
    "shit", "sh*t", "s**t",
    "bitch", "b*tch",
    "bastard", "cunt", "c**t",
    "asshole", "ass hole",
    "dick", "d*ck",
    "motherfucker", "mofo",
    "faggot", "fag",
    "whore", "slut",
    "cock", "c*ck",
    "stfu", "shut the fuck up",
}


def check_pii_tos(text: str) -> tuple[bool, str]:
    """Return (True, reason) if text contains PII or TOS-violating content."""
    lower = text.lower()
    for kw in _TOS_KEYWORDS:
        if kw in lower:
            return True, f"TOS violation ({kw.strip()})"
    for label, pat in _PII_PATTERNS:
        if pat.search(text):
            return True, f"contains {label}"
    return False, ""


def check_profanity_at_bot(text: str) -> bool:
    """Return True if text contains profanity aimed at the bot."""
    lower = text.lower()
    return any(word in lower for word in _PROFANITY)


# ── Logging ────────────────────────────────────────────────────────────────────

async def log_action(bot, title: str, description: str, color: int = 0x5865F2) -> None:
    """Send an embed to the log channel."""
    import discord
    channel = bot.get_channel(LOG_CHANNEL_ID) if hasattr(bot, 'get_channel') else None
    if channel is None:
        try:
            channel = await bot.fetch_channel(LOG_CHANNEL_ID)
        except Exception:
            return
    if channel:
        embed = discord.Embed(title=title, description=description, color=color)
        await channel.send(embed=embed)


# Late import to avoid circular dependency
try:
    from config import LOG_CHANNEL_ID
except ImportError:
    LOG_CHANNEL_ID = 0
