"""
Persistent store for App Engineering projects and usage tracking.
"""
from __future__ import annotations
import asyncio
import datetime
import json
import secrets
from pathlib import Path
from typing import Any

from config import SITE_FREE_MONTHLY_LIMIT

DATA_DIR = Path(__file__).parent / "data" / "site"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_FILE = DATA_DIR / "projects.json"
USAGE_FILE = DATA_DIR / "usage.json"
USER_KEYS_FILE = DATA_DIR / "user_keys.json"
PROJECT_FILES_DIR = DATA_DIR / "projects_storage"
PROJECT_FILES_DIR.mkdir(exist_ok=True)
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


_projects: dict[str, dict[str, Any]] = _load(PROJECTS_FILE)
_usage: dict[str, dict] = _load(USAGE_FILE)
_user_keys: dict[str, str] = _load(USER_KEYS_FILE)


def _gen_project_id() -> str:
    return secrets.token_hex(8)


def _project_dir(project_id: str) -> Path:
    d = PROJECT_FILES_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


async def create_project(user_id: int, prompt: str) -> dict[str, Any]:
    async with _lock:
        pid = _gen_project_id()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        project = {"id": pid, "owner_id": user_id, "prompt": prompt[:500], "files": {}, "edit_log": [{"timestamp": now, "description": "Project created", "files": [], "build_status": "pending", "debug_status": None}], "checkpoints": [], "build_status": "pending", "preview_url": None, "screenshot_path": None, "created_at": now, "last_modified": now}
        _projects[pid] = project
        _save(PROJECTS_FILE, _projects)
        _project_dir(pid)
        return project


async def get_project(project_id: str) -> dict[str, Any] | None:
    async with _lock:
        return _projects.get(project_id)


async def get_user_projects(user_id: int) -> list[dict[str, Any]]:
    async with _lock:
        return [p for p in _projects.values() if p["owner_id"] == user_id]


async def get_user_latest_project(user_id: int) -> dict[str, Any] | None:
    projects = await get_user_projects(user_id)
    return max(projects, key=lambda p: p["last_modified"], default=None)


async def update_project(project_id: str, updates: dict[str, Any]) -> bool:
    async with _lock:
        if project_id not in _projects:
            return False
        _projects[project_id].update(updates)
        _projects[project_id]["last_modified"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _save(PROJECTS_FILE, _projects)
        return True


async def add_edit_log_entry(project_id: str, description: str, files: list[str], build_status: str = "pending", debug_status: str | None = None) -> None:
    async with _lock:
        if project_id not in _projects:
            return
        entry = {"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "description": description[:200], "files": files[:20], "build_status": build_status, "debug_status": debug_status}
        _projects[project_id]["edit_log"].append(entry)
        _projects[project_id]["last_modified"] = entry["timestamp"]
        _save(PROJECTS_FILE, _projects)


async def save_checkpoint(project_id: str, label: str = "") -> None:
    async with _lock:
        if project_id not in _projects:
            return
        project = _projects[project_id]
        project["checkpoints"].append({"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "label": label or f"Checkpoint {len(project['checkpoints']) + 1}", "files_snapshot": dict(project["files"])})
        project["checkpoints"] = project["checkpoints"][-20:]
        _save(PROJECTS_FILE, _projects)


async def restore_checkpoint(project_id: str, checkpoint_index: int) -> bool:
    async with _lock:
        if project_id not in _projects:
            return False
        project = _projects[project_id]
        if checkpoint_index < 0 or checkpoint_index >= len(project["checkpoints"]):
            return False
        project["files"] = dict(project["checkpoints"][checkpoint_index]["files_snapshot"])
        _save(PROJECTS_FILE, _projects)
        return True


async def set_project_files(project_id: str, files: dict[str, str]) -> None:
    async with _lock:
        if project_id in _projects:
            _projects[project_id]["files"] = files
            _projects[project_id]["last_modified"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _save(PROJECTS_FILE, _projects)


async def delete_project(project_id: str) -> bool:
    async with _lock:
        if project_id not in _projects:
            return False
        del _projects[project_id]
        _save(PROJECTS_FILE, _projects)
        return True


def _current_month() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


async def check_site_usage(user_id: int) -> tuple[bool, int]:
    async with _lock:
        entry = _usage.get(str(user_id), {})
        if entry.get("month") != _current_month():
            return True, SITE_FREE_MONTHLY_LIMIT
        remaining = SITE_FREE_MONTHLY_LIMIT - entry.get("count", 0)
        return remaining > 0, max(0, remaining)


# Big project keywords — these cost 3 credits instead of 1
_BIG_PROJECT_KEYWORDS = [
    "dashboard", "social media", "e-commerce", "ecommerce", "store",
    "platform", "marketplace", "full stack", "fullstack", "multi-page",
    "multi page", "admin panel", "cms", "content management",
    "chat app", "messaging app", "real-time", "realtime",
    "booking system", "reservation", "inventory", "crm",
    "erp", "learning management", "lms", "forum",
    "multi-user", "authentication system", "user management",
    "api", "backend", "database", "multiple pages",
    "landing page with", "website with", "web app with",
    "game", "interactive", "calculator with",
    "kanban", "project management", "todo app with",
    "portfolio with", "blog with", "shop with",
]

# Small project keywords — always 1 credit
_SMALL_PROJECT_KEYWORDS = [
    "landing page", "simple", "basic", "minimal", "one page",
    "static", "portfolio", "resume", "coming soon",
    "countdown", "timer", "clock", "calculator",
    "quote", "joke", "fact", "weather widget",
    "color picker", "tip calculator", "bill splitter",
    "todo", "notes", "bookmark", "link tree",
    "qr code", "badge", "card", "button",
]


def detect_project_size(prompt: str) -> int:
    """Returns the credit cost: 1 for small, 3 for big projects."""
    prompt_lower = prompt.lower()
    # Check for big project keywords
    big_score = sum(1 for kw in _BIG_PROJECT_KEYWORDS if kw in prompt_lower)
    small_score = sum(1 for kw in _SMALL_PROJECT_KEYWORDS if kw in prompt_lower)
    # If explicitly big or more big keywords than small
    if big_score > 0 and big_score >= small_score:
        return 3
    # If mentions multiple features/pages, likely big
    feature_count = prompt_lower.count(" and ") + prompt_lower.count(" with ") + prompt_lower.count(" also ")
    if feature_count >= 3 and len(prompt) > 150:
        return 3
    return 1


async def consume_site_message(user_id: int, credits: int = 1) -> tuple[bool, int]:
    """Consume credits for a site generation. Big projects cost 3, small cost 1."""
    async with _lock:
        key = str(user_id)
        month = _current_month()
        entry = _usage.get(key, {})
        if entry.get("month") != month:
            entry = {"month": month, "count": 0}
        if entry.get("count", 0) + credits > SITE_FREE_MONTHLY_LIMIT:
            _usage[key] = entry
            return False, max(0, SITE_FREE_MONTHLY_LIMIT - entry.get("count", 0))
        entry["count"] += credits
        _usage[key] = entry
        _save(USAGE_FILE, _usage)
        return True, max(0, SITE_FREE_MONTHLY_LIMIT - entry["count"])


async def use_site_message(user_id: int) -> int:
    allowed, remaining = await consume_site_message(user_id)
    return remaining if allowed else 0


async def get_site_usage_remaining(user_id: int) -> int:
    _, remaining = await check_site_usage(user_id)
    return remaining


async def set_user_gemini_key(user_id: int, key: str) -> None:
    async with _lock:
        _user_keys[str(user_id)] = key
        _save(USER_KEYS_FILE, _user_keys)


async def get_user_gemini_key(user_id: int) -> str | None:
    async with _lock:
        return _user_keys.get(str(user_id))


async def remove_user_gemini_key(user_id: int) -> bool:
    async with _lock:
        key = str(user_id)
        if key not in _user_keys:
            return False
        del _user_keys[key]
        _save(USER_KEYS_FILE, _user_keys)
        return True
