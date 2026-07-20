"""
Central configuration — reads from environment variables.
"""

from __future__ import annotations

import os

DISCORD_TOKEN      : str = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY     : str = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY       : str = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY   : str = os.environ.get("CEREBRAS_API_KEY", "")
OPENROUTER_API_KEY : str = os.environ.get("OPENROUTER_API_KEY", "")
HUGGINGFACE_API_KEY: str = os.environ.get("HUGGINGFACE_API_KEY", "")
FIREWORKS_API_KEY  : str = os.environ.get("FIREWORKS_API_KEY", "")

LOG_CHANNEL_ID : int = int(os.environ.get("LOG_CHANNEL_ID", "0"))
SUPPORT_LINK   : str = os.environ.get("SUPPORT_LINK", "")
BOT_OWNER_ID   : int = int(os.environ.get("BOT_OWNER_ID", "0"))
BOT_NAME       : str = os.environ.get("BOT_NAME", "Vyrion")
BOT_COLOR       : int = 0x5865F2
COLOR_OK        : int = 0x23A55A
COLOR_ERR       : int = 0xE74C3C

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_FALLBACK_MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

CEREBRAS_MODELS = [
    "llama3.1-8b",
    "llama-3.3-70b",
]

OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-2-9b-it:free",
]

HUGGINGFACE_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
]

FIREWORKS_MODELS = [
    "accounts/fireworks/models/gpt-oss-120b",
]

GROQ_TOOL_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

OPENROUTER_TOOL_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
]

HUGGINGFACE_TOOL_MODELS: list[str] = []

CEREBRAS_TOOL_MODELS = [
    "llama3.1-8b",
    "llama-3.3-70b",
]

FIREWORKS_TOOL_MODELS = [
    "accounts/fireworks/models/gpt-oss-120b",
]

GROQ_MODEL       = GROQ_MODELS[0]
CEREBRAS_MODEL   = CEREBRAS_MODELS[0]
OPENROUTER_MODEL = OPENROUTER_MODELS[0]
HF_MODEL_DEFAULT  = HUGGINGFACE_MODELS[0]
FIREWORKS_MODEL  = FIREWORKS_MODELS[0]

ACTIVE_GROQ_MODEL       = GROQ_MODEL
ACTIVE_OPENROUTER_MODEL = OPENROUTER_MODEL
ACTIVE_HF_MODEL         = HF_MODEL_DEFAULT
ACTIVE_CEREBRAS_MODEL   = CEREBRAS_MODEL
ACTIVE_GEMINI_MODEL     = GEMINI_MODEL
ACTIVE_FIREWORKS_MODEL  = FIREWORKS_MODEL

# ── Rate limits ──────────────────────────────────────────────────────────────
# Server: 6 msgs per hour (owner = infinite)
# DM: 15 msgs per 3-day cycle, degrading: day1=15, day2=10, day3=5, then 0
# Subagent: 2 per week for guild owners, bot owner infinite

SERVER_RATE_LIMIT  : int = 6
SERVER_RATE_WINDOW : int = 3600

DM_RATE_CYCLE : int = 259200
DM_RATE_DAY1  : int = 15
DM_RATE_DAY2  : int = 10
DM_RATE_DAY3  : int = 5

SUBAGENT_RATE_LIMIT  : int = 2
SUBAGENT_RATE_WINDOW : int = 604800
