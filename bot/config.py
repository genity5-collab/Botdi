import os

# ── Core credentials ──────────────────────────────────────────────────────────
DISCORD_TOKEN      : str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY     : str = os.environ["GEMINI_API_KEY"]
GROQ_API_KEY       : str = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY   : str = os.environ.get("CEREBRAS_API_KEY", "")
OPENROUTER_API_KEY : str = os.environ.get("OPENROUTER_API_KEY", "")
FIREWORKS_API_KEY  : str = os.environ.get("FIREWORKS_API_KEY", "")

# ── Supabase credentials ──────────────────────────────────────────────────────
SUPABASE_URL           : str = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY   : str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

def _parse_channel_id(value: str) -> int:
    if value.startswith("http"):
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    return int(value)

ADMIN_CHANNEL_ID : int = _parse_channel_id(os.environ["ADMIN_CHANNEL_ID"])
LOG_CHANNEL_ID   : int = _parse_channel_id(os.environ["LOG_CHANNEL_ID"])
SUPPORT_LINK     : str = os.environ["SUPPORT_LINK"]

# ── Bot settings ──────────────────────────────────────────────────────────────
BOT_PREFIX = "/"
BOT_COLOR  = 0x5865F2
COLOR_OK   = 0x23A55A
COLOR_WARN = 0xF0B132
COLOR_ERR  = 0xED4245
COLOR_INFO = 0x5865F2

# ── AI — Gemini ───────────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash-lite",
]

# ── AI — OpenAI-compatible providers ─────────────────────────────────────────
GROQ_MODEL       = "llama-3.3-70b-versatile"
GROQ_URL         = "https://api.groq.com/openai/v1/chat/completions"

CEREBRAS_MODEL   = "llama3.1-8b"
CEREBRAS_URL     = "https://api.cerebras.ai/v1/chat/completions"

OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
OPENROUTER_URL   = "https://openrouter.ai/api/v1/chat/completions"

# ── Limits & quotas ───────────────────────────────────────────────────────────
DM_DAILY_LIMIT       = 15    # free DM messages per user per day (owner = unlimited)
MEMORY_MAX_EXCHANGES = 30    # conversation pairs stored per user (60 messages)

# ── Moderation settings ───────────────────────────────────────────────────────
STRIKES_FOR_BAN         = 3
STRIKE_TIMEOUT_SECONDS  = 86_400
AUTOMOD_TIMEOUT_SECONDS = 3_600
FILTER_COOLDOWN_SECONDS = 15

BLACKLISTED_WORDS: list[str] = [
    "nigger", "nigga", "faggot", "fag", "retard", "tranny", "chink",
    "kike", "spic", "wetback", "coon", "dyke",
]

# ── App Engineering (/site) settings ──────────────────────────────────────────
SITE_FREE_MONTHLY_LIMIT = 5       # free /site messages per user per month
SITE_MAX_DEBUG_RETRIES  = 3       # max automatic repair attempts
SITE_PREVIEW_BASE_URL   = os.environ.get("SITE_PREVIEW_BASE_URL", "https://preview.botdi.app")
SITE_KEY_ENCRYPTION_SALT = os.environ.get("SITE_KEY_SALT", "botdi-site-key-salt-2026")

# AI provider configuration for App Engineering — stored separately per provider
# so model identifiers are NOT assumed interchangeable.
SITE_PROVIDERS = [
    {
        "name":       "Groq",
        "url":        "https://api.groq.com/openai/v1/chat/completions",
        "api_key":    GROQ_API_KEY,
        "model":      "GPTOSS20B",
    },
    {
        "name":       "OpenRouter",
        "url":        "https://openrouter.ai/api/v1/chat/completions",
        "api_key":    OPENROUTER_API_KEY,
        "model":      "GPTOSS20B-FREE",
        "extra_headers": {
            "HTTP-Referer": "https://botdi.app",
            "X-Title":      "Botdi App Engineering",
        },
    },
    {
        "name":       "Fireworks",
        "url":        "https://api.fireworks.ai/inference/v1/chat/completions",
        "api_key":    FIREWORKS_API_KEY,
        "model":      "GPTOSS20B",
    },
]
