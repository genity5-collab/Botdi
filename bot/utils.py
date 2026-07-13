"""
Shared utilities: action logging, appeal embeds, PII / TOS filter, profanity guard.
"""

from __future__ import annotations

import re
import discord
from config import LOG_CHANNEL_ID, SUPPORT_LINK, BOT_COLOR


# ── PII patterns ──────────────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email address",    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone number",     re.compile(r"(\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")),
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
]

# Words that trigger a strike when aimed at the bot
_PROFANITY: set[str] = {
    "fuck", "fucker", "fucking", "fuk", "f**k", "f*ck",
    "shit", "sh*t", "s**t",
    "bitch", "b*tch",
    "bastard", "cunt", "c**t",
    "asshole", "ass hole",
    "dick", "d*ck",
    "motherfucker", "mofo",
    "retard", "retarded",
    "faggot", "fag",
    "whore", "slut",
    "cock", "c*ck",
    "piss off", "piss",
    "stfu", "shut the fuck up",
    "stupid bot", "dumb bot", "idiot bot", "trash bot", "garbage bot",
}

# Words to scrub from AI output (replaced with ***)
_OUTPUT_PROFANITY = re.compile(
    r"\b(fuck(?:ing)?|shit|bitch|bastard|cunt|asshole|dick|motherfuck(?:er|ing)?|"
    r"retard(?:ed)?|faggot|whore|slut|cock|piss)\b",
    re.I,
)


def check_pii_tos(text: str) -> tuple[bool, str]:
    """Returns (violated, reason). True if PII or TOS content detected."""
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            return True, f"Contains {label}"
    lower = text.lower()
    for kw in _TOS_KEYWORDS:
        if kw in lower:
            return True, f"Possible TOS violation: '{kw.strip()}'"
    return False, ""


def check_profanity_at_bot(text: str) -> bool:
    """True if the message contains profanity targeted at the bot."""
    lower = text.lower()
    return any(word in lower for word in _PROFANITY)


def clean_ai_output(text: str, max_len: int = 380) -> str:
    """Strip profanity from AI output and enforce length cap."""
    text = _OUTPUT_PROFANITY.sub("***", text)
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


# ── Appeal embed ──────────────────────────────────────────────────────────────

def build_appeal_embed(reason: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="⚖️ Moderation Action",
        description=(
            f"**Reason:** {reason or 'Violation of server rules'}\n\n"
            "You may appeal this action using the link below."
        ),
        color=BOT_COLOR,
    )
    embed.add_field(name="📋 Appeal", value=f"[Submit an appeal]({SUPPORT_LINK})", inline=False)
    embed.set_footer(text="Appeals are reviewed within 48 hours.")
    return embed


# ── Action log ────────────────────────────────────────────────────────────────

async def log_action(bot: discord.Client, title: str, description: str, color: int = 0xE74C3C) -> None:
    """Send a log embed to LOG_CHANNEL_ID."""
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(title=title, description=description, color=color)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


# ── ID validation ─────────────────────────────────────────────────────────────

def parse_user_id(argument: str) -> int | None:
    """
    Parse a Discord user ID from a mention (<@123>) or a raw integer string.
    Returns the integer ID, or None if invalid.
    """
    mention_match = re.match(r"<@!?(\d{17,20})>", argument)
    if mention_match:
        return int(mention_match.group(1))
    if re.match(r"^\d{17,20}$", argument):
        return int(argument)
    return None
