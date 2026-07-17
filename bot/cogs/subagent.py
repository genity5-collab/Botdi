"""
Subagent Cog — /subagent slash command.

Uses multi-provider AI with function calling to let users describe
Discord actions in natural language. The AI decides which functions
to call — create channels with permission overwrites, roles with
specific permissions, rich embeds, categories, events, and more.

Permissions:
  - Bot owner: infinite usage
  - Guild owner (server owner): 5 uses per week
  - Administrators: NOT allowed

API errors are never shown to the user.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    LOG_CHANNEL_ID, BOT_COLOR, COLOR_OK, COLOR_ERR, BOT_OWNER_ID, BOT_NAME,
    SUBAGENT_RATE_LIMIT, SUBAGENT_RATE_WINDOW,
)
from data_store import check_subagent_rate_limit
from utils import log_action, sanitize_ai_output
import ai_providers

log = logging.getLogger("vyrion.subagent")

genai_types = ai_providers.get_genai_types()


SUBAGENT_SYSTEM = """You are Vyrion Subagent, an expert Discord server manager AI. You execute Discord actions by calling functions.

## CRITICAL RULES
1. ALWAYS call functions to execute actions. NEVER just describe what you would do.
2. When a user mentions a role like @youtuber or @moderator, use that EXACT role name in the function call.
3. When asked to restrict a channel to specific roles, use set_channel_permissions to DENY access for @everyone and ALLOW access for the specified role.
4. When asked to create a channel for a specific role, create the channel AND set permissions in the same request.
5. Call multiple functions at once when actions are independent. Call them in sequence when one depends on another.
6. Make things look professional — add emojis to channel names, topics, and embeds when appropriate.
7. Use rich embeds (with colors, fields, footers, thumbnails) instead of plain messages when announcing something.
8. When creating roles, set appropriate permissions. For example:
   - Moderator: manage_messages=True, kick_members=True, timeout_members=True
   - Admin: administrator=True
   - VIP/VIP+: mentionable=True, hoist=True, priority=1
   - Muted: send_messages=False, add_reactions=False, connect=False
9. When creating categories, organize channels logically (e.g. "Information" category for rules, announcements).
10. NEVER mention API errors, provider names, model names, or internal system details.
11. If something goes wrong internally, just say 'I had trouble with that action.'
12. Do not ask for confirmation. Do not explain what you're about to do. Just call the functions.
13. After all functions are called, give a brief summary with emoji decorations.

## EXAMPLES

User: "Create a channel for @youtuber role only to chat there"
You should:
1. Call create_text_channel with name="youtuber-chat", topic="💬 Exclusive chat for YouTubers"
2. Call set_channel_permissions with channel_name="youtuber-chat", role_name="@everyone", deny="view_channel"
3. Call set_channel_permissions with channel_name="youtuber-chat", role_name="youtuber", allow="view_channel,send_messages,read_message_history,add_reactions,attach_files,embed_links"

User: "Create a moderator role with proper permissions"
You should:
1. Call create_role with name="Moderator", color="#2B2D31", hoist=true, mentionable=true, permissions="manage_messages,kick_members,timeout_members,manage_channels,view_audit_log,manage_roles"

User: "Send a welcome embed in #general"
You should:
1. Call send_embed with channel_name="general", title="🌟 Welcome to the Server!", description="We're glad to have you here. Check out the rules and enjoy your stay!", color="#5865F2", footer="Vyrion Subagent", thumbnail_url=""

User: "Set up a server with rules and announcements"
You should:
1. Call create_category with name="Information"
2. Call create_text_channel with name="rules", category="Information", topic="📜 Server Rules"
3. Call create_text_channel with name="announcements", category="Information", topic="📢 Server Announcements"
4. Call create_text_channel with name="general", category="Information", topic="💬 General Chat"
5. Call send_embed in rules with the rules content
6. Call send_embed in announcements welcoming everyone

## PERMISSION FLAGS
When setting role permissions or channel overwrites, use these comma-separated flag names:
- administrator, manage_guild, manage_roles, manage_channels, manage_messages, manage_webhooks, manage_emojis, manage_events
- kick_members, ban_members, timeout_members, moderate_members
- view_audit_log, view_guild_insights
- send_messages, send_messages_in_threads, create_public_threads, create_private_threads
- embed_links, attach_files, add_reactions, use_external_emojis, use_external_stickers
- read_message_history, read_messages, view_channel
- connect, speak, stream, use_voice_activation, priority_speaker
- change_nickname, mention_everyone, mute_members, deafen_members, move_members
- request_to_speak, manage_threads, use_application_commands, use_embedded_activities
"""

MAX_ROUNDS = 10

# ── Permission flag mapping ───────────────────────────────────────────────────

_PERM_FLAGS: dict[str, int] = {
    "administrator": 8,
    "view_audit_log": 128,
    "manage_guild": 32,
    "manage_roles": 268435456,
       "manage_channels": 16,
    "manage_webhooks": 536870912,
    "manage_emojis": 1073741824,
    "manage_events": 8589934592,
    "view_guild_insights": 524288,
    "kick_members": 2,
    "ban_members": 4,
    "timeout_members": 137438953472,
    "moderate_members": 1099511627776,
    "send_messages": 2048,
    "send_messages_in_threads": 274877906944,
    "create_public_threads": 33554432,
    "create_private_threads": 68719476736,
    "embed_links": 16384,
    "attach_files": 32768,
    "add_reactions": 64,
    "use_external_emojis": 262144,
    "use_external_stickers": 549755813888,
    "read_message_history": 65536,
    "read_messages": 1024,
    "view_channel": 1024,
    "connect": 1048576,
    "speak": 2097152,
    "stream": 67108864,
    "use_voice_activation": 536870912,
    "priority_speaker": 256,
    "change_nickname": 67108864,
    "mention_everyone": 131072,
    "mute_members": 4194304,
    "deafen_members": 8388608,
    "move_members": 16777216,
    "request_to_speak": 4294967296,
    "manage_threads": 17179869184,
    "use_application_commands": 128,
    "use_embedded_activities": 8589934592,
}

def _parse_perms(perm_str: str) -> int:
    """Parse comma-separated permission flag names into a Discord permissions integer."""
    if not perm_str:
        return 0
    bits = 0
    for flag in perm_str.split(","):
        flag = flag.strip().lower()
        if flag in _PERM_FLAGS:
            bits |= _PERM_FLAGS[flag]
    return bits


def _build_tools() -> list:
    if genai_types is None:
        return []
    fd = genai_types.FunctionDeclaration
    Tool = genai_types.Tool

    def decl(name, desc, props, required):
        return fd(name=name, description=desc, parameters_json_schema={
            "type": "object", "properties": props, "required": required,
        })

    return [Tool(function_declarations=[
        decl("create_text_channel", "Create a new text channel. Use set_channel_permissions after if role-restricted access is needed.", {
            "name": {"type": "string", "description": "Channel name, lowercase with hyphens. Add emoji decoration e.g. 'general-chat' or 'rules'"},
            "category": {"type": "string", "description": "Optional: category name to place channel in"},
            "topic": {"type": "string", "description": "Optional: channel topic with emoji decoration e.g. '💬 General chat for everyone'"},
        }, ["name"]),
        decl("create_voice_channel", "Create a new voice channel.", {
            "name": {"type": "string", "description": "Channel name"},
            "category": {"type": "string", "description": "Optional: category name"},
            "user_limit": {"type": "integer", "description": "Max users (0 = unlimited)"},
        }, ["name"]),
        decl("create_category", "Create a new channel category to organize channels.", {
            "name": {"type": "string", "description": "Category name e.g. 'Information', 'Voice Channels', 'Staff'"},
        }, ["name"]),
        decl("create_role", "Create a new role with specific permissions. Set permissions as comma-separated flag names.", {
            "name": {"type": "string", "description": "Role name e.g. 'Moderator', 'VIP', 'YouTuber'"},
            "color": {"type": "string", "description": "Hex color e.g. '#FF0000' for red, '#5865F2' for blurple, '#2B2D31' for dark"},
            "hoist": {"type": "boolean", "description": "Display separately in member list (default false). Set true for VIP/Moderator roles"},
            "mentionable": {"type": "boolean", "description": "Allow @mention (default false). Set true for most roles"},
            "permissions": {"type": "string", "description": "Comma-separated permission flags e.g. 'manage_messages,kick_members,timeout_members'. See permission flags reference in system prompt"},
        }, ["name"]),
        decl("set_channel_permissions", "Set permission overwrites for a channel. Use to restrict channel access to specific roles. For @everyone role, use role_name='@everyone'.", {
            "channel_name": {"type": "string", "description": "Channel name to set permissions on"},
            "role_name": {"type": "string", "description": "Role name. Use '@everyone' for the everyone role"},
            "allow": {"type": "string", "description": "Comma-separated permission flags to ALLOW e.g. 'view_channel,send_messages,read_message_history'. Leave empty for none"},
            "deny": {"type": "string", "description": "Comma-separated permission flags to DENY e.g. 'view_channel'. Leave empty for none"},
        }, ["channel_name", "role_name"]),
        decl("send_message", "Send a plain text message to a channel.", {
            "channel_name": {"type": "string", "description": "Target channel name (without #)"},
            "content": {"type": "string", "description": "Message content. Can include emoji decoration"},
        }, ["channel_name", "content"]),
        decl("send_embed", "Send a rich embed to a channel. Use for announcements, welcome messages, rules, etc. Make it visually appealing.", {
            "channel_name": {"type": "string", "description": "Target channel name (without #)"},
            "title": {"type": "string", "description": "Embed title with emoji e.g. '🌟 Welcome to the Server!'"},
            "description": {"type": "string", "description": "Embed body text. Can be multi-line with formatting"},
            "color": {"type": "string", "description": "Hex color e.g. '#FF0000' (red), '#23A55A' (green), '#5865F2' (blurple), '#F0B132' (gold), '#9B59B6' (purple), '#E91E63' (pink)"},
            "footer": {"type": "string", "description": "Optional: footer text e.g. 'Vyrion Subagent'"},
            "image_url": {"type": "string", "description": "Optional: banner image URL"},
            "thumbnail_url": {"type": "string", "description": "Optional: small thumbnail image URL (top-right)"},
            "author_name": {"type": "string", "description": "Optional: author name shown at top of embed"},
            "author_icon_url": {"type": "string", "description": "Optional: author avatar icon URL"},
            "field_name": {"type": "string", "description": "Optional: first field name/title"},
            "field_value": {"type": "string", "description": "Optional: first field value/content"},
            "field_inline": {"type": "boolean", "description": "Optional: whether first field is inline (default false)"},
            "field2_name": {"type": "string", "description": "Optional: second field name/title"},
            "field2_value": {"type": "string", "description": "Optional: second field value/content"},
            "field2_inline": {"type": "boolean", "description": "Optional: whether second field is inline"},
            "field3_name": {"type": "string", "description": "Optional: third field name/title"},
            "field3_value": {"type": "string", "description": "Optional: third field value/content"},
            "field3_inline": {"type": "boolean", "description": "Optional: whether third field is inline"},
            "timestamp": {"type": "boolean", "description": "Optional: set to true to show current timestamp at bottom of embed"},
        }, ["channel_name", "title", "description"]),
        decl("add_role_to_user", "Assign a role to a user.", {
            "role_name": {"type": "string", "description": "Role name"},
            "user": {"type": "string", "description": "Username or user ID"},
        }, ["role_name", "user"]),
        decl("remove_role_from_user", "Remove a role from a user.", {
            "role_name": {"type": "string", "description": "Role name"},
            "user": {"type": "string", "description": "Username or user ID"},
        }, ["role_name", "user"]),
        decl("rename_channel", "Rename a channel.", {
            "current_name": {"type": "string", "description": "Current channel name"},
            "new_name": {"type": "string", "description": "New channel name"},
        }, ["current_name", "new_name"]),
        decl("set_slowmode", "Set slowmode on a text channel.", {
            "channel_name": {"type": "string", "description": "Channel name"},
            "seconds": {"type": "integer", "description": "Slowmode seconds (0-21600)"},
        }, ["channel_name", "seconds"]),
        decl("set_channel_topic", "Set the topic of a text channel.", {
            "channel_name": {"type": "string", "description": "Channel name"},
            "topic": {"type": "string", "description": "New topic text with emoji decoration"},
        }, ["channel_name", "topic"]),
        decl("delete_channel", "Delete a channel by name.", {
            "channel_name": {"type": "string", "description": "Channel to delete"},
        }, ["channel_name"]),
        decl("create_scheduled_event", "Create a scheduled event in the server.", {
            "name": {"type": "string", "description": "Event name/title"},
            "description": {"type": "string", "description": "Event description"},
            "start_time": {"type": "string", "description": "Start time in ISO 8601 format (e.g. 2025-01-15T20:00:00)"},
            "end_time": {"type": "string", "description": "Optional: end time ISO 8601"},
            "channel_name": {"type": "string", "description": "Optional: voice channel name to host in (for stage/voice events)"},
            "location": {"type": "string", "description": "Optional: external location text (if not in a channel)"},
        }, ["name", "start_time"]),
        decl("edit_scheduled_event", "Edit an existing scheduled event.", {
            "event_name": {"type": "string", "description": "Current event name"},
            "new_name": {"type": "string", "description": "New event name (optional)"},
            "new_description": {"type": "string", "description": "New description (optional)"},
            "new_start_time": {"type": "string", "description": "New start time ISO 8601 (optional)"},
        }, ["event_name"]),
        decl("delete_scheduled_event", "Delete a scheduled event by name.", {
            "event_name": {"type": "string", "description": "Event name to delete"},
        }, ["event_name"]),
        decl("create_forum_channel", "Create a forum channel.", {
            "name": {"type": "string", "description": "Channel name"},
            "category": {"type": "string", "description": "Optional: category name"},
            "topic": {"type": "string", "description": "Optional: channel topic/guidelines"},
        }, ["name"]),
        decl("create_announcement_channel", "Create an announcement channel.", {
            "name": {"type": "string", "description": "Channel name"},
            "category": {"type": "string", "description": "Optional: category name"},
            "topic": {"type": "string", "description": "Optional: channel topic"},
        }, ["name"]),
        decl("create_stage_channel", "Create a stage channel.", {
            "name": {"type": "string", "description": "Channel name"},
            "category": {"type": "string", "description": "Optional: category name"},
        }, ["name"]),
        decl("create_invite", "Create an invite for a channel.", {
            "channel_name": {"type": "string", "description": "Channel name"},
            "max_age": {"type": "integer", "description": "Max age in seconds (0 = never)"},
            "max_uses": {"type": "integer", "description": "Max uses (0 = unlimited)"},
        }, ["channel_name"]),
        decl("kick_member", "Kick a member from the server.", {
            "user": {"type": "string", "description": "Username or user ID"},
            "reason": {"type": "string", "description": "Reason for kick"},
        }, ["user"]),
        decl("ban_member", "Ban a member from the server.", {
            "user": {"type": "string", "description": "Username or user ID"},
            "reason": {"type": "string", "description": "Reason for ban"},
        }, ["user"]),
        decl("timeout_member", "Timeout a member.", {
            "user": {"type": "string", "description": "Username or user ID"},
            "minutes": {"type": "integer", "description": "Duration in minutes"},
            "reason": {"type": "string", "description": "Reason"},
        }, ["user", "minutes"]),
        decl("send_dm", "Send a DM to a server member.", {
            "user": {"type": "string", "description": "Username or user ID"},
            "content": {"type": "string", "description": "Message content"},
        }, ["user", "content"]),
        decl("edit_role", "Edit an existing role's permissions, color, or name.", {
            "role_name": {"type": "string", "description": "Current role name"},
            "new_name": {"type": "string", "description": "Optional: new role name"},
            "color": {"type": "string", "description": "Optional: new hex color"},
            "permissions": {"type": "string", "description": "Optional: comma-separated permission flags to set"},
            "hoist": {"type": "boolean", "description": "Optional: display separately"},
            "mentionable": {"type": "boolean", "description": "Optional: allow @mention"},
        }, ["role_name"]),
        decl("delete_role", "Delete a role by name.", {
            "role_name": {"type": "string", "description": "Role name to delete"},
        }, ["role_name"]),
        decl("reorder_channel", "Move a channel to a different category or reorder it.", {
            "channel_name": {"type": "string", "description": "Channel name to move"},
            "category": {"type": "string", "description": "Optional: target category name"},
            "position": {"type": "integer", "description": "Optional: position in the category (0 = top)"},
        }, ["channel_name"]),
    ])]


def _build_tools_json() -> list[dict]:
    def t(name, desc, props, required):
        return {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required}}
    return [
        t("create_text_channel", "Create a new text channel. Use set_channel_permissions after if role-restricted access is needed.", {
            "name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"},
        }, ["name"]),
        t("create_voice_channel", "Create a new voice channel.", {
            "name": {"type": "string"}, "category": {"type": "string"}, "user_limit": {"type": "integer"},
        }, ["name"]),
        t("create_category", "Create a new channel category.", {"name": {"type": "string"}}, ["name"]),
        t("create_role", "Create a new role with specific permissions. Set permissions as comma-separated flag names.", {
            "name": {"type": "string"}, "color": {"type": "string"},
            "hoist": {"type": "boolean"}, "mentionable": {"type": "boolean"},
            "permissions": {"type": "string"},
        }, ["name"]),
        t("set_channel_permissions", "Set permission overwrites for a channel. Use to restrict channel access to specific roles. For @everyone role, use role_name='@everyone'.", {
            "channel_name": {"type": "string"}, "role_name": {"type": "string"},
            "allow": {"type": "string"}, "deny": {"type": "string"},
        }, ["channel_name", "role_name"]),
        t("send_message", "Send a plain text message to a channel.", {
            "channel_name": {"type": "string"}, "content": {"type": "string"},
        }, ["channel_name", "content"]),
        t("send_embed", "Send a rich embed to a channel. Use for announcements, welcome messages, rules, etc.", {
            "channel_name": {"type": "string"}, "title": {"type": "string"},
            "description": {"type": "string"}, "color": {"type": "string"},
            "footer": {"type": "string"}, "image_url": {"type": "string"}, "thumbnail_url": {"type": "string"},
            "author_name": {"type": "string"}, "author_icon_url": {"type": "string"},
            "field_name": {"type": "string"}, "field_value": {"type": "string"}, "field_inline": {"type": "boolean"},
            "field2_name": {"type": "string"}, "field2_value": {"type": "string"}, "field2_inline": {"type": "boolean"},
            "field3_name": {"type": "string"}, "field3_value": {"type": "string"}, "field3_inline": {"type": "boolean"},
            "timestamp": {"type": "boolean"},
        }, ["channel_name", "title", "description"]),
        t("add_role_to_user", "Assign a role to a user.", {"role_name": {"type": "string"}, "user": {"type": "string"}}, ["role_name", "user"]),
        t("remove_role_from_user", "Remove a role from a user.", {"role_name": {"type": "string"}, "user": {"type": "string"}}, ["role_name", "user"]),
        t("rename_channel", "Rename a channel.", {"current_name": {"type": "string"}, "new_name": {"type": "string"}}, ["current_name", "new_name"]),
        t("set_slowmode", "Set slowmode on a text channel.", {"channel_name": {"type": "string"}, "seconds": {"type": "integer"}}, ["channel_name", "seconds"]),
        t("set_channel_topic", "Set the topic of a text channel.", {"channel_name": {"type": "string"}, "topic": {"type": "string"}}, ["channel_name", "topic"]),
        t("delete_channel", "Delete a channel by name.", {"channel_name": {"type": "string"}}, ["channel_name"]),
        t("create_scheduled_event", "Create a scheduled event.", {"name": {"type": "string"}, "description": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}, "channel_name": {"type": "string"}, "location": {"type": "string"}}, ["name", "start_time"]),
        t("edit_scheduled_event", "Edit a scheduled event.", {"event_name": {"type": "string"}, "new_name": {"type": "string"}, "new_description": {"type": "string"}, "new_start_time": {"type": "string"}}, ["event_name"]),
        t("delete_scheduled_event", "Delete a scheduled event.", {"event_name": {"type": "string"}}, ["event_name"]),
        t("create_forum_channel", "Create a forum channel.", {"name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"}}, ["name"]),
        t("create_announcement_channel", "Create an announcement channel.", {"name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"}}, ["name"]),
        t("create_stage_channel", "Create a stage channel.", {"name": {"type": "string"}, "category": {"type": "string"}}, ["name"]),
        t("create_invite", "Create an invite.", {"channel_name": {"type": "string"}, "max_age": {"type": "integer"}, "max_uses": {"type": "integer"}}, ["channel_name"]),
        t("kick_member", "Kick a member.", {"user": {"type": "string"}, "reason": {"type": "string"}}, ["user"]),
        t("ban_member", "Ban a member.", {"user": {"type": "string"}, "reason": {"type": "string"}}, ["user"]),
        t("timeout_member", "Timeout a member.", {"user": {"type": "string"}, "minutes": {"type": "integer"}, "reason": {"type": "string"}}, ["user", "minutes"]),
        t("send_dm", "Send a DM to a member.", {"user": {"type": "string"}, "content": {"type": "string"}}, ["user", "content"]),
        t("edit_role", "Edit an existing role.", {"role_name": {"type": "string"}, "new_name": {"type": "string"}, "color": {"type": "string"}, "permissions": {"type": "string"}, "hoist": {"type": "boolean"}, "mentionable": {"type": "boolean"}}, ["role_name"]),
        t("delete_role", "Delete a role.", {"role_name": {"type": "string"}}, ["role_name"]),
        t("reorder_channel", "Move a channel to a different category.", {"channel_name": {"type": "string"}, "category": {"type": "string"}, "position": {"type": "integer"}}, ["channel_name"]),
    ]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hex_to_int(color_hex: str) -> int:
    c = (color_hex or "").lstrip("#")
    try:
        return int(c, 16)
    except (ValueError, TypeError):
        return 0x5865F2


def _find_channel(guild: discord.Guild, name: str) -> discord.abc.GuildChannel | None:
    clean = name.lower().lstrip("#").replace("-", "-")
    for ch in guild.channels:
        if ch.name.lower() == clean:
            return ch
    return None


def _find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    clean = name.lower().lstrip("@")
    if clean == "everyone":
        return guild.default_role
    for r in guild.roles:
        if r.name.lower() == clean:
            return r
    return None


async def _find_member(guild: discord.Guild, query: str) -> discord.Member | None:
    if query.isdigit():
        return guild.get_member(int(query))
    member = guild.get_member_named(query)
    if member:
        return member
    for m in guild.members:
        if m.name.lower() == query.lower() or m.display_name.lower() == query.lower():
            return m
    return None


def _parse_iso_dt(s: str) -> datetime.datetime | None:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


# ── Live changelog ─────────────────────────────────────────────────────────────

CHANGELOG_FILE = Path(__file__).parent / "data" / "subagent_changelog.json"

def _load_changelog() -> list[dict]:
    if CHANGELOG_FILE.exists():
        try:
            return json.loads(CHANGELOG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_changelog(entries: list[dict]) -> None:
    CHANGELOG_FILE.parent.mkdir(exist_ok=True)
    if len(entries) > 200:
        entries = entries[-200:]
    CHANGELOG_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def _add_changelog_entry(action: str, detail: str, status: str = "ok") -> None:
    entries = _load_changelog()
    entries.append({"ts": datetime.datetime.now().isoformat(timespec="seconds"), "action": action, "detail": detail[:300], "status": status})
    _save_changelog(entries)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Subagent(commands.Cog, name="Subagent"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="subagent", description="Ask the AI to perform Discord actions (server owners only, 5/week)")
    @app_commands.describe(prompt="What should the AI do? e.g. 'Create a channel for @youtuber role only to chat'")
    async def subagent(self, interaction: discord.Interaction, prompt: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("This command only works in a server.")
            return

        is_bot_owner = interaction.user.id == BOT_OWNER_ID
        is_guild_owner = interaction.guild.owner_id == interaction.user.id

        if not is_bot_owner and not is_guild_owner:
            await interaction.followup.send("Only the server owner can use this command.", ephemeral=True)
            return

        if not is_bot_owner:
            allowed, remaining, retry_after = check_subagent_rate_limit(
                interaction.user.id, limit=SUBAGENT_RATE_LIMIT, window=SUBAGENT_RATE_WINDOW, owner_id=BOT_OWNER_ID,
            )
            if not allowed:
                days = max(retry_after // 86400, 1)
                await interaction.followup.send(f"You've used all {SUBAGENT_RATE_LIMIT} subagent actions for this week. Try again in ~{days} day(s).", ephemeral=True)
                return

        if not ai_providers.is_any_provider_available():
            await interaction.followup.send("I'm not configured yet. Please try again later.")
            return

        guild = interaction.guild
        edit_log: list[str] = []

        # Build a context string with current server state for the AI
        channels_info = []
        for ch in guild.channels[:50]:
            ch_type = type(ch).__name__.replace("Channel", "").lower()
            channels_info.append(f"  - #{ch.name} ({ch_type})" + (f" in '{ch.category.name}'" if ch.category else ""))
        roles_info = []
        for r in guild.roles[:30]:
            roles_info.append(f"  - @{r.name} (id:{r.id})")
        context = f"Current server: {guild.name}\nChannels:\n" + "\n".join(channels_info) + "\nRoles:\n" + "\n".join(roles_info)

        async def _execute_function(name: str, args: dict) -> str:
            try:
                if name == "create_text_channel":
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_text_channel(args["name"], category=category, topic=args.get("topic"))
                    entry = f"Created text channel #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_text_channel", entry)
                    return entry

                if name == "create_voice_channel":
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_voice_channel(args["name"], category=category, user_limit=args.get("user_limit", 0))
                    entry = f"Created voice channel 🔊 {ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_voice_channel", entry)
                    return entry

                if name == "create_category":
                    cat = await guild.create_category(args["name"])
                    entry = f"Created category 📁 {cat.name}"
                    edit_log.append(entry); _add_changelog_entry("create_category", entry)
                    return entry

                if name == "create_role":
                    color = _hex_to_int(args.get("color", "#5865F2"))
                    perm_bits = _parse_perms(args.get("permissions", ""))
                    role = await guild.create_role(
                        name=args["name"], color=discord.Color(color),
                        hoist=args.get("hoist", False), mentionable=args.get("mentionable", False),
                        permissions=discord.Permissions(perm_bits),
                    )
                    entry = f"Created role @{role.name}"
                    edit_log.append(entry); _add_changelog_entry("create_role", entry)
                    return entry

                if name == "set_channel_permissions":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch:
                        return f"Channel '{args['channel_name']}' not found."
                    role = _find_role(guild, args["role_name"])
                    if not role:
                        return f"Role '{args['role_name']}' not found."
                    allow_bits = _parse_perms(args.get("allow", ""))
                    deny_bits = _parse_perms(args.get("deny", ""))
                    overwrite = discord.PermissionOverwrite.from_pair(
                        allow=discord.Permissions(allow_bits),
                        deny=discord.Permissions(deny_bits),
                    )
                    await ch.set_permissions(role, overwrite=overwrite)
                    entry = f"Set permissions for #{ch.name}: @{role.name} allow=[{args.get('allow','')}] deny=[{args.get('deny','')}]"
                    edit_log.append(entry); _add_changelog_entry("set_channel_permissions", entry)
                    return entry

                if name == "send_message":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    await ch.send(args["content"])
                    entry = f"Sent message in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_message", entry)
                    return entry

                if name == "send_embed":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    embed = discord.Embed(
                        title=args["title"], description=args["description"],
                        color=discord.Color(_hex_to_int(args.get("color", "#5865F2"))),
                    )
                    if args.get("footer"):
                        embed.set_footer(text=args["footer"])
                    if args.get("image_url"):
                        embed.set_image(url=args["image_url"])
                    if args.get("thumbnail_url"):
                        embed.set_thumbnail(url=args["thumbnail_url"])
                    if args.get("author_name"):
                        embed.set_author(name=args["author_name"], icon_url=args.get("author_icon_url", "") or None)
                    if args.get("field_name") and args.get("field_value"):
                        embed.add_field(name=args["field_name"], value=args["field_value"], inline=args.get("field_inline", False))
                    if args.get("field2_name") and args.get("field2_value"):
                        embed.add_field(name=args["field2_name"], value=args["field2_value"], inline=args.get("field2_inline", False))
                    if args.get("field3_name") and args.get("field3_value"):
                        embed.add_field(name=args["field3_name"], value=args["field3_value"], inline=args.get("field3_inline", False))
                    if args.get("timestamp"):
                        embed.timestamp = discord.utils.utcnow()
                    await ch.send(embed=embed)
                    entry = f"Sent embed '{args['title']}' in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_embed", entry)
                    return entry

                if name == "add_role_to_user":
                    role = _find_role(guild, args["role_name"])
                    if not role:
                        return f"Role '{args['role_name']}' not found."
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    await member.add_roles(role)
                    entry = f"Added role @{role.name} to {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("add_role_to_user", entry)
                    return entry

                if name == "remove_role_from_user":
                    role = _find_role(guild, args["role_name"])
                    if not role:
                        return f"Role '{args['role_name']}' not found."
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    await member.remove_roles(role)
                    entry = f"Removed role @{role.name} from {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("remove_role_from_user", entry)
                    return entry

                if name == "rename_channel":
                    ch = _find_channel(guild, args["current_name"])
                    if not ch:
                        return f"Channel '{args['current_name']}' not found."
                    old = ch.name
                    await ch.edit(name=args["new_name"])
                    entry = f"Renamed #{old} → #{args['new_name']}"
                    edit_log.append(entry); _add_changelog_entry("rename_channel", entry)
                    return entry

                if name == "set_slowmode":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    await ch.edit(slowmode_delay=args["seconds"])
                    entry = f"Set slowmode in #{ch.name} to {args['seconds']}s"
                    edit_log.append(entry); _add_changelog_entry("set_slowmode", entry)
                    return entry

                if name == "set_channel_topic":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    await ch.edit(topic=args["topic"])
                    entry = f"Set topic of #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("set_channel_topic", entry)
                    return entry

                if name == "delete_channel":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch:
                        return f"Channel '{args['channel_name']}' not found."
                    chname = ch.name
                    await ch.delete()
                    entry = f"Deleted channel #{chname}"
                    edit_log.append(entry); _add_changelog_entry("delete_channel", entry)
                    return entry

                if name == "create_scheduled_event":
                    start_dt = _parse_iso_dt(args["start_time"])
                    if not start_dt:
                        return f"Invalid start_time: {args['start_time']}"
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
                    end_dt = _parse_iso_dt(args.get("end_time", ""))
                    if end_dt and end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
                    kwargs: dict = {"name": args["name"], "description": args.get("description", ""), "start_time": start_dt}
                    if end_dt:
                        kwargs["end_time"] = end_dt
                    ch = _find_channel(guild, args["channel_name"]) if args.get("channel_name") else None
                    if ch and isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                        kwargs["entity_type"] = discord.EntityType.voice
                        kwargs["channel"] = ch
                    elif args.get("location"):
                        kwargs["entity_type"] = discord.EntityType.external
                        kwargs["location"] = args["location"]
                    else:
                        kwargs["entity_type"] = discord.EntityType.external
                        kwargs["location"] = args.get("location", "Online")
                    event = await guild.create_scheduled_event(**kwargs)
                    entry = f"Created event '{event.name}' starting {start_dt.isoformat()}"
                    edit_log.append(entry); _add_changelog_entry("create_scheduled_event", entry)
                    return entry

                if name == "edit_scheduled_event":
                    event = discord.utils.find(lambda e: e.name.lower() == args["event_name"].lower(), guild.scheduled_events)
                    if not event:
                        return f"Event '{args['event_name']}' not found."
                    edit_kwargs: dict = {}
                    if args.get("new_name"):
                        edit_kwargs["name"] = args["new_name"]
                    if args.get("new_description"):
                        edit_kwargs["description"] = args["new_description"]
                    new_start = _parse_iso_dt(args.get("new_start_time", ""))
                    if new_start:
                        if new_start.tzinfo is None:
                            new_start = new_start.replace(tzinfo=datetime.timezone.utc)
                        edit_kwargs["start_time"] = new_start
                    if edit_kwargs:
                        await event.edit(**edit_kwargs)
                    entry = f"Edited event '{args['event_name']}'"
                    edit_log.append(entry); _add_changelog_entry("edit_scheduled_event", entry)
                    return entry

                if name == "delete_scheduled_event":
                    event = discord.utils.find(lambda e: e.name.lower() == args["event_name"].lower(), guild.scheduled_events)
                    if not event:
                        return f"Event '{args['event_name']}' not found."
                    await event.delete()
                    entry = f"Deleted event '{args['event_name']}'"
                    edit_log.append(entry); _add_changelog_entry("delete_scheduled_event", entry)
                    return entry

                if name == "create_forum_channel":
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_forum_channel(args["name"], category=category, topic=args.get("topic"))
                    entry = f"Created forum channel #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_forum_channel", entry)
                    return entry

                if name == "create_announcement_channel":
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_text_channel(args["name"], category=category, topic=args.get("topic"), news=True)
                    entry = f"Created announcement channel #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_announcement_channel", entry)
                    return entry

                if name == "create_stage_channel":
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_stage_channel(args["name"], category=category)
                    entry = f"Created stage channel {ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_stage_channel", entry)
                    return entry

                if name == "create_invite":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
                        return f"Channel '{args['channel_name']}' not found."
                    invite = await ch.create_invite(max_age=args.get("max_age", 0), max_uses=args.get("max_uses", 0))
                    entry = f"Created invite {invite.url} for #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_invite", entry)
                    return entry

                if name == "kick_member":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    await member.kick(reason=args.get("reason", "Subagent action"))
                    entry = f"Kicked {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("kick_member", entry)
                    return entry

                if name == "ban_member":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        uid = int(args["user"]) if args["user"].isdigit() else None
                        if uid:
                            await guild.ban(discord.Object(id=uid), reason=args.get("reason", "Subagent action"))
                            entry = f"Banned user ID {uid}"
                            edit_log.append(entry); _add_changelog_entry("ban_member", entry)
                            return entry
                        return f"User '{args['user']}' not found."
                    await member.ban(reason=args.get("reason", "Subagent action"), delete_message_days=0)
                    entry = f"Banned {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("ban_member", entry)
                    return entry

                if name == "timeout_member":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    until = discord.utils.utcnow() + datetime.timedelta(minutes=args["minutes"])
                    await member.timeout(until, reason=args.get("reason", "Subagent action"))
                    entry = f"Timed out {member.display_name} for {args['minutes']}m"
                    edit_log.append(entry); _add_changelog_entry("timeout_member", entry)
                    return entry

                if name == "send_dm":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    dm = await member.create_dm()
                    await dm.send(args["content"])
                    entry = f"Sent DM to {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("send_dm", entry)
                    return entry

                if name == "edit_role":
                    role = _find_role(guild, args["role_name"])
                    if not role:
                        return f"Role '{args['role_name']}' not found."
                    edit_kwargs: dict = {}
                    if args.get("new_name"):
                        edit_kwargs["name"] = args["new_name"]
                    if args.get("color"):
                        edit_kwargs["color"] = discord.Color(_hex_to_int(args["color"]))
                    if args.get("permissions"):
                        edit_kwargs["permissions"] = discord.Permissions(_parse_perms(args["permissions"]))
                    if "hoist" in args:
                        edit_kwargs["hoist"] = args["hoist"]
                    if "mentionable" in args:
                        edit_kwargs["mentionable"] = args["mentionable"]
                    if edit_kwargs:
                        await role.edit(**edit_kwargs)
                    entry = f"Edited role @{role.name}"
                    edit_log.append(entry); _add_changelog_entry("edit_role", entry)
                    return entry

                if name == "delete_role":
                    role = _find_role(guild, args["role_name"])
                    if not role:
                        return f"Role '{args['role_name']}' not found."
                    rname = role.name
                    await role.delete()
                    entry = f"Deleted role @{rname}"
                    edit_log.append(entry); _add_changelog_entry("delete_role", entry)
                    return entry

                if name == "reorder_channel":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch:
                        return f"Channel '{args['channel_name']}' not found."
                    edit_kwargs: dict = {}
                    if args.get("category"):
                        cat = discord.utils.get(guild.categories, name=args["category"])
                        if cat:
                            edit_kwargs["category"] = cat
                    if "position" in args:
                        edit_kwargs["position"] = args["position"]
                    if edit_kwargs:
                        await ch.edit(**edit_kwargs)
                    entry = f"Reordered #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("reorder_channel", entry)
                    return entry

                return f"Unknown function: {name}"
            except discord.Forbidden:
                msg = f"Missing permissions for: {name}"
                edit_log.append(f"[FAILED] {msg}")
                _add_changelog_entry(name, msg, "failed")
                return msg
            except Exception as e:
                msg = f"Error in {name}"
                edit_log.append(f"[FAILED] {msg}")
                _add_changelog_entry(name, msg, "failed")
                log.exception("Subagent function error: %s", name)
                return msg

        try:
            final_text = ""
            use_gemini = ai_providers.is_gemini_available() and genai_types is not None

            full_system = f"{SUBAGENT_SYSTEM}\n\n## CURRENT SERVER STATE\n{context}"

            if use_gemini:
                contents = [genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prompt)])]
                tools = _build_tools()

                for _ in range(MAX_ROUNDS):
                    response = await ai_providers.gemini_function_call(full_system, contents, tools)
                    if response is None:
                        use_gemini = False
                        break
                    if not response.function_calls:
                        final_text = response.text or "Done."
                        break
                    fc_parts = []
                    for fc in response.function_calls:
                        fn_name = fc.name
                        fn_args = dict(fc.args) if fc.args else {}
                        result = await _execute_function(fn_name, fn_args)
                        fc_parts.append(genai_types.Part.from_function_response(name=fn_name, response={"result": result}))
                        contents.append(genai_types.Content(role="model", parts=[genai_types.Part.from_function_call(name=fn_name, args=fn_args)]))
                    contents.append(genai_types.Content(role="user", parts=fc_parts))
                else:
                    final_text = "Reached max function-call rounds."

            if not use_gemini:
                tools_json = _build_tools_json()
                chat_messages: list[dict] = [{"role": "user", "content": prompt}]

                for round_num in range(MAX_ROUNDS):
                    result = await ai_providers.openai_function_call(full_system, chat_messages, tools_json)
                    if result is None:
                        result = await ai_providers.text_function_call(full_system, chat_messages, tools_json)
                        if result is None:
                            final_text = "I had trouble processing that request. Please try again."
                            break
                    tool_calls = result.get("tool_calls")
                    if not tool_calls:
                        final_text = result.get("content") or "Done."
                        break
                    for idx, tc in enumerate(tool_calls):
                        call_id = f"call_{round_num}_{idx}"
                        fn_name = tc["name"]
                        fn_args = tc["arguments"]
                        exec_result = await _execute_function(fn_name, fn_args)
                        chat_messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": fn_name, "arguments": json.dumps(fn_args)}}]})
                        chat_messages.append({"role": "tool", "tool_call_id": call_id, "content": exec_result})
                else:
                    final_text = "Reached max function-call rounds."

        except Exception:
            log.exception("Subagent error")
            final_text = "I had trouble with that request. Please try again."

        final_text = sanitize_ai_output(final_text, user_message=prompt)

        embed = discord.Embed(title=f"🤖 {BOT_NAME} Subagent", color=COLOR_OK if edit_log else BOT_COLOR)
        embed.add_field(name="Request", value=prompt[:1024], inline=False)
        if final_text:
            embed.add_field(name="Summary", value=final_text[:1024], inline=False)
        if edit_log:
            log_text = "\n".join(f"• {e}" for e in edit_log)
            if len(log_text) > 1024:
                log_text = log_text[:1020] + "…"
            embed.add_field(name=f"Edit Log ({len(edit_log)} actions)", value=log_text, inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

        if edit_log:
            log_lines = "\n".join(f"• {e}" for e in edit_log)
            await log_action(self.bot, "🤖 Subagent Actions", f"**Owner:** {interaction.user.mention}\n**Request:** {prompt[:200]}\n\n**Edit Log:**\n{log_lines[:1500]}", color=COLOR_OK)

    @app_commands.command(name="changelog", description="View the live subagent changelog.")
    async def changelog_cmd(self, interaction: discord.Interaction) -> None:
        entries = _load_changelog()
        if not entries:
            await interaction.response.send_message("No changelog entries yet.", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Subagent Changelog", description=f"Last {min(len(entries), 25)} of {len(entries)} actions", color=BOT_COLOR)
        for e in entries[-25:]:
            icon = "✅" if e["status"] == "ok" else "❌"
            embed.add_field(name=f"{icon} {e['action']} — {e['ts']}", value=e["detail"][:200], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Subagent(bot))
