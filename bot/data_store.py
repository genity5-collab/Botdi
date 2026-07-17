"""
Lightweight JSON-backed persistent store.
Covers: strikes, tickets, conversation memory, DM daily quotas, taught facts, enforced rules.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import random
import string
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

STRIKES_FILE  = DATA_DIR / "strikes.json"
TICKETS_FILE  = DATA_DIR / "tickets.json"
MEMORIES_FILE = DATA_DIR / "memories.json"
DM_QUOTA_FILE = DATA_DIR / "dm_quota.json"
TAUGHT_FILE   = DATA_DIR / "taught.json"
RULES_FILE    = DATA_DIR / "rules.json"

_lock = asyncio.Lock()

def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

_strikes  : dict[str, int]            = _load(STRIKES_FILE)
_tickets  : dict[str, dict[str, Any]] = _load(TICKETS_FILE)
_memories : dict[str, list[dict]]     = _load(MEMORIES_FILE)
_dm_quota : dict[str, dict]           = _load(DM_QUOTA_FILE)
_taught   : dict[str, list[dict]]     = _load(TAUGHT_FILE)
_rules    : dict[str, list[dict]]     = _load(RULES_FILE)

# ── Strikes ─────────────────────────────────────────────────────────────

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

# ── Tickets ─────────────────────────────────────────────────────────────

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

MAX_EXCHANGES = 100

def _summarize_old_exchanges(messages: list[dict], keep_recent: int = 25) -> list[dict]:
    if len(messages) <= keep_recent * 2:
        return messages
    recent = messages[-(keep_recent * 2):]
    old = messages[:-(keep_recent * 2)]
    topics: list[str] = []
    for msg in old:
        content = (msg.get("content") or "").strip()
        if len(content) > 5:
            topics.append(content[:100])
    if not topics:
        return recent
    summary_text = " | ".join(topics[-20:])
    return [{"role": "system", "content": f"[Earlier conversation summary: {summary_text}]"}, *recent]


def get_memory(user_id: int) -> list[dict]:
    return _summarize_old_exchanges(list(_memories.get(str(user_id), [])))


def add_memory(user_id: int, role: str, content: str) -> None:
    key = str(user_id)
    hist = _memories.get(key, [])
    hist.append({"role": role, "content": content[:1500]})
    if len(hist) > MAX_EXCHANGES * 2:
        hist = hist[-(MAX_EXCHANGES * 2):]
    _memories[key] = hist


async def save_memory() -> None:
    async with _lock:
        _save(MEMORIES_FILE, _memories)


async def clear_memory(user_id: int) -> None:
    async with _lock:
        _memories.pop(str(user_id), None)
        _save(MEMORIES_FILE, _memories)

# ── DM Daily Quota ─────────────────────────────────────────────────────────

DM_DAILY_LIMIT = 15

def check_dm_quota(user_id: int) -> tuple[bool, int]:
    today = datetime.date.today().isoformat()
    key = str(user_id)
    entry = _dm_quota.get(key, {})
    if entry.get("date") != today:
        return True, DM_DAILY_LIMIT
    used = entry.get("count", 0)
    remaining = DM_DAILY_LIMIT - used
    return remaining > 0, max(0, remaining)


def use_dm_quota(user_id: int) -> int:
    today = datetime.date.today().isoformat()
    key = str(user_id)
    entry = _dm_quota.get(key, {})
    if entry.get("date") != today:
        _dm_quota[key] = {"date": today, "count": 1}
    else:
        _dm_quota[key]["count"] = entry.get("count", 0) + 1
    _save(DM_QUOTA_FILE, _dm_quota)
    return max(0, DM_DAILY_LIMIT - _dm_quota[key]["count"])


def get_dm_quota_remaining(user_id: int) -> int:
    _, remaining = check_dm_quota(user_id)
    return remaining

# ── Taught server facts (from /teach) ────────────────────────────────────────

MAX_TAUGHT_PER_GUILD = 100

def get_taught(guild_id: int) -> str:
    facts = _taught.get(str(guild_id), [])
    if not facts:
        return ""
    lines = []
    for i, f in enumerate(facts[-MAX_TAUGHT_PER_GUILD:], start=1):
        lines.append(f"{i}. {f.get('fact', '')}")
    return "\n".join(lines)


async def add_taught(guild_id: int, fact: str, taught_by: int) -> None:
    if not fact:
        return
    async with _lock:
        key = str(guild_id)
        arr = _taught.get(key, [])
        arr.append({"fact": fact[:800], "by": taught_by, "ts": int(time.time())})
        if len(arr) > MAX_TAUGHT_PER_GUILD:
            arr = arr[-MAX_TAUGHT_PER_GUILD:]
        _taught[key] = arr
        _save(TAUGHT_FILE, _taught)


async def clear_taught(guild_id: int) -> None:
    async with _lock:
        _taught.pop(str(guild_id), None)
        _save(TAUGHT_FILE, _taught)

# ── Enforced rules (from /rule) ────────────────────────────────────────────────

MAX_RULES = 20

def get_rules(guild_id: int) -> list[dict]:
    return list(_rules.get(str(guild_id), []))


def get_rules_text(guild_id: int) -> str:
    rules = get_rules(guild_id)
    if not rules:
        return ""
    lines = []
    for i, r in enumerate(rules, start=1):
        lines.append(f"{i}. {r.get('rule', '')}")
    return "\n".join(lines)


async def add_rule(guild_id: int, rule: str, set_by: int) -> int:
    if not rule.strip():
        return 0
    async with _lock:
        key = str(guild_id)
        arr = _rules.get(key, [])
        arr.append({"rule": rule[:500], "by": set_by, "ts": int(time.time())})
        if len(arr) > MAX_RULES:
            arr = arr[-MAX_RULES:]
        _rules[key] = arr
        _save(RULES_FILE, _rules)
        return len(arr)


async def remove_rule(guild_id: int, index: int) -> bool:
    async with _lock:
        key = str(guild_id)
        arr = _rules.get(key, [])
        if index < 0 or index >= len(arr):
            return False
        arr.pop(index)
        _rules[key] = arr
        _save(RULES_FILE, _rules)
        return True


async def clear_rules(guild_id: int) -> None:
    async with _lock:
        _rules.pop(str(guild_id), None)
        _save(RULES_FILE, _rules)
