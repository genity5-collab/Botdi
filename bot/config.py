import os

# ── Core credentials (loaded from Replit Secrets) ────────────────────────────
DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")

def _parse_channel_id(value: str) -> int:
    """Accept a raw integer ID or a full Discord channel URL."""
    if value.startswith("http"):
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    return int(value)

ADMIN_CHANNEL_ID: int = _parse_channel_id(os.environ["ADMIN_CHANNEL_ID"])
LOG_CHANNEL_ID: int = _parse_channel_id(os.environ["LOG_CHANNEL_ID"])
SUPPORT_LINK: str = os.environ["SUPPORT_LINK"]

# ── Bot settings ──────────────────────────────────────────────────────────────
BOT_PREFIX = "!"
BOT_COLOR   = 0x5865F2   # Discord blurple
COLOR_OK    = 0x23A55A   # Green  — success
COLOR_WARN  = 0xF0B132   # Yellow — warning
COLOR_ERR   = 0xED4245   # Red    — error / punishment
COLOR_INFO  = 0x5865F2   # Blurple — informational

# ── AI settings ───────────────────────────────────────────────────────────────
AI_COOLDOWN_SECONDS = 60       # Per-user cooldown for AI @mention responses

# Gemini model chain (tried in order)
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_FALLBACK_MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-8b",
]

# Groq / Llama — final fallback after all Gemini models fail
GROQ_MODEL   = "llama-3.1-8b-instant"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── Moderation settings ───────────────────────────────────────────────────────
STRIKES_FOR_BAN          = 3
STRIKE_TIMEOUT_SECONDS   = 86_400   # 24 h timeout per strike
AUTOMOD_TIMEOUT_SECONDS  = 3_600    # 1 h timeout for blacklisted word
FILTER_COOLDOWN_SECONDS  = 15       # Per-user automod filter cooldown

# ── Blacklisted words (edit this list to add/remove terms) ───────────────────
BLACKLISTED_WORDS: list[str] = [
    # Add lowercase words/phrases here, e.g.: "badword", "slur"
]
