"""
Lightweight JSON-backed persistent store.
Covers: strikes, tickets, conversation memory, rate limiting, taught facts, enforced rules.
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

STRIKES_FILE    = DATA_DIR / "strikes.json"
TICKETS_FILE    = DATA_DIR / "tickets.json"
MEMORIES_FILE   = DATA_DIR / "memories.json"
RATE_LIMIT_FILE = DATA_DIR / "rate_limits.json"
TAUGHT_FILE     = DATA_DIR / "taught.json"
RULES_FILE      = DATA_DIR / "rules.json"
SYSTEMS_FILE    = DATA_DIR / "systems.json"
ACTION_HISTORY_FILE = DATA_DIR / "action_history.json"
SCHEDULED_FILE  = DATA_DIR / "scheduled_actions.json"

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

_strikes     : dict[str, int]            = _load(STRIKES_FILE)
_tickets     : dict[str, dict[str, Any]] = _load(TICKETS_FILE)
_memories    : dict[str, list[dict]]     = _load(MEMORIES_FILE)
_rate_limits : dict[str, dict]           = _load(RATE_LIMIT_FILE)
_taught      : dict[str, list[dict]]     = _load(TAUGHT_FILE)
_rules       : dict[str, list[dict]]     = _load(RULES_FILE)

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

# ── Rate limiting ────────────────────────────────────────────────────────────
# Server: 5 msgs per hour (owner = infinite)
# DM: 15 msgs per 3-day cycle, degrading: day1=15, day2=10, day3=5, then 0
# Subagent: 5 per week for guild owners, bot owner infinite

def _now_ts() -> int:
    return int(time.time())


def check_server_rate_limit(user_id: int, *, limit: int = 5, window: int = 3600, owner_id: int = 0) -> tuple[bool, int, int]:
    """Returns (allowed, remaining, retry_after_seconds)."""
    if owner_id and user_id == owner_id:
        return True, -1, 0
    key = str(user_id)
    now = _now_ts()
    entry = _rate_limits.get(key, {})
    server = entry.get("server", {})
    timestamps: list = server.get("timestamps", [])
    cutoff = now - window
    timestamps = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= limit:
        retry_after = timestamps[0] + window - now
        _rate_limits[key] = {**entry, "server": {"timestamps": timestamps}}
        _save(RATE_LIMIT_FILE, _rate_limits)
        return False, 0, max(retry_after, 1)
    timestamps.append(now)
    _rate_limits[key] = {**entry, "server": {"timestamps": timestamps}}
    _save(RATE_LIMIT_FILE, _rate_limits)
    return True, limit - len(timestamps), 0


def check_dm_rate_limit(user_id: int, *, cycle: int = 259200, day1: int = 15, day2: int = 10, day3: int = 5, owner_id: int = 0) -> tuple[bool, int, int]:
    """Returns (allowed, remaining, retry_after_seconds)."""
    if owner_id and user_id == owner_id:
        return True, -1, 0
    key = str(user_id)
    now = _now_ts()
    entry = _rate_limits.get(key, {})
    dm = entry.get("dm", {})
    cycle_start = dm.get("cycle_start", 0)
    used = dm.get("used", 0)

    if cycle_start == 0 or (now - cycle_start) >= cycle:
        cycle_start = now
        used = 0

    elapsed = now - cycle_start
    day_num = elapsed // 86400

    if day_num >= 3:
        retry_after = cycle_start + cycle - now
        _rate_limits[key] = {**entry, "dm": {"cycle_start": cycle_start, "used": used}}
        _save(RATE_LIMIT_FILE, _rate_limits)
        return False, 0, max(retry_after, 1)

    daily_limits = [day1, day2, day3]
    daily_limit = daily_limits[day_num]
    day_start = cycle_start + (day_num * 86400)
    day_end = day_start + 86400
    day_used = dm.get(f"day{day_num}_used", 0)

    if day_used >= daily_limit:
        retry_after = day_end - now
        _rate_limits[key] = {**entry, "dm": {**dm, "cycle_start": cycle_start, "used": used}}
        _save(RATE_LIMIT_FILE, _rate_limits)
        return False, 0, max(retry_after, 1)

    dm[f"day{day_num}_used"] = day_used + 1
    used += 1
    _rate_limits[key] = {**entry, "dm": {**dm, "cycle_start": cycle_start, "used": used}}
    _save(RATE_LIMIT_FILE, _rate_limits)
    remaining = daily_limit - day_used - 1
    return True, remaining, 0


def check_subagent_rate_limit(user_id: int, *, limit: int = 5, window: int = 604800, owner_id: int = 0) -> tuple[bool, int, int]:
    """Returns (allowed, remaining, retry_after_seconds). Bot owner = infinite."""
    if owner_id and user_id == owner_id:
        return True, -1, 0
    key = str(user_id)
    now = _now_ts()
    entry = _rate_limits.get(key, {})
    sub = entry.get("subagent", {})
    timestamps: list = sub.get("timestamps", [])
    cutoff = now - window
    timestamps = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= limit:
        retry_after = timestamps[0] + window - now
        _rate_limits[key] = {**entry, "subagent": {"timestamps": timestamps}}
        _save(RATE_LIMIT_FILE, _rate_limits)
        return False, 0, max(retry_after, 1)
    timestamps.append(now)
    _rate_limits[key] = {**entry, "subagent": {"timestamps": timestamps}}
    _save(RATE_LIMIT_FILE, _rate_limits)
    return True, limit - len(timestamps), 0


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


# ── Systems data (panels, autorole, welcome, suggestions, applications,
#      giveaways, verification, backups, snapshots, action history,
#      scheduled actions, automation, always-allow-deletes) ────────────────────

_systems: dict[str, dict] = _load(SYSTEMS_FILE)
_action_history: dict[str, list[dict]] = _load(ACTION_HISTORY_FILE)
_scheduled: dict[str, list[dict]] = _load(SCHEDULED_FILE)


def _guild_systems(guild_id: int) -> dict:
    key = str(guild_id)
    if key not in _systems:
        _systems[key] = {
            "ticket_panels": {},
            "autorole": {"enabled": False, "roles": []},
            "welcome": {"enabled": False, "channel_id": None, "message": "", "goodbye_channel_id": None, "goodbye_message": ""},
            "suggestions": {"channel_id": None},
            "applications": {},
            "giveaways": {},
            "verification": {"enabled": False, "role_id": None, "channel_id": None, "message": ""},
            "always_allow_deletes": False,
        }
    return _systems[key]


async def save_systems() -> None:
    async with _lock:
        _save(SYSTEMS_FILE, _systems)


async def get_guild_systems(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)


async def set_always_allow_deletes(guild_id: int, value: bool) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        g["always_allow_deletes"] = value
        await save_systems()
        return value


async def get_always_allow_deletes(guild_id: int) -> bool:
    async with _lock:
        return _guild_systems(guild_id).get("always_allow_deletes", False)


# ── Ticket panels ────────────────────────────────────────────────────────────

async def create_ticket_panel(guild_id: int, panel_id: str, channel_id: int, title: str, description: str, categories: list[str]) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        panel = {
            "channel_id": channel_id, "title": title, "description": description,
            "categories": categories, "message_id": None, "tickets": {},
        }
        g["ticket_panels"][panel_id] = panel
        await save_systems()
        return panel


async def get_ticket_panel(guild_id: int, panel_id: str) -> dict | None:
    async with _lock:
        return _guild_systems(guild_id)["ticket_panels"].get(panel_id)


async def list_ticket_panels(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)["ticket_panels"]


async def delete_ticket_panel(guild_id: int, panel_id: str) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        if panel_id in g["ticket_panels"]:
            del g["ticket_panels"][panel_id]
            await save_systems()
            return True
        return False


async def set_panel_message_id(guild_id: int, panel_id: str, message_id: int) -> None:
    async with _lock:
        g = _guild_systems(guild_id)
        if panel_id in g["ticket_panels"]:
            g["ticket_panels"][panel_id]["message_id"] = message_id
            await save_systems()


async def add_panel_ticket(guild_id: int, panel_id: str, ticket_id: str, user_id: int, channel_id: int, category: str) -> None:
    async with _lock:
        g = _guild_systems(guild_id)
        if panel_id in g["ticket_panels"]:
            g["ticket_panels"][panel_id]["tickets"][ticket_id] = {
                "user_id": user_id, "channel_id": channel_id, "category": category, "status": "open", "created_at": int(time.time()),
            }
            await save_systems()


async def close_panel_ticket(guild_id: int, panel_id: str, ticket_id: str) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        panel = g["ticket_panels"].get(panel_id)
        if panel and ticket_id in panel["tickets"]:
            panel["tickets"][ticket_id]["status"] = "closed"
            await save_systems()
            return True
        return False


# ── Autorole ──────────────────────────────────────────────────────────────────

async def set_autorole(guild_id: int, enabled: bool, roles: list[int]) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        g["autorole"] = {"enabled": enabled, "roles": roles}
        await save_systems()
        return g["autorole"]


async def get_autorole(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)["autorole"]


# ── Welcome / goodbye ─────────────────────────────────────────────────────────

async def set_welcome(guild_id: int, enabled: bool, channel_id: int | None, message: str, goodbye_channel_id: int | None = None, goodbye_message: str = "") -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        g["welcome"] = {
            "enabled": enabled, "channel_id": channel_id, "message": message,
            "goodbye_channel_id": goodbye_channel_id, "goodbye_message": goodbye_message,
        }
        await save_systems()
        return g["welcome"]


async def get_welcome(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)["welcome"]


# ── Suggestions ───────────────────────────────────────────────────────────────

async def set_suggestion_channel(guild_id: int, channel_id: int) -> None:
    async with _lock:
        g = _guild_systems(guild_id)
        g["suggestions"] = {"channel_id": channel_id}
        await save_systems()


async def get_suggestions(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)["suggestions"]


# ── Applications / forms ─────────────────────────────────────────────────────

async def create_application(guild_id: int, app_id: str, name: str, description: str, questions: list[str], channel_id: int) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        app = {
            "name": name, "description": description, "questions": questions,
            "channel_id": channel_id, "message_id": None, "submissions": {},
        }
        g["applications"][app_id] = app
        await save_systems()
        return app


async def get_application(guild_id: int, app_id: str) -> dict | None:
    async with _lock:
        return _guild_systems(guild_id)["applications"].get(app_id)


async def list_applications(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)["applications"]


async def delete_application(guild_id: int, app_id: str) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        if app_id in g["applications"]:
            del g["applications"][app_id]
            await save_systems()
            return True
        return False


async def set_application_message_id(guild_id: int, app_id: str, message_id: int) -> None:
    async with _lock:
        g = _guild_systems(guild_id)
        if app_id in g["applications"]:
            g["applications"][app_id]["message_id"] = message_id
            await save_systems()


async def add_application_submission(guild_id: int, app_id: str, submission_id: str, user_id: int, answers: list[str]) -> None:
    async with _lock:
        g = _guild_systems(guild_id)
        if app_id in g["applications"]:
            g["applications"][app_id]["submissions"][submission_id] = {
                "user_id": user_id, "answers": answers, "status": "pending", "ts": int(time.time()),
            }
            await save_systems()


async def update_application_submission_status(guild_id: int, app_id: str, submission_id: str, status: str) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        app = g["applications"].get(app_id)
        if app and submission_id in app["submissions"]:
            app["submissions"][submission_id]["status"] = status
            await save_systems()
            return True
        return False


# ── Giveaways ─────────────────────────────────────────────────────────────────

async def create_giveaway(guild_id: int, giveaway_id: str, channel_id: int, title: str, description: str, prize: str, duration_minutes: int, winners: int) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        end_ts = int(time.time()) + duration_minutes * 60
        gw = {
            "channel_id": channel_id, "title": title, "description": description,
            "prize": prize, "end_ts": end_ts, "winners": winners,
            "message_id": None, "participants": [], "ended": False,
        }
        g["giveaways"][giveaway_id] = gw
        await save_systems()
        return gw


async def get_giveaway(guild_id: int, giveaway_id: str) -> dict | None:
    async with _lock:
        return _guild_systems(guild_id)["giveaways"].get(giveaway_id)


async def list_giveaways(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)["giveaways"]


async def set_giveaway_message_id(guild_id: int, giveaway_id: str, message_id: int) -> None:
    async with _lock:
        g = _guild_systems(guild_id)
        if giveaway_id in g["giveaways"]:
            g["giveaways"][giveaway_id]["message_id"] = message_id
            await save_systems()


async def add_giveaway_participant(guild_id: int, giveaway_id: str, user_id: int) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        gw = g["giveaways"].get(giveaway_id)
        if not gw or gw["ended"]:
            return False
        if user_id not in gw["participants"]:
            gw["participants"].append(user_id)
            await save_systems()
        return True


async def end_giveaway(guild_id: int, giveaway_id: str) -> dict | None:
    async with _lock:
        g = _guild_systems(guild_id)
        gw = g["giveaways"].get(giveaway_id)
        if not gw:
            return None
        gw["ended"] = True
        await save_systems()
        return gw


async def delete_giveaway(guild_id: int, giveaway_id: str) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        if giveaway_id in g["giveaways"]:
            del g["giveaways"][giveaway_id]
            await save_systems()
            return True
        return False


# ── Verification ───────────────────────────────────────────────────────────────

async def set_verification(guild_id: int, enabled: bool, role_id: int | None, channel_id: int | None, message: str) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        g["verification"] = {"enabled": enabled, "role_id": role_id, "channel_id": channel_id, "message": message}
        await save_systems()
        return g["verification"]


async def get_verification(guild_id: int) -> dict:
    async with _lock:
        return _guild_systems(guild_id)["verification"]


# ── Server snapshots ───────────────────────────────────────────────────────────

async def save_snapshot(guild_id: int, snapshot_id: str, name: str, data: dict) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        if "snapshots" not in g:
            g["snapshots"] = {}
        snap = {"id": snapshot_id, "name": name, "ts": int(time.time()), "data": data}
        g["snapshots"][snapshot_id] = snap
        await save_systems()
        return snap


async def get_snapshot(guild_id: int, snapshot_id: str) -> dict | None:
    async with _lock:
        g = _guild_systems(guild_id)
        return g.get("snapshots", {}).get(snapshot_id)


async def list_snapshots(guild_id: int) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        return g.get("snapshots", {})


async def delete_snapshot(guild_id: int, snapshot_id: str) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        snaps = g.get("snapshots", {})
        if snapshot_id in snaps:
            del snaps[snapshot_id]
            await save_systems()
            return True
        return False


# ── Action history ────────────────────────────────────────────────────────────

MAX_ACTION_HISTORY = 500


async def add_action_history(guild_id: int, action: str, detail: str, undo_data: dict | None = None) -> None:
    async with _lock:
        key = str(guild_id)
        arr = _action_history.setdefault(key, [])
        entry = {
            "ts": int(time.time()),
            "action": action,
            "detail": detail[:500],
            "undo_data": undo_data,
        }
        arr.append(entry)
        if len(arr) > MAX_ACTION_HISTORY:
            arr = arr[-MAX_ACTION_HISTORY:]
        _save(ACTION_HISTORY_FILE, _action_history)


async def get_action_history(guild_id: int, limit: int = 25) -> list[dict]:
    async with _lock:
        arr = _action_history.get(str(guild_id), [])
        return list(arr[-limit:])


async def get_last_action(guild_id: int) -> dict | None:
    async with _lock:
        arr = _action_history.get(str(guild_id), [])
        return arr[-1] if arr else None


# ── Scheduled actions ─────────────────────────────────────────────────────────

async def add_scheduled_action(guild_id: int, sched_id: str, channel_id: int, content: str, cron_day: str, hour: int, minute: int) -> dict:
    async with _lock:
        key = str(guild_id)
        arr = _scheduled.setdefault(key, [])
        entry = {
            "id": sched_id, "channel_id": channel_id, "content": content[:2000],
            "day": cron_day, "hour": hour, "minute": minute, "last_run": 0, "enabled": True,
        }
        arr.append(entry)
        _save(SCHEDULED_FILE, _scheduled)
        return entry


async def list_scheduled_actions(guild_id: int) -> list[dict]:
    async with _lock:
        return list(_scheduled.get(str(guild_id), []))


async def remove_scheduled_action(guild_id: int, sched_id: str) -> bool:
    async with _lock:
        key = str(guild_id)
        arr = _scheduled.get(key, [])
        for i, e in enumerate(arr):
            if e["id"] == sched_id:
                arr.pop(i)
                _save(SCHEDULED_FILE, _scheduled)
                return True
        return False


async def update_scheduled_last_run(guild_id: int, sched_id: str, ts: int) -> None:
    async with _lock:
        key = str(guild_id)
        for e in _scheduled.get(key, []):
            if e["id"] == sched_id:
                e["last_run"] = ts
                _save(SCHEDULED_FILE, _scheduled)
                return


# ── Automation triggers ───────────────────────────────────────────────────────

async def set_automation(guild_id: int, trigger: str, actions: dict) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        if "automation" not in g:
            g["automation"] = {}
        g["automation"][trigger] = actions
        await save_systems()
        return g["automation"][trigger]


async def get_automation(guild_id: int, trigger: str) -> dict | None:
    async with _lock:
        g = _guild_systems(guild_id)
        return g.get("automation", {}).get(trigger)


async def list_automation(guild_id: int) -> dict:
    async with _lock:
        g = _guild_systems(guild_id)
        return g.get("automation", {})


async def remove_automation(guild_id: int, trigger: str) -> bool:
    async with _lock:
        g = _guild_systems(guild_id)
        if "automation" in g and trigger in g["automation"]:
            del g["automation"][trigger]
            await save_systems()
            return True
        return False
