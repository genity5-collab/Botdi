"""
Supabase-backed persistent store for the /site App Engineering feature.
Covers: projects, edit logs, monthly usage, user Gemini keys.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import json
import logging
import os
from typing import Any

import aiohttp

from config import (
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
    SITE_FREE_MONTHLY_LIMIT,
    SITE_KEY_ENCRYPTION_SALT,
)

log = logging.getLogger(__name__)

_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
}


def _table(name: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{name}"


# ── XOR encryption for user keys ──────────────────────────────────────────────

def _xor_key(plaintext: str) -> str:
    """XOR-encrypt a string using a server-side salt. Returns base64."""
    key_stream = (SITE_KEY_ENCRYPTION_SALT * (len(plaintext) // len(SITE_KEY_ENCRYPTION_SALT) + 1))[:len(plaintext)]
    raw = bytes(a ^ ord(b) for a, b in zip(plaintext.encode(), key_stream.encode()))
    return base64.b64encode(raw).decode()


def _xor_decrypt(ciphertext_b64: str) -> str:
    """Decrypt an XOR-encrypted base64 string."""
    raw = base64.b64decode(ciphertext_b64)
    key_stream = (SITE_KEY_ENCRYPTION_SALT * (len(raw) // len(SITE_KEY_ENCRYPTION_SALT) + 1))[:len(raw)]
    return bytes(a ^ b for a, b in zip(raw, key_stream.encode())).decode()


# ── Projects ──────────────────────────────────────────────────────────────────

async def create_project(
    owner_id: int,
    owner_name: str,
    prompt: str,
    project_name: str,
    files: dict[str, str],
    dependencies: list[str] | None = None,
    external_requests: bool = False,
) -> str | None:
    """Create a new project row. Returns the project UUID or None on failure."""
    payload = {
        "owner_id":         owner_id,
        "owner_name":        owner_name,
        "prompt":            prompt[:500],
        "project_name":      project_name[:100],
        "files":             files,
        "dependencies":      dependencies or [],
        "external_requests": external_requests,
        "build_status":      "pending",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{_table('site_projects')}?select=id",
                headers=_HEADERS, json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    return data[0]["id"] if data else None
                log.warning("create_project HTTP %s", resp.status)
    except Exception as exc:
        log.warning("create_project: %s", exc)
    return None


async def get_project(project_id: str) -> dict[str, Any] | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_table('site_projects')}?id=eq.{project_id}&limit=1",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data[0] if data else None
    except Exception as exc:
        log.warning("get_project: %s", exc)
    return None


async def get_user_projects(owner_id: int) -> list[dict[str, Any]]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_table('site_projects')}?owner_id=eq.{owner_id}&order=updated_at.desc",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as exc:
        log.warning("get_user_projects: %s", exc)
    return []


async def get_latest_user_project(owner_id: int) -> dict[str, Any] | None:
    """Return the most recently updated project for a user (for edit continuity)."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_table('site_projects')}?owner_id=eq.{owner_id}&order=updated_at.desc&limit=1",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data[0] if data else None
    except Exception as exc:
        log.warning("get_latest_user_project: %s", exc)
    return None


async def update_project(project_id: str, updates: dict[str, Any]) -> bool:
    updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.patch(
                f"{_table('site_projects')}?id=eq.{project_id}",
                headers=_HEADERS, json=updates,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 204)
    except Exception as exc:
        log.warning("update_project: %s", exc)
    return False


async def delete_project(project_id: str) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.delete(
                f"{_table('site_projects')}?id=eq.{project_id}",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 204)
    except Exception as exc:
        log.warning("delete_project: %s", exc)
    return False


# ── Edit logs ─────────────────────────────────────────────────────────────────

async def add_edit_log(
    project_id: str,
    description: str,
    files_affected: list[str],
    build_status: str = "",
    debug_status: str = "none",
    prompt: str = "",
) -> None:
    payload = {
        "project_id":      project_id,
        "description":     description[:200],
        "files_affected":  files_affected,
        "build_status":    build_status,
        "debug_status":    debug_status,
        "prompt":          prompt[:300],
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                _table("site_edit_logs"),
                headers=_HEADERS, json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status not in (200, 201):
                    log.warning("add_edit_log HTTP %s", resp.status)
    except Exception as exc:
        log.warning("add_edit_log: %s", exc)


async def get_edit_logs(project_id: str) -> list[dict[str, Any]]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_table('site_edit_logs')}?project_id=eq.{project_id}&order=timestamp.asc",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as exc:
        log.warning("get_edit_logs: %s", exc)
    return []


# ── Monthly usage ─────────────────────────────────────────────────────────────

def _current_month() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


async def get_usage(user_id: int) -> dict[str, Any]:
    """Returns usage row for the current month, creating one if needed."""
    month = _current_month()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_table('site_usage')}?user_id=eq.{user_id}&limit=1",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        row = data[0]
                        # Reset count if new month
                        if row.get("month_key") != month:
                            await update_usage(user_id, {"month_key": month, "count": 0})
                            row["month_key"] = month
                            row["count"] = 0
                        return row
                    # Create new row
                    payload = {"user_id": user_id, "month_key": month, "count": 0}
                    async with s.post(
                        _table("site_usage"),
                        headers=_HEADERS, json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r2:
                        if r2.status in (200, 201):
                            d2 = await r2.json()
                            return d2[0] if d2 else payload
                        return payload
    except Exception as exc:
        log.warning("get_usage: %s", exc)
    return {"user_id": user_id, "month_key": month, "count": 0, "has_custom_key": False}


async def update_usage(user_id: int, updates: dict[str, Any]) -> bool:
    updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.patch(
                f"{_table('site_usage')}?user_id=eq.{user_id}",
                headers=_HEADERS, json=updates,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 204)
    except Exception as exc:
        log.warning("update_usage: %s", exc)
    return False


async def increment_usage(user_id: int) -> int:
    """Increment usage count for current month. Returns new count."""
    row = await get_usage(user_id)
    new_count = row.get("count", 0) + 1
    await update_usage(user_id, {"count": new_count, "month_key": _current_month()})
    return new_count


async def get_usage_remaining(user_id: int, has_custom_key: bool = False) -> int:
    """Returns remaining free messages, or -1 if unlimited (custom key)."""
    if has_custom_key:
        return -1
    row = await get_usage(user_id)
    used = row.get("count", 0)
    return max(0, SITE_FREE_MONTHLY_LIMIT - used)


async def check_usage(user_id: int, has_custom_key: bool = False) -> tuple[bool, int]:
    """Returns (allowed, remaining). remaining=-1 means unlimited."""
    if has_custom_key:
        return True, -1
    row = await get_usage(user_id)
    used = row.get("count", 0)
    remaining = max(0, SITE_FREE_MONTHLY_LIMIT - used)
    return remaining > 0, remaining


# ── User Gemini keys ──────────────────────────────────────────────────────────

async def set_user_key(user_id: int, api_key: str) -> bool:
    encrypted = _xor_key(api_key.strip())
    payload = {
        "user_id":       user_id,
        "encrypted_key": encrypted,
    }
    try:
        async with aiohttp.ClientSession() as s:
            # Try upsert via POST with prefer resolution
            headers = {**_HEADERS, "Prefer": "resolution=merge-duplicates"}
            async with s.post(
                _table("site_user_keys"),
                headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 201)
    except Exception as exc:
        log.warning("set_user_key: %s", exc)
    return False


async def get_user_key(user_id: int) -> str | None:
    """Returns the decrypted Gemini API key, or None if user hasn't set one."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_table('site_user_keys')}?user_id=eq.{user_id}&limit=1",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        return _xor_decrypt(data[0]["encrypted_key"])
    except Exception as exc:
        log.warning("get_user_key: %s", exc)
    return None


async def remove_user_key(user_id: int) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.delete(
                f"{_table('site_user_keys')}?user_id=eq.{user_id}",
                headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 204)
    except Exception as exc:
        log.warning("remove_user_key: %s", exc)
    return False
