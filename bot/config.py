import os

DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY: str = os.environ.get("CEREBRAS_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
FIREWORKS_API_KEY: str = os.environ.get("FIREWORKS_API_KEY", "")


def _parse_channel_id(value: str) -> int:
    if not value:
        return 0
    if value.startswith("http"):
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    return int(value)


ADMIN_CHANNEL_ID: int = _parse_channel_id(os.environ.get("ADMIN_CHANNEL_ID", "0"))
LOG_CHANNEL_ID: int = _parse_channel_id(os.environ.get("LOG_CHANNEL_ID", "0"))
SUPPORT_LINK: str = os.environ.get("SUPPORT_LINK", "")

BOT_PREFIX = "!"
BOT_COLOR = 0x5865F2
COLOR_OK = 0x23A55A
COLOR_WARN = 0xF0B132
COLOR_ERR = 0xED4245
COLOR_INFO = 0x5865F2

# ── AI chat models (free / cheap only — never expensive) ──────────────────────
GEMINI_MODEL = "gemini-2.0-flash"  # free tier
GEMINI_FALLBACK_MODELS = ["gemini-1.5-flash"]  # removed gemini-1.5-pro (expensive)

GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_MODEL = "llama3.1-8b"
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
OPENROUTER_MODEL = "meta-llama/llama-3.2-3b-instruct:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Site engineering provider chain (all free models) ─────────────────────────
SITE_GROQ_MODEL = "gpt-oss-20b"
SITE_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SITE_OPENROUTER_MODEL = "gpt-oss-20b:free"
SITE_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_FIREWORKS_MODEL = "gpt-oss-20b"
SITE_FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

# Extra free OpenRouter fallback models for site generation
SITE_OPENROUTER_FALLBACK_MODELS = [
    "gpt-oss-20b:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2.5-7b-instruct:free",
]

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

# ── Scam / suspicious website prevention ─────────────────────────────────────
SITE_BLOCKED_KEYWORDS: list[str] = [
    "phishing", "scam", "fake login", "credential harvest", "carding",
    "money laundering", "crypto scam", "pyramid scheme", "ponzi",
    "fake giveaway", "free robux", "free nitro", "steam scam",
    "account steal", "password steal", "token grab", "token logger",
    "keylogger", "malware", "ransomware", "spyware", "trojan",
    "credit card steal", "bank login steal", "ssn steal",
    "counterfeit", "illegal drug", "weapons sale", "hitman",
    "child exploit", "csam", "revenge porn",
    "piracy", "cracked software", "warez", "serial key gen",
]

SITE_BLOCKED_PATTERNS = [
    r"(?i)fake\s+(?:login|sign[\s-]?in|auth)\s+page",
    r"(?i)(?:steal|grab|harvest)\s+(?:password|credential|token|credit)",
    r"(?i)(?:free|unlimited)\s+(?:robux|nitro|vbucks|fortnite|discord\s+nitro)",
    r"(?i)(?:phishing|spoofing)\s+(?:page|site|link|form)",
]

# ── Strike system escalation ─────────────────────────────────────────────────
# 1 strike = DM warning + 10 hour timeout
# 2 strikes = DM warning + 2 day timeout
# 3 strikes = ban
STRIKES_FOR_BAN = 3
STRIKE_1_TIMEOUT_SECONDS = 10 * 3600       # 10 hours
STRIKE_2_TIMEOUT_SECONDS = 2 * 24 * 3600   # 2 days
STRIKE_TIMEOUT_SECONDS = STRIKE_1_TIMEOUT_SECONDS  # legacy compat
AUTOMOD_TIMEOUT_SECONDS = 3_600
FILTER_COOLDOWN_SECONDS = 15
BLACKLISTED_WORDS: list[str] = []

DM_DAILY_LIMIT = 15
MEMORY_MAX_EXCHANGES = 50
