"""
Vyrion — configuration.
"""
import os

DISCORD_TOKEN      : str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY     : str = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY       : str = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY   : str = os.environ.get("CEREBRAS_API_KEY", "")
OPENROUTER_API_KEY : str = os.environ.get("OPENROUTER_API_KEY", "")
HUGGINGFACE_API_KEY: str = os.environ.get("HUGGINGFACE_API_KEY", "")

# Discord bot owner — hardcoded fallback so commands always work for the real owner
_BOT_OWNER_ENV = int(os.environ.get("BOT_OWNER_ID", "0"))
BOT_OWNER_ID: int = _BOT_OWNER_ENV if _BOT_OWNER_ENV else 1109828785425096756

def _parse_channel_id(value: str) -> int:
    if value.startswith("http"):
        return int(value.rstrip("/").rsplit("/", 1)[-1])
    return int(value)

ADMIN_CHANNEL_ID : int = _parse_channel_id(os.environ["ADMIN_CHANNEL_ID"])
LOG_CHANNEL_ID   : int = _parse_channel_id(os.environ["LOG_CHANNEL_ID"])
SUPPORT_LINK     : str = os.environ["SUPPORT_LINK"]

BOT_NAME   = "Vyrion"
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

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Rate limiting ────────────────────────────────────────────────────────────
# Server: 5 messages per hour for everyone, owner has infinite
# DM: 15 messages per 3-day cycle, degrading: day1=15, day2=10, day3=5, then 0
# Subagent: 5 per week for guild owners, bot owner infinite, admins NOT allowed
SERVER_RATE_LIMIT      = 5
SERVER_RATE_WINDOW     = 3600
DM_RATE_LIMIT_CYCLE    = 3 * 24 * 3600
DM_DAY1_LIMIT          = 15
DM_DAY2_LIMIT          = 10
DM_DAY3_LIMIT          = 5
SUBAGENT_RATE_LIMIT    = 5
SUBAGENT_RATE_WINDOW   = 7 * 24 * 3600  # 1 week

# ── AI model registry ─────────────────────────────────────────────────────────
# Models known to support native OpenAI-style tool/function calling
# These are tried first for subagent function calling
GROQ_TOOL_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-3.1-70b-versatile",
]

OPENROUTER_TOOL_MODELS = [
    "google/gemini-flash-1.5:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "deepseek/deepseek-chat:free",
    "mistralai/mistral-nemo:free",
]

CEREBRAS_TOOL_MODELS = [
    "llama3.1-8b",
    "llama-3.3-70b",
]

# All available models for text generation (broader list)
GROQ_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "deepseek-r1-distill-llama-70b",
    "deepseek-r1-distill-qwen-32b",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-3.1-70b-versatile",
]

OPENROUTER_MODELS = [
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.2-1b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-2-9b-it:free",
    "google/gemma-7b-it:free",
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-r1-zero:free",
    "deepseek/deepseek-chat:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "qwen/qwq-32b:free",
    "mistralai/mistral-7b-instruct:free",
    "mistralai/mistral-nemo:free",
    "microsoft/phi-3-mini-128k-instruct:free",
    "microsoft/phi-3-medium-128k-instruct:free",
    "huggingfaceh4/zephyr-7b-beta:free",
    "openchat/openchat-7b:free",
    "undi95/toppy-m-7b:free",
    "gryphe/mythomax-l2-13b:free",
    "nousresearch/nous-hermes2-mixtral-8x7b-dpo:free",
    "sao10k/l3-euryale-70b:free",
    "sophosympatheia/rogue-rose-103b-v0.2:free",
    "thedrummer/rocinante-12b:free",
]

HUGGINGFACE_MODELS = [
    "meta-llama/Llama-3.2-3B-Instruct",
    "meta-llama/Llama-3.2-1B-Instruct",
    "google/gemma-2-2b-it",
    "google/gemma-2-9b-it",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-3B-Instruct",
    "Qwen/Qwen2.5-Coder-32B-Instruct",
    "microsoft/Phi-3-mini-4k-instruct",
    "HuggingFaceH4/zephyr-7b-beta",
    "tiiuae/falcon-7b-instruct",
]

CEREBRAS_MODELS = [
    "llama3.1-8b",
    "llama-3.3-70b",
]

# Default active models (first of each list)
GROQ_MODEL       = GROQ_MODELS[0]
CEREBRAS_MODEL   = CEREBRAS_MODELS[0]
OPENROUTER_MODEL = OPENROUTER_MODELS[0]
HF_MODEL_DEFAULT  = HUGGINGFACE_MODELS[0]

# Mutable active model overrides (changed via /model command)
ACTIVE_GROQ_MODEL       = GROQ_MODEL
ACTIVE_OPENROUTER_MODEL = OPENROUTER_MODEL
ACTIVE_HF_MODEL         = HF_MODEL_DEFAULT
ACTIVE_CEREBRAS_MODEL   = CEREBRAS_MODEL
ACTIVE_GEMINI_MODEL     = GEMINI_MODEL

MEMORY_MAX_EXCHANGES = 100

STRIKES_FOR_BAN         = 3
STRIKE_TIMEOUT_SECONDS  = 86_400
AUTOMOD_TIMEOUT_SECONDS = 3_600
FILTER_COOLDOWN_SECONDS = 15

BLACKLISTED_WORDS: list[str] = []
