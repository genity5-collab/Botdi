"""
Subagent Cog — /subagent slash command.

Uses multi-provider AI with function calling to let users describe
Discord actions in natural language. The AI decides which functions
to call — create channels with permission overwrites, roles with
specific permissions, rich embeds, categories, events, interactive
components, and more.

Permissions:
  - Bot owner: infinite usage
  - Guild owner (server owner): 5 uses per week
  - Administrators: NOT allowed

API errors are never shown to the user.
"""

from __future__ import annotations

import asyncio
import datetime
import difflib
import json
import logging
import re
from pathlib import Path

import aiohttp
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


# ── Content filtering ──────────────────────────────────────────────────────────

_BANNED_PATTERNS = [
    re.compile(r"n[i1]gg[ae]r", re.I),
    re.compile(r"f[a@]g[gq]ot", re.I),
    re.compile(r"tr[a@]nn[yi]e?", re.I),
    re.compile(r"r[e3]t[a@]rd", re.I),
    re.compile(r"ch[i1]nk", re.I),
    re.compile(r"sp[i1]c", re.I),
    re.compile(r"k[i1]k[e3]", re.I),
    re.compile(r"cracker", re.I),
    re.compile(r"w[e3]tb[a@]ck", re.I),
    re.compile(r"g[o0][o0]k", re.I),
]

_MASS_MENTION_RE = re.compile(r"@everyone|@here", re.I)

def _filter_content(content: str) -> str | None:
    """Return filtered content, or None if it violates rules."""
    if not content:
        return content
    for pattern in _BANNED_PATTERNS:
        if pattern.search(content):
            return None
    return content


def _safe_content(content: str) -> str:
    """Strip mass mentions from content."""
    return _MASS_MENTION_RE.sub("", content).strip()


SUBAGENT_SYSTEM = """You are Vyrion Subagent, an expert Discord server manager AI. You execute Discord actions by calling functions.

## CRITICAL RULES
1. ALWAYS call functions to execute actions. NEVER just describe what you would do.
2. When a user mentions a role like @youtuber or @moderator, use that EXACT role name in the function call.
3. When asked to restrict a channel to specific roles, use set_channel_permissions to DENY access for @everyone and ALLOW access for the specified role. ALWAYS set @everyone permissions FIRST, then set each role's permissions.
4. When asked to create a channel for a specific role, create the channel AND set permissions in the same request.
5. Call multiple functions at once when actions are independent. Call them in sequence when one depends on another.
6. Make things look professional — add emojis to channel names, topics, and embeds when appropriate.
7. Use rich embeds (with colors, fields, footers, thumbnails) instead of plain messages when announcing something.
8. When creating roles, set ALL appropriate permissions based on the role type:
   - Moderator: manage_messages, kick_members, timeout_members, manage_channels, view_audit_log, manage_roles, ban_members, manage_threads, moderate_members
   - Admin: administrator
   - VIP/VIP+: mentionable=True, hoist=True, priority=1, view_channel, send_messages, read_message_history, embed_links, attach_files, add_reactions, use_external_emojis, connect, speak, stream
   - Muted: send_messages=False (deny), add_reactions=False (deny), connect=False (deny), speak=False (deny)
   - Member: view_channel, send_messages, read_message_history, add_reactions, connect, speak
   - YouTuber: view_channel, send_messages, read_message_history, embed_links, attach_files, add_reactions, use_external_emojis
   When asked to set up permissions for a role, ALWAYS include ALL relevant permissions. Don't just set one or two.
9. When creating categories, organize channels logically (e.g. "Information" category for rules, announcements).
10. NEVER mention API errors, provider names, model names, or internal system details.
11. If something goes wrong internally, just say 'I had trouble with that action.'
12. Do not ask for confirmation. Do not explain what you're about to do. Just call the functions.
13. After all functions are called, give a brief summary with emoji decorations. Keep summaries under 75 words.
14. Before creating a new channel or role, the system automatically checks for existing ones with similar names and reuses them. Do not worry about duplicates.
15. When you need to delete something (channel, role, event), just call the delete function — the system will automatically ask the user for confirmation before executing.
16. You can read recent bot logs by calling read_logs to diagnose issues or check server activity.
17. You can edit your own previously sent messages by calling edit_bot_message with the message ID.
18. You can send GIFs by calling send_gif with a search query to add visual flair to announcements and messages.
19. When the user asks to create a channel without specifying a type, the system will ask them whether they want a normal text, forum, announcement, voice, or stage channel.
20. Use send_gif alongside send_embed or send_message to make announcements more engaging when appropriate.
21. NEVER send messages that contain slurs, hate speech, or rule-breaking content. The system will block such content automatically.
22. When asked for interactive content, use send_poll for polls, send_button_embed for embeds with action buttons, or send_game for mini-games.
23. When setting up a channel for a role, set permissions for @everyone (deny view_channel), then for the specific role (allow view_channel, send_messages, read_message_history, etc.), AND for any moderator/admin roles (allow all).
24. Listen carefully to what the user asks. If they say "only @role can see it", deny view_channel for @everyone and allow it only for that role. If they say "@role can talk but not send images", allow send_messages but deny attach_files and embed_links.
25. When the user asks to "set up permissions" or "fix permissions" for a channel, set permissions for ALL relevant roles: @everyone, the target role, and any staff roles (Moderator, Admin).
26. When asked to make a "private" channel, deny view_channel for @everyone and allow it only for specified roles.
27. When asked to make a "read-only" channel, deny send_messages for @everyone but allow view_channel and read_message_history.

## EXAMPLES

User: "Create a channel for @youtuber role only to chat there"
You should:
1. Call create_text_channel with name="youtuber-chat", topic="💬 Exclusive chat for YouTubers"
2. Call set_channel_permissions with channel_name="youtuber-chat", role_name="@everyone", deny="view_channel"
3. Call set_channel_permissions with channel_name="youtuber-chat", role_name="youtuber", allow="view_channel,send_messages,read_message_history,add_reactions,attach_files,embed_links,use_external_emojis"
4. Call set_channel_permissions with channel_name="youtuber-chat", role_name="Moderator", allow="view_channel,send_messages,read_message_history,manage_messages,manage_threads"

User: "Create a moderator role with proper permissions"
You should:
1. Call create_role with name="Moderator", color="#2B2D31", hoist=true, mentionable=true, permissions="manage_messages,kick_members,ban_members,timeout_members,moderate_members,manage_channels,manage_roles,view_audit_log,manage_threads,send_messages,read_message_history,view_channel,embed_links,attach_files,connect,speak,move_members,mute_members,deafen_members"

User: "Send a welcome embed in #general"
You should:
1. Call send_embed with channel_name="general", title="🌟 Welcome to the Server!", description="We're glad to have you here. Check out the rules and enjoy your stay!", color="#5865F2", footer="Vyrion Subagent", thumbnail_url=""

User: "Set up a server with rules and announcements"
You should:
1. Call create_category with name="Information"
2. Call create_text_channel with name="rules", category="Information", topic="📜 Server Rules"
3. Call create_text_channel with name="announcements", category="Information", topic="📢 Server Announcements"
4. Call create_text_channel with name="general", category="Information", topic="💬 General Chat"
5. Call set_channel_permissions with channel_name="rules", role_name="@everyone", allow="view_channel,read_message_history", deny="send_messages"
6. Call set_channel_permissions with channel_name="announcements", role_name="@everyone", allow="view_channel,read_message_history", deny="send_messages"
7. Call send_embed in rules with the rules content
8. Call send_embed in announcements welcoming everyone

User: "Make #announcements read-only"
You should:
1. Call set_channel_permissions with channel_name="announcements", role_name="@everyone", allow="view_channel,read_message_history", deny="send_messages,create_public_threads,create_private_threads"
2. Call set_channel_permissions with channel_name="announcements", role_name="Moderator", allow="view_channel,send_messages,read_message_history,manage_messages,manage_threads"

User: "Create a poll asking what game to play"
You should:
1. Call send_poll with channel_name="general", question="🎮 What game should we play?", options=["Minecraft", "Valorant", "Among Us", "League of Legends"], duration_minutes=60

User: "Send a button embed for role selection"
You should:
1. Call send_button_embed with channel_name="roles", title="🎭 Select Your Roles", description="Click a button below to get that role!", buttons=[{"label":"🎮 Gamer","style":"success","custom_id":"role_gamer"},{"label":"🎨 Artist","style":"primary","custom_id":"role_artist"},{"label":"🎵 Music Lover","style":"secondary","custom_id":"role_music"}]

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

MAX_ROUNDS = 25

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
    if not perm_str:
        return 0
    bits = 0
    for flag in perm_str.split(","):
        flag = flag.strip().lower()
        if flag in _PERM_FLAGS:
            bits |= _PERM_FLAGS[flag]
    return bits


# ── Role permission presets ───────────────────────────────────────────────────

_ROLE_PRESETS: dict[str, str] = {
    "moderator": "manage_messages,kick_members,ban_members,timeout_members,moderate_members,manage_channels,manage_roles,view_audit_log,manage_threads,send_messages,read_message_history,view_channel,embed_links,attach_files,connect,speak,move_members,mute_members,deafen_members",
    "admin": "administrator",
    "muted": "",
    "vip": "view_channel,send_messages,read_message_history,embed_links,attach_files,add_reactions,use_external_emojis,connect,speak,stream,change_nickname",
    "member": "view_channel,send_messages,read_message_history,add_reactions,connect,speak,change_nickname",
    "youtuber": "view_channel,send_messages,read_message_history,embed_links,attach_files,add_reactions,use_external_emojis",
    "bot": "view_channel,send_messages,read_message_history,embed_links,attach_files,add_reactions,use_external_emojis,manage_messages,manage_threads",
}

def _get_role_perms(role_name: str, explicit_perms: str = "") -> str:
    """Return permissions for a role, using presets if no explicit perms given."""
    if explicit_perms:
        return explicit_perms
    lower = role_name.lower()
    for key, perms in _ROLE_PRESETS.items():
        if key in lower:
            return perms
    return "view_channel,send_messages,read_message_history"
