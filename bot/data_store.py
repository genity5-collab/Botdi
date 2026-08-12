"""
Lightweight JSON-backed persistent store.
Covers: strikes, tickets, conversation memory, DM daily quotas.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import random
import string
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

STRIKES_FILE   = DATA_DIR / "strikes.json"
TICKETS_FILE   = DATA_DIR / "tickets.json"
MEMORIES_FILE  = DATA_DIR / "memories.json"
DM_QUOTA_FILE  = DATA_DIR / "dm_quota.json"
SUMMARIES_FILE = DATA_DIR / "summaries.json"
FACTS_FILE     = DATA_DIR / "user_facts.json"

_lock = asyncio.Lock()

# ── Internal helpers ──────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

# In-memory caches
_strikes  : dict[str, int]            = _load(STRIKES_FILE)
_tickets  : dict[str, dict[str, Any]] = _load(TICKETS_FILE)
_memories : dict[str, list[dict]]     = _load(MEMORIES_FILE)
_dm_quota : dict[str, dict]           = _load(DM_QUOTA_FILE)

# ── Strikes ───────────────────────────────────────────────────────────────────

async def get_strikes(user_id: int) -> int:
    async with _lock:
        return _strikes.get(str(user_id), 0)

async def add_strike(user_id: int) -> int:
    async with _lock:
        key = str(user_id)
        _strikes[key] = _strikes.get(key, 0) + 1
        _save(STRIKES_FILE, _strikes)
        return _strikes[key]

async def reset_strikes(user_id: int) -> None:
    async with _lock:
        _strikes.pop(str(user_id), None)
        _save(STRIKES_FILE, _strikes)

async def get_all_strikes() -> dict[str, int]:
    async with _lock:
        return dict(_strikes)

# ── Tickets ───────────────────────────────────────────────────────────────────

def _gen_ticket_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def create_ticket(user_id: int, category: str) -> str:
    async with _lock:
        tid = _gen_ticket_id()
        while tid in _tickets:
            tid = _gen_ticket_id()
        _tickets[tid] = {"user_id": user_id, "category": category, "status": "open", "last_activity": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        _save(TICKETS_FILE, _tickets)
        return tid

async def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    async with _lock:
        return _tickets.get(ticket_id.upper())

async def close_ticket(ticket_id: str) -> bool:
    async with _lock:
        tid = ticket_id.upper()
        if tid not in _tickets:
            return False
        _tickets[tid]["status"] = "closed"
        _tickets[tid]["last_activity"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _save(TICKETS_FILE, _tickets)
        return True

async def get_user_open_ticket(user_id: int) -> str | None:
    async with _lock:
        for tid, t in _tickets.items():
            if t["user_id"] == user_id and t["status"] == "open":
                return tid
        return None

# ── Conversation Memory ───────────────────────────────────────────────────────

MAX_EXCHANGES = 25   # keep last 25 pairs = 50 messages per user

def get_memory(user_id: int) -> list[dict]:
    """Return stored message history for a user as OpenAI message dicts."""
    return list(_memories.get(str(user_id), []))

def add_memory(user_id: int, role: str, content: str) -> list[dict]:
    """Append a message to history; trim to MAX_EXCHANGES * 2.
    Returns any messages that were dropped during trimming."""
    key  = str(user_id)
    hist = _memories.get(key, [])
    hist.append({"role": role, "content": content[:800]})
    dropped: list[dict] = []
    if len(hist) > MAX_EXCHANGES * 2:
        dropped = hist[:len(hist) - (MAX_EXCHANGES * 2)]
        hist = hist[-(MAX_EXCHANGES * 2):]
    _memories[key] = hist
    return dropped

async def save_memory() -> None:
    async with _lock:
        _save(MEMORIES_FILE, _memories)

async def clear_memory(user_id: int) -> None:
    async with _lock:
        _memories.pop(str(user_id), None)
        _save(MEMORIES_FILE, _memories)

# ── Conversation Summaries ────────────────────────────────────────────────────

_summaries: dict[str, str] = _load(SUMMARIES_FILE)

def get_summary(user_id: int) -> str:
    return _summaries.get(str(user_id), "")

def set_summary(user_id: int, text: str) -> None:
    _summaries[str(user_id)] = text[:1500]

async def save_summaries() -> None:
    async with _lock:
        _save(SUMMARIES_FILE, _summaries)

async def clear_summary(user_id: int) -> None:
    async with _lock:
        _summaries.pop(str(user_id), None)
        _save(SUMMARIES_FILE, _summaries)

# ── Long-term User Facts ──────────────────────────────────────────────────────

_facts: dict[str, list[str]] = _load(FACTS_FILE)

def get_facts(user_id: int) -> list[str]:
    return list(_facts.get(str(user_id), []))

def set_facts(user_id: int, facts: list[str]) -> None:
    _facts[str(user_id)] = [f[:300] for f in facts[:20]]

async def save_facts() -> None:
    async with _lock:
        _save(FACTS_FILE, _facts)

async def clear_facts(user_id: int) -> None:
    async with _lock:
        _facts.pop(str(user_id), None)
        _save(FACTS_FILE, _facts)

# ── DM Daily Quota ────────────────────────────────────────────────────────────

DM_DAILY_LIMIT = 15   # synced with config.DM_DAILY_LIMIT

def check_dm_quota(user_id: int) -> tuple[bool, int]:
    """
    Returns (allowed, remaining).
    allowed=True if user still has messages left today.
    """
    today = datetime.date.today().isoformat()
    key   = str(user_id)
    entry = _dm_quota.get(key, {})
    if entry.get("date") != today:
        return True, DM_DAILY_LIMIT          # fresh day
    used      = entry.get("count", 0)
    remaining = DM_DAILY_LIMIT - used
    return remaining > 0, max(0, remaining)

def use_dm_quota(user_id: int) -> int:
    """Consume one DM message and persist. Returns remaining count for today."""
    today = datetime.date.today().isoformat()
    key   = str(user_id)
    entry = _dm_quota.get(key, {})
    if entry.get("date") != today:
        _dm_quota[key] = {"date": today, "count": 1}
    else:
        _dm_quota[key]["count"] = entry.get("count", 0) + 1
    _save(DM_QUOTA_FILE, _dm_quota)
    used      = _dm_quota[key]["count"]
    remaining = DM_DAILY_LIMIT - used
    return max(0, remaining)

def get_dm_quota_remaining(user_id: int) -> int:
    _, remaining = check_dm_quota(user_id)
    return remaining
