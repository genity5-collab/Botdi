import os

DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY: str = os.environ.get("CEREBRAS_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
FIREWORKS_API_KEY: str = os.environ.get("FIREWORKS_API_KEY", "")


def _parse_channel_id(value: str) -> int:
    if value.startswith("http"):
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    return int(value)


ADMIN_CHANNEL_ID: int = _parse_channel_id(os.environ["ADMIN_CHANNEL_ID"])
LOG_CHANNEL_ID: int = _parse_channel_id(os.environ["LOG_CHANNEL_ID"])
SUPPORT_LINK: str = os.environ["SUPPORT_LINK"]

BOT_PREFIX = "!"
BOT_COLOR = 0x5865F2
COLOR_OK = 0x23A55A
COLOR_WARN = 0xF0B132
COLOR_ERR = 0xED4245
COLOR_INFO = 0x5865F2

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_FALLBACK_MODELS = ["gemini-1.5-pro", "gemini-1.5-flash"]

GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_MODEL = "llama3.1-8b"
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
OPENROUTER_MODEL = "meta-llama/llama-3.2-3b-instruct:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# App Engineering ("/site") provider config
# Each provider stores its own model identifier separately.
SITE_GROQ_MODEL = "gpt-oss-20b"
SITE_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SITE_OPENROUTER_MODEL = "gpt-oss-20b:free"
SITE_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SITE_FIREWORKS_MODEL = "gpt-oss-20b"
SITE_FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

# Provider fallback order for Botdi-controlled generation
SITE_PROVIDER_CHAIN = [
    {"name": "groq", "url": SITE_GROQ_URL, "api_key": GROQ_API_KEY, "model": SITE_GROQ_MODEL},
    {"name": "openrouter", "url": SITE_OPENROUTER_URL, "api_key": OPENROUTER_API_KEY, "model": SITE_OPENROUTER_MODEL},
    {"name": "fireworks", "url": SITE_FIREWORKS_URL, "api_key": FIREWORKS_API_KEY, "model": SITE_FIREWORKS_MODEL},
]

SITE_FREE_MONTHLY_LIMIT = 5
SITE_MAX_DEBUG_RETRIES = 3
SITE_PREVIEW_BASE_URL = os.environ.get("SITE_PREVIEW_BASE_URL", "https://preview.botdi.app")
SITE_PREVIEW_EXPIRY_HOURS = 24
SITE_MAX_PROJECTS_PER_USER = 10

DM_DAILY_LIMIT = 15
MEMORY_MAX_EXCHANGES = 50

STRIKES_FOR_BAN = 3
STRIKE_TIMEOUT_SECONDS = 86_400
AUTOMOD_TIMEOUT_SECONDS = 3_600
FILTER_COOLDOWN_SECONDS = 15
BLACKLISTED_WORDS: list[str] = []
