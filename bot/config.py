"""
Nexus — configuration.
"""
import os

DISCORD_TOKEN      : str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY     : str = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY       : str = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY   : str = os.environ.get("CEREBRAS_API_KEY", "")
OPENROUTER_API_KEY : str = os.environ.get("OPENROUTER_API_KEY", "")
HUGGINGFACE_API_KEY: str = os.environ.get("HUGGINGFACE_API_KEY", "")

# Discord bot owner — /subagent restricted to this user
BOT_OWNER_ID: int = int(os.environ.get("BOT_OWNER_ID", "0"))

def _parse_channel_id(value: str) -> int:
    if value.startswith("http"):
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    return int(value)

ADMIN_CHANNEL_ID : int = _parse_channel_id(os.environ["ADMIN_CHANNEL_ID"])
LOG_CHANNEL_ID   : int = _parse_channel_id(os.environ["LOG_CHANNEL_ID"])
SUPPORT_LINK     : str = os.environ["SUPPORT_LINK"]

BOT_NAME   = "Nexus"
BOT_PREFIX = "/"
BOT_COLOR  = 0x5865F2
COLOR_OK   = 0x23A55A
COLOR_WARN = 0xF0B132
COLOR_ERR  = 0xED4245
COLOR_INFO = 0x5865F2

# ── AI — Gemini (vision-capable) ─────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_FALLBACK_MODELS = [
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

GROQ_MODEL       = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_URL         = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_MODEL   = "llama3.1-8b"
CEREBRAS_URL     = "https://api.cerebras.ai/v1/chat/completions"
OPENROUTER_MODEL = "meta-llama/llama-3.2-3b-instruct:free"
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

DM_DAILY_LIMIT       = 15
MEMORY_MAX_EXCHANGES = 100

STRIKES_FOR_BAN         = 3
STRIKE_TIMEOUT_SECONDS  = 86_400
AUTOMOD_TIMEOUT_SECONDS = 3_600
FILTER_COOLDOWN_SECONDS = 15

BLACKLISTED_WORDS: list[str] = []
