"""
Persistent store for App Engineering projects and usage tracking.
"""
from __future__ import annotations
import asyncio
import datetime
import json
import os
import secrets
from pathlib import Path
from typing import Any

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
        project = {
            "id": pid,
            "owner_id": user_id,
            "prompt": prompt[:500],
            "files": {},
            "edit_log": [
                {
                    "timestamp": now,
                    "description": "Project created",
                    "files": [],
                    "build_status": "pending",
                    "debug_status": None,
                }
            ],
            "checkpoints": [],
            "build_status": "pending",
            "preview_url": None,
            "screenshot_path": None,
            "created_at": now,
            "last_modified": now,
        }
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
    async with _lock:
        user_projects = [p for p in _projects.values() if p["owner_id"] == user_id]
        if not user_projects:
            return None
        user_projects.sort(key=lambda p: p["last_modified"], reverse=True)
        return user_projects[0]


async def update_project(project_id: str, updates: dict[str, Any]) -> bool:
    async with _lock:
        if project_id not in _projects:
            return False
        _projects[project_id].update(updates)
        _projects[project_id]["last_modified"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        _save(PROJECTS_FILE, _projects)
        return True


async def add_edit_log_entry(
    project_id: str,
    description: str,
    files: list[str],
    build_status: str = "pending",
    debug_status: str | None = None,
) -> None:
    async with _lock:
        if project_id not in _projects:
            return
        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "description": description[:200],
            "files": files[:20],
            "build_status": build_status,
            "debug_status": debug_status,
        }
        _projects[project_id]["edit_log"].append(entry)
        _projects[project_id]["last_modified"] = entry["timestamp"]
        _save(PROJECTS_FILE, _projects)


async def save_checkpoint(project_id: str, label: str = "") -> None:
    async with _lock:
        if project_id not in _projects:
            return
        proj = _projects[project_id]
        checkpoint = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "label": label or f"Checkpoint {len(proj['checkpoints']) + 1}",
            "files_snapshot": dict(proj["files"]),
        }
        proj["checkpoints"].append(checkpoint)
        if len(proj["checkpoints"]) > 20:
            proj["checkpoints"] = proj["checkpoints"][-20:]
        _save(PROJECTS_FILE, _projects)


async def restore_checkpoint(project_id: str, checkpoint_index: int) -> bool:
    async with _lock:
        if project_id not in _projects:
            return False
        proj = _projects[project_id]
        if checkpoint_index < 0 or checkpoint_index >= len(proj["checkpoints"]):
            return False
        checkpoint = proj["checkpoints"][checkpoint_index]
        proj["files"] = dict(checkpoint["files_snapshot"])
        proj["last_modified"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _save(PROJECTS_FILE, _projects)
        return True


async def set_project_files(project_id: str, files: dict[str, str]) -> None:
    async with _lock:
        if project_id not in _projects:
            return
        _projects[project_id]["files"] = files
        _projects[project_id]["last_modified"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        _save(PROJECTS_FILE, _projects)


async def delete_project(project_id: str) -> bool:
    async with _lock:
        if project_id not in _projects:
            return False
        del _projects[project_id]
        _save(PROJECTS_FILE, _projects)
        d = PROJECT_FILES_DIR / project_id
        if d.exists():
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        return True


def _current_month() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


async def check_site_usage(user_id: int) -> tuple[bool, int]:
    async with _lock:
        key = str(user_id)
        month = _current_month()
        entry = _usage.get(key, {})
        if entry.get("month") != month:
            return True, 5
        used = entry.get("count", 0)
        remaining = 5 - used
        return remaining > 0, max(0, remaining)


async def use_site_message(user_id: int) -> int:
    async with _lock:
        key = str(user_id)
        month = _current_month()
        entry = _usage.get(key, {})
        if entry.get("month") != month:
            _usage[key] = {"month": month, "count": 1}
        else:
            _usage[key]["count"] = entry.get("count", 0) + 1
        _save(USAGE_FILE, _usage)
        remaining = 5 - _usage[key]["count"]
        return max(0, remaining)


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
        if key in _user_keys:
            del _user_keys[key]
            _save(USER_KEYS_FILE, _user_keys)
            return True
        return False
