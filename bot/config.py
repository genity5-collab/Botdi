import os

# ── Core credentials (loaded from Replit Secrets) ────────────────────────────
DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
def _parse_channel_id(value: str) -> int:
    """Accept a raw integer ID or a full Discord channel URL."""
    # URL form: https://discord.com/channels/<guild>/<channel>
    if value.startswith("http"):
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    return int(value)

ADMIN_CHANNEL_ID: int = _parse_channel_id(os.environ["ADMIN_CHANNEL_ID"])
LOG_CHANNEL_ID: int = _parse_channel_id(os.environ["LOG_CHANNEL_ID"])
SUPPORT_LINK: str = os.environ["SUPPORT_LINK"]

# ── Bot settings ──────────────────────────────────────────────────────────────
BOT_PREFIX = "!"
BOT_COLOR = 0x5865F2          # Discord blurple

# ── AI settings ───────────────────────────────────────────────────────────────
AI_COOLDOWN_SECONDS = 60      # Per-user cooldown for AI ping responses
GEMINI_MODEL = "gemini-flash-latest"
# Fallback chain tried in order when primary model is overloaded / errors
GEMINI_FALLBACK_MODELS = [
    "gemini-2.0-flash-lite",   # lighter, still fast
    "gemini-1.5-flash-8b",     # smallest/cheapest — last resort
]

# ── Moderation settings ───────────────────────────────────────────────────────
STRIKES_FOR_BAN = 3
STRIKE_TIMEOUT_SECONDS = 86_400        # 24 h  (1 strike)
AUTOMOD_TIMEOUT_SECONDS = 3_600        # 1 h   (blacklisted word)
FILTER_COOLDOWN_SECONDS = 15           # Per-user automod check cooldown

# ── Blacklisted words (edit this list to add/remove terms) ───────────────────
BLACKLISTED_WORDS: list[str] = [
    # Add lowercase words/phrases here, e.g.: "badword", "slur"
]
