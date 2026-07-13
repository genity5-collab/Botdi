"""
Shared utilities: action logging, appeal embeds, PII / TOS filter.
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

# Simple keyword list for TOS-violating content
_TOS_KEYWORDS: list[str] = [
    "buy account", "sell account", "account trade", "doxx", "dox ",
    "swat", "raid server", "ddos", "botnet", "self harm", "kill yourself",
    "kys ", "csam", "cp link",
]


def check_pii_tos(text: str) -> tuple[bool, str]:
    """
    Returns (violated, reason).
    violated = True if PII or TOS content detected.
    """
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            return True, f"Contains {label}"

    lower = text.lower()
    for kw in _TOS_KEYWORDS:
        if kw in lower:
            return True, f"Possible TOS violation: '{kw.strip()}'"

    return False, ""


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
