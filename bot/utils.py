"""
Shared utilities: action logging, appeal embeds, PII / TOS filter, profanity guard, enforced rules.
"""

from __future__ import annotations

import re
import discord
from config import LOG_CHANNEL_ID, SUPPORT_LINK, BOT_COLOR


# ── PII patterns ──────────────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email address",    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone number",     re.compile(r"(?:\+\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}\b|\+\d{10,15}")),
    ("SSN",              re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit card",      re.compile(r"\b(?:\d[ \-]?){13,16}\b")),
    ("IP address",       re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("home address",     re.compile(r"\d{1,5}\s+\w[\w\s]*\b(street|st|avenue|ave|road|rd|blvd|lane|ln|drive|dr|court|ct)\b", re.I)),
]

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

_OUTPUT_PROFANITY = re.compile(
    r"\b(fuck(?:ing)?|shit|bitch|bastard|cunt|asshole|dick|motherfuck(?:er|ing)?|"
    r"retard(?:ed)?|faggot|whore|slut|cock|piss)\b",
    re.I,
)


def check_pii_tos(text: str) -> tuple[bool, str]:
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            return True, f"Contains {label}"
    lower = text.lower()
    for kw in _TOS_KEYWORDS:
        if kw in lower:
            return True, f"Possible TOS violation: '{kw.strip()}'"
    return False, ""


def check_profanity_at_bot(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in _PROFANITY)


def clean_ai_output(text: str, max_len: int = 380) -> str:
    text = _OUTPUT_PROFANITY.sub("***", text)
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


# ── AI output sanitization ─────────────────────────────────────────────────────

_PING_RE = re.compile(
    r"@(?:everyone|here|&\d+|<@!?\d+>|<#\d+>|<@&\d+>)",
    re.I,
)

# Patterns that indicate the AI is leaking API/provider internals
_API_LEAK_PATTERNS = [
    re.compile(r"(?:API|provider|model|Gemini|Groq|OpenRouter|HuggingFace|Cerebras|Llama|Gemma|DeepSeek|Qwen|Mistral|Phi)\s*(?:error|failed|unavailable|returned|key|token|limit|quota|rate)\s*[^\n.]*", re.I),
    re.compile(r"\d{3}\s*(?:error|status|response|forbidden|unauthorized|not found|bad request)[^\n.]*", re.I),
    re.compile(r"No AI provider available[^.]*", re.I),
    re.compile(r"(?:sk-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{35}|hf_[A-Za-z0-9]{20,})", re.I),
    re.compile(r"(?:rate|quota|limit)\s*(?:exceeded|reached|hit)[^\n.]*", re.I),
    re.compile(r"(?:timeout|timed out|connection (?:refused|reset|error))[^\n.]*", re.I),
    re.compile(r"(?:HTTP|HTTPS)\s*\d{3}[^\n.]*", re.I),
    re.compile(r"(?:internal server error|service unavailable|gateway timeout|bad gateway)[^\n.]*", re.I),
    re.compile(r"(?:invalid api key|authentication (?:failed|error)|unauthorized access)[^\n.]*", re.I),
    re.compile(r"(?:model (?:not found|overloaded|deprecated|discontinued))[^\n.]*", re.I),
    re.compile(r"(?:billing|credit|subscription)\s*(?:issue|problem|required|expired)[^\n.]*", re.I),
]


def sanitize_ai_output(text: str, *, user_message: str = "") -> str:
    text = _PING_RE.sub("", text)
    text = _OUTPUT_PROFANITY.sub("***", text)
    for pattern in _API_LEAK_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"sk-[A-Za-z0-9]{20,}", "", text)
    text = re.sub(r"AIza[A-Za-z0-9_-]{35}", "", text)
    text = re.sub(r"hf_[A-Za-z0-9]{20,}", "", text)
    # Strip any leftover "I'll put this in my own words" prefix from previous version
    text = re.sub(r"^I'll put this in my own words:\s*", "", text, flags=re.I)
    if user_message and _is_copying(text, user_message):
        text = _rephrase_copy(text, user_message)
    text = re.sub(r"\s{3,}", "\n", text)
    text = re.sub(r"^\s+|\s+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_copying(ai_text: str, user_text: str) -> bool:
    """Detect if AI is copying user's message verbatim or near-verbatim."""
    ai_lower = ai_text.lower().strip()
    user_lower = user_text.lower().strip()
    if not user_lower or not ai_lower:
        return False
    user_words = user_lower.split()
    if len(user_words) < 4:
        return False
    # Check 5-word chunks (lowered from 8 for stricter detection)
    chunk_size = 5
    for i in range(len(user_words) - chunk_size + 1):
        chunk = " ".join(user_words[i:i + chunk_size])
        if chunk in ai_lower:
            return True
    # Check if AI starts with the same 4+ words as user message
    if len(user_words) >= 4:
        first4 = " ".join(user_words[:4])
        if ai_lower.startswith(first4):
            return True
    # Check if AI ends with the same 4+ words as user message
    if len(user_words) >= 4:
        last4 = " ".join(user_words[-4:])
        if ai_lower.endswith(last4):
            return True
    # Check if >60% of user words appear in same order in AI text
    if len(user_words) >= 6:
        match_count = 0
        search_pos = 0
        for w in user_words:
            idx = ai_lower.find(w, search_pos)
            if idx != -1:
                match_count += 1
                search_pos = idx + len(w)
        if match_count / len(user_words) > 0.6:
            return True
    return False


def _rephrase_copy(ai_text: str, user_text: str) -> str:
    """When copying is detected, strip copied segments and rephrase."""
    user_lower = user_text.lower().strip()
    ai_lower = ai_text.lower()
    # If the AI text is mostly the user's message, replace with a generic response
    user_words = user_lower.split()
    if len(user_words) >= 4:
        # Remove any 5-word chunks that match user text
        chunk_size = 5
        words = ai_text.split()
        result_words = list(words)
        i = 0
        while i < len(result_words) - chunk_size + 1:
            chunk = " ".join(result_words[i:i + chunk_size]).lower()
            if chunk in user_lower:
                del result_words[i:i + chunk_size]
            else:
                i += 1
        cleaned = " ".join(result_words).strip()
        if cleaned and len(cleaned.split()) >= 3:
            return cleaned
    # Fallback: if we can't clean it, return a neutral response
    return "I understand. Let me know if you need help with anything specific."


def count_words(text: str) -> int:
    clean = re.sub(r"```[\s\S]*?```", " ", text)
    return len(clean.split())


def enforce_word_limit(text: str, *, is_code: bool = False, normal_limit: int = 40, code_limit: int = 100) -> str:
    has_line_breaks = text.count("\n") >= 4
    has_rhyme_pattern = bool(re.search(r"(\w+)\s*\n.*\1\s*$", text, re.MULTILINE))
    if has_line_breaks and has_rhyme_pattern:
        normal_limit = 25
    limit = code_limit if is_code else normal_limit
    words = text.split()
    if len(words) <= limit:
        return text
    truncated = " ".join(words[:limit])
    last_period = truncated.rfind(".")
    if last_period > limit * 0.7:
        truncated = truncated[:last_period + 1]
    return truncated + "…"


# ── Enforced rules ─────────────────────────────────────────────────────────────

def append_enforced_rules(text: str, rules_text: str) -> str:
    """Programmatically append enforced rules to every AI response."""
    if not rules_text or not rules_text.strip():
        return text
    suffix = f"\n\n📋 **Enforced Rules:**\n{rules_text}"
    if len(text) + len(suffix) > 1900:
        text = text[:1900 - len(suffix)]
    return text + suffix


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
    mention_match = re.match(r"<@!?(\d{17,20})>", argument)
    if mention_match:
        return int(mention_match.group(1))
    if re.match(r"^\d{17,20}$", argument):
        return int(argument)
    return None
