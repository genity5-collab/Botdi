"""
Lightweight JSON-backed persistent store.
Covers: strikes, tickets, conversation memory.
"""

from __future__ import annotations

import asyncio
import json
import random
import string
from collections import deque
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

STRIKES_FILE  = DATA_DIR / "strikes.json"
TICKETS_FILE  = DATA_DIR / "tickets.json"
MEMORIES_FILE = DATA_DIR / "memories.json"

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
_strikes:  dict[str, int]          = _load(STRIKES_FILE)
_tickets:  dict[str, dict[str, Any]] = _load(TICKETS_FILE)
_memories: dict[str, list[dict]]   = _load(MEMORIES_FILE)  # user_id → [{role, content}]


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
        _tickets[tid] = {"user_id": user_id, "category": category, "status": "open"}
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
        _save(TICKETS_FILE, _tickets)
        return True


async def get_user_open_ticket(user_id: int) -> str | None:
    async with _lock:
        for tid, t in _tickets.items():
            if t["user_id"] == user_id and t["status"] == "open":
                return tid
        return None


# ── Conversation Memory ───────────────────────────────────────────────────────

MAX_EXCHANGES = 8   # pairs = 16 messages max per user

def get_memory(user_id: int) -> list[dict]:
    """Return the stored message history for a user (as OpenAI message dicts)."""
    return list(_memories.get(str(user_id), []))


def add_memory(user_id: int, role: str, content: str) -> None:
    """Append a message to a user's history; trim to MAX_EXCHANGES * 2 messages."""
    key = str(user_id)
    hist = _memories.get(key, [])
    hist.append({"role": role, "content": content[:600]})
    # Keep only the last MAX_EXCHANGES * 2 messages
    if len(hist) > MAX_EXCHANGES * 2:
        hist = hist[-(MAX_EXCHANGES * 2):]
    _memories[key] = hist


async def save_memory() -> None:
    """Persist the memory dict to disk (call after adding entries)."""
    async with _lock:
        _save(MEMORIES_FILE, _memories)


async def clear_memory(user_id: int) -> None:
    """Wipe a user's conversation history."""
    async with _lock:
        _memories.pop(str(user_id), None)
        _save(MEMORIES_FILE, _memories)
