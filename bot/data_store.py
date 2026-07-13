"""
Lightweight JSON-backed persistent store.
All access goes through module-level async helpers so the in-memory
dicts stay in sync with the on-disk files.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import string
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

STRIKES_FILE = DATA_DIR / "strikes.json"
TICKETS_FILE = DATA_DIR / "tickets.json"

_lock = asyncio.Lock()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


# In-memory caches
_strikes: dict[str, int] = _load(STRIKES_FILE)
_tickets: dict[str, dict[str, Any]] = _load(TICKETS_FILE)


# ── Strikes ───────────────────────────────────────────────────────────────────

async def get_strikes(user_id: int) -> int:
    async with _lock:
        return _strikes.get(str(user_id), 0)


async def add_strike(user_id: int) -> int:
    """Increment strike count and persist. Returns new total."""
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

def _generate_ticket_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


async def create_ticket(user_id: int, category: str) -> str:
    """Create a new ticket and return its ID."""
    async with _lock:
        tid = _generate_ticket_id()
        while tid in _tickets:
            tid = _generate_ticket_id()
        _tickets[tid] = {
            "user_id": user_id,
            "category": category,
            "status": "open",
        }
        _save(TICKETS_FILE, _tickets)
        return tid


async def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    async with _lock:
        return _tickets.get(ticket_id.upper())


async def close_ticket(ticket_id: str) -> bool:
    """Mark ticket as closed. Returns False if not found."""
    async with _lock:
        tid = ticket_id.upper()
        if tid not in _tickets:
            return False
        _tickets[tid]["status"] = "closed"
        _save(TICKETS_FILE, _tickets)
        return True


async def get_user_open_ticket(user_id: int) -> str | None:
    """Return the open ticket ID for a user, or None."""
    async with _lock:
        for tid, t in _tickets.items():
            if t["user_id"] == user_id and t["status"] == "open":
                return tid
        return None
