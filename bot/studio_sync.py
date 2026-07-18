"""
Vyrion Studio Sync — bridges the Discord bot to the Supabase database
that powers the Studio Dashboard web app.

All writes are fire-and-forget (non-blocking) so bot latency is unaffected.
If Supabase is not configured, every function silently no-ops.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import os
import uuid
from typing import Any

log = logging.getLogger("vyrion.studio_sync")

_client = None
_enabled: bool | None = None

def _get_client():
    global _client, _enabled
    if _enabled is False:
        return None
    if _client is not None:
        return _client
    url = os.environ.get("SUPABASE_URL") or os.environ.get("VITE_SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("VITE_SUPABASE_ANON_KEY")
    if not url or not key:
        _enabled = False
        log.info("Studio sync disabled — no Supabase credentials")
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        _enabled = True
        log.info("Studio sync enabled — connected to Supabase")
        return _client
    except Exception as e:
        _enabled = False
        log.warning("Studio sync init failed: %s", e)
        return None


def _fire(coro):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        pass


# ── Profile sync ───────────────────────────────────────────────────────────────

def sync_profile(discord_user_id: int, username: str, global_name: str | None, avatar_url: str | None, auth_uid: str | None = None):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            data = {
                "id": auth_uid,
                "discord_user_id": str(discord_user_id),
                "discord_username": username,
                "discord_global_name": global_name,
                "discord_avatar_url": avatar_url,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            data = {k: v for k, v in data.items() if v is not None}
            client.table("profiles").upsert(data).execute()
        except Exception as e:
            log.debug("sync_profile error: %s", e)

    _fire(_do())


# ── Guild sync ─────────────────────────────────────────────────────────────────

def sync_guild(guild: Any, auth_uid: str | None = None):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            import discord
            icon_url = str(guild.icon.url) if guild.icon else None
            online = 0
            if hasattr(guild, "members") and guild.members:
                online = sum(1 for m in guild.members if m.status == discord.Status.online)
            data = {
                "discord_guild_id": str(guild.id),
                "name": guild.name,
                "icon_url": icon_url,
                "owner_discord_id": str(guild.owner_id) if guild.owner_id else None,
                "member_count": guild.member_count or 0,
                "online_count": online,
                "channel_count": len(guild.channels),
                "role_count": len(guild.roles),
                "bot_present": True,
                "bot_status": "online",
                "user_id": auth_uid,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if auth_uid is None:
                data.pop("user_id", None)
            client.table("guilds").upsert(data, on_conflict="discord_guild_id").execute()
        except Exception as e:
            log.debug("sync_guild error: %s", e)

    _fire(_do())


# ── AI conversation logging ────────────────────────────────────────────────────

def log_conversation(
    *,
    discord_user_id: int,
    guild_id: int | None,
    channel_id: int | None,
    user_message: str,
    ai_response: str | None,
    intent: str | None = None,
    escalated_to_subagent: bool = False,
    model_used: str | None = None,
    provider: str | None = None,
    tokens_used: int = 0,
    response_time_ms: int | None = None,
    auth_uid: str | None = None,
):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            data = {
                "user_id": auth_uid,
                "guild_id": str(guild_id) if guild_id else None,
                "channel_id": str(channel_id) if channel_id else None,
                "user_message": user_message[:4000],
                "ai_response": (ai_response[:4000] if ai_response else None),
                "intent": intent,
                "escalated_to_subagent": escalated_to_subagent,
                "model_used": model_used,
                "provider": provider,
                "tokens_used": tokens_used,
                "response_time_ms": response_time_ms,
            }
            if auth_uid is None:
                data.pop("user_id", None)
            client.table("ai_conversations").insert(data).execute()
        except Exception as e:
            log.debug("log_conversation error: %s", e)

    _fire(_do())


# ── Task / step logging ────────────────────────────────────────────────────────

def create_task(
    *,
    discord_guild_id: str | None,
    title: str,
    description: str | None = None,
    mode: str = "plan",
    auth_uid: str | None = None,
) -> str | None:
    client = _get_client()
    if not client:
        return None
    try:
        data = {
            "user_id": auth_uid,
            "discord_guild_id": discord_guild_id,
            "title": title,
            "description": description,
            "mode": mode,
            "status": "planning" if mode == "plan" else "building",
        }
        if auth_uid is None:
            data.pop("user_id", None)
        result = client.table("tasks").insert(data).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception as e:
        log.debug("create_task error: %s", e)
    return None


def update_task(task_id: str, **fields):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            fields["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            client.table("tasks").update(fields).eq("id", task_id).execute()
        except Exception as e:
            log.debug("update_task error: %s", e)

    _fire(_do())


def add_task_step(
    *,
    task_id: str,
    step_number: int,
    title: str,
    description: str | None = None,
    tool_name: str | None = None,
    tool_args: dict | None = None,
    target: str | None = None,
    auth_uid: str | None = None,
) -> str | None:
    client = _get_client()
    if not client:
        return None
    try:
        data = {
            "task_id": task_id,
            "user_id": auth_uid,
            "step_number": step_number,
            "title": title,
            "description": description,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "target": target,
            "status": "pending",
        }
        if auth_uid is None:
            data.pop("user_id", None)
        result = client.table("task_steps").insert(data).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception as e:
        log.debug("add_task_step error: %s", e)
    return None


def update_task_step(step_id: str, **fields):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            if fields.get("status") == "completed":
                fields["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if fields.get("status") == "running" and "started_at" not in fields:
                fields["started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            client.table("task_steps").update(fields).eq("id", step_id).execute()
        except Exception as e:
            log.debug("update_task_step error: %s", e)

    _fire(_do())


# ── Edit log ───────────────────────────────────────────────────────────────────

def log_edit(
    *,
    action_type: str,
    action_category: str = "general",
    target: str | None = None,
    target_type: str | None = None,
    before_value: Any = None,
    after_value: Any = None,
    triggered_by: str = "subagent",
    triggered_by_name: str | None = None,
    status: str = "success",
    error_message: str | None = None,
    task_id: str | None = None,
    guild_id: str | None = None,
    auth_uid: str | None = None,
) -> str | None:
    client = _get_client()
    if not client:
        return None
    try:
        action_id = f"act_{uuid.uuid4().hex[:12]}"
        data = {
            "user_id": auth_uid,
            "task_id": task_id,
            "action_id": action_id,
            "action_type": action_type,
            "action_category": action_category,
            "target": target,
            "target_type": target_type,
            "before_value": before_value,
            "after_value": after_value,
            "triggered_by": triggered_by,
            "triggered_by_name": triggered_by_name,
            "status": status,
            "error_message": error_message,
            "guild_id": guild_id,
        }
        if auth_uid is None:
            data.pop("user_id", None)
        client.table("edit_logs").insert(data).execute()
        return action_id
    except Exception as e:
        log.debug("log_edit error: %s", e)
    return None


# ── Analytics events ───────────────────────────────────────────────────────────

def log_analytics(
    *,
    event_type: str,
    event_category: str = "general",
    event_name: str,
    value: Any = None,
    success: bool = True,
    error_message: str | None = None,
    guild_id: str | None = None,
    auth_uid: str | None = None,
):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            data = {
                "user_id": auth_uid,
                "guild_id": guild_id,
                "event_type": event_type,
                "event_category": event_category,
                "event_name": event_name,
                "value": value,
                "success": success,
                "error_message": error_message,
            }
            if auth_uid is None:
                data.pop("user_id", None)
            client.table("analytics_events").insert(data).execute()
        except Exception as e:
            log.debug("log_analytics error: %s", e)

    _fire(_do())


# ── AutoMod events ─────────────────────────────────────────────────────────────

def log_automod(
    *,
    guild_id: int | None,
    channel_id: int | None,
    user_discord_id: int | None,
    severity: str = "medium",
    category: str = "other",
    action_taken: str = "log",
    content_snippet: str | None = None,
    auth_uid: str | None = None,
):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            data = {
                "user_id": auth_uid,
                "guild_id": str(guild_id) if guild_id else None,
                "channel_id": str(channel_id) if channel_id else None,
                "user_discord_id": str(user_discord_id) if user_discord_id else None,
                "severity": severity,
                "category": category,
                "action_taken": action_taken,
                "content_snippet": (content_snippet[:200] if content_snippet else None),
            }
            if auth_uid is None:
                data.pop("user_id", None)
            client.table("automod_events").insert(data).execute()
        except Exception as e:
            log.debug("log_automod error: %s", e)

    _fire(_do())


# ── Rate limit sync ────────────────────────────────────────────────────────────

def sync_rate_limit(
    *,
    limit_type: str,
    messages_used: int,
    max_messages: int,
    guild_id: str | None = None,
    window_end: datetime.datetime | None = None,
    auth_uid: str | None = None,
):
    client = _get_client()
    if not client:
        return

    async def _do():
        try:
            data = {
                "user_id": auth_uid,
                "limit_type": limit_type,
                "guild_id": guild_id,
                "messages_used": messages_used,
                "max_messages": max_messages,
                "window_end": window_end.isoformat() if window_end else None,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if auth_uid is None:
                data.pop("user_id", None)
            client.table("rate_limit_state").upsert(data, on_conflict="user_id,limit_type").execute()
        except Exception as e:
            log.debug("sync_rate_limit error: %s", e)

    _fire(_do())


# ── API key management ────────────────────────────────────────────────────────

def store_api_key(provider: str, key_value: str, auth_uid: str | None = None) -> bool:
    client = _get_client()
    if not client or not auth_uid:
        return False
    try:
        key_hash = hashlib.sha256(key_value.encode()).hexdigest()[:32]
        client.table("api_keys").upsert({
            "user_id": auth_uid,
            "provider": provider,
            "key_label": f"{provider} key",
            "key_hash": key_hash,
            "is_valid": True,
            "last_validated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, on_conflict="user_id,provider").execute()
        return True
    except Exception as e:
        log.debug("store_api_key error: %s", e)
        return False


def has_valid_api_key(provider: str, auth_uid: str | None = None) -> bool:
    client = _get_client()
    if not client or not auth_uid:
        return False
    try:
        result = client.table("api_keys").select("is_valid").eq("user_id", auth_uid).eq("provider", provider).maybeSingle().execute()
        return bool(result.data and result.data.get("is_valid"))
    except Exception:
        return False


def get_user_subagent_limit(auth_uid: str | None = None) -> int:
    """Return 8 if user has any valid API key, else 2."""
    client = _get_client()
    if not client or not auth_uid:
        return 2
    try:
        result = client.table("api_keys").select("is_valid").eq("user_id", auth_uid).eq("is_valid", True).execute()
        return 8 if result.data else 2
    except Exception:
        return 2
