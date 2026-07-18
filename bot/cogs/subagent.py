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
from data_store import (
    check_subagent_rate_limit,
    get_always_allow_deletes,
    add_action_history,
    create_ticket_panel,
    create_application, add_application_submission, list_applications,
    create_giveaway, end_giveaway, list_giveaways,
    set_autorole, get_autorole,
    set_welcome, get_welcome,
    set_suggestion_channel,
    set_verification, get_verification,
    save_snapshot, list_snapshots, get_snapshot,
    set_automation, list_automation, remove_automation,
    add_scheduled_action, list_scheduled_actions, remove_scheduled_action,
)
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

## DUPLICATE DETECTION — CRITICAL
1. Before creating ANY new channel, role, category, or event, the system AUTOMATICALLY checks for existing ones with similar names. If one exists, it reuses it instead of creating a duplicate.
2. You should NOT try to check for duplicates yourself — just call the create function and the system handles it.
3. When asked to "go to general and send a message", just call send_message with channel_name="general". If no channel is specified, the system will use the channel where the command was run.
4. When asked to create something that already exists, the system will reuse it and tell you in the result. Just proceed with the next steps (e.g. setting permissions, sending messages).

## CHANNEL_NAME OPTIONAL
For send_message, send_embed, send_poll, send_button_embed, send_game, send_gif: if the user does NOT specify a channel, omit channel_name entirely. The system will use the current channel automatically. Only include channel_name when the user explicitly names a channel.

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
22. When asked for interactive content, use send_poll for polls, send_button_embed for embeds with action buttons, or send_game for mini-games. Available game types: trivia, trivia_quiz, would_you_rather, this_or_that, rock_paper_scissors, number_guess, word_scramble, emoji_guess.
23. When setting up a channel for a role, set permissions for @everyone (deny view_channel), then for the specific role (allow view_channel, send_messages, read_message_history, etc.), AND for any moderator/admin roles (allow all).
24. Listen carefully to what the user asks. If they say "only @role can see it", deny view_channel for @everyone and allow it only for that role. If they say "@role can talk but not send images", allow send_messages but deny attach_files and embed_links.
25. When the user asks to "set up permissions" or "fix permissions" for a channel, set permissions for ALL relevant roles: @everyone, the target role, and any staff roles (Moderator, Admin).
26. When asked to make a "private" channel, deny view_channel for @everyone and allow it only for specified roles.
27. When asked to make a "read-only" channel, deny send_messages for @everyone but allow view_channel and read_message_history.
28. Use list_channels, list_roles, or list_events to research the server structure before making changes when you need to know what already exists.
29. Use web_search to look up current information on any topic before acting when the user asks a factual question or needs research.
30. Use wikipedia_lookup for detailed factual summaries about people, places, concepts, or events.
31. Use delete_message to delete any message by ID. The system will handle it if the bot has permission.
32. When creating channels, the system automatically sets @everyone permissions (view, send, read history for text; view, connect, speak for voice). You only need to call set_channel_permissions if you want to RESTRICT access further.
33. When polishing channels, add emoji decorations to names and topics. Make announcements visually appealing with rich embeds (colors, fields, thumbnails, footers). Use send_gif to add visual flair.
34. When the user says "go to general and send a message", call send_message with channel_name="general". If they don't specify a channel at all, omit channel_name and the system uses the current channel.
35. You can create complete support systems: ticket panels (create_ticket_panel), application forms (create_application_form), giveaways (start_giveaway), verification systems (set_verification), auto-role (set_autorole), welcome/goodbye messages (set_welcome), suggestion channels (set_suggestion_channel), and automation triggers (set_automation).
36. You can save and restore server snapshots: save_server_snapshot captures the full server structure, restore_server_snapshot rebuilds missing channels/roles/categories from a snapshot.
37. You can schedule recurring messages: add_scheduled_action posts a message on a specific day of the week at a given time (UTC), or daily. Use list_scheduled to see them and remove_scheduled to delete one.
38. You can set automation triggers: set_automation with trigger="member_join" to automatically give a role and/or send a message when someone joins, or trigger="member_leave" for when someone leaves.
39. You can toggle always-allow-deletes with set_always_allow_deletes. When ON, delete confirmations are bypassed — channels, roles, and events are deleted immediately without asking. Use this when the user says "always allow deletes" or "don't ask me to confirm deletions".
40. When the user asks to "create a complete support system" or "set up my server", chain multiple calls together: create categories, channels, roles, ticket panels, verification, welcome messages, and automation all in one request.
41. All actions are recorded in action history automatically, enabling the /undo-last command to reverse the last create/delete action.

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


# ── Tool declarations (Gemini) ───────────────────────────────────────────────

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
        decl("create_role", "Create a new role with specific permissions. If no permissions specified, the system auto-detects role type and sets appropriate permissions.", {
            "name": {"type": "string", "description": "Role name e.g. 'Moderator', 'VIP', 'YouTuber'"},
            "color": {"type": "string", "description": "Hex color e.g. '#FF0000' for red, '#5865F2' for blurple, '#2B2D31' for dark"},
            "hoist": {"type": "boolean", "description": "Display separately in member list (default false). Set true for VIP/Moderator roles"},
            "mentionable": {"type": "boolean", "description": "Allow @mention (default false). Set true for most roles"},
            "permissions": {"type": "string", "description": "Comma-separated permission flags. If omitted, system auto-detects from role name."},
        }, ["name"]),
        decl("set_channel_permissions", "Set permission overwrites for a channel. Use to restrict channel access to specific roles. For @everyone role, use role_name='@everyone'. Always set @everyone FIRST, then specific roles, then staff roles.", {
            "channel_name": {"type": "string", "description": "Channel name to set permissions on"},
            "role_name": {"type": "string", "description": "Role name. Use '@everyone' for the everyone role"},
            "allow": {"type": "string", "description": "Comma-separated permission flags to ALLOW e.g. 'view_channel,send_messages,read_message_history'. Leave empty for none"},
            "deny": {"type": "string", "description": "Comma-separated permission flags to DENY e.g. 'view_channel'. Leave empty for none"},
        }, ["channel_name", "role_name"]),
        decl("send_message", "Send a plain text message to a channel. If channel_name is omitted, sends in the current channel. Content is filtered for rule-breaking content.", {
            "channel_name": {"type": "string", "description": "Target channel name (without #). If omitted, uses current channel."},
            "content": {"type": "string", "description": "Message content. Can include emoji decoration. Slurs and hate speech are blocked."},
        }, ["content"]),
        decl("send_embed", "Send a rich embed to a channel. If channel_name is omitted, sends in the current channel. Use for announcements, welcome messages, rules, etc. Make it visually appealing.", {
            "channel_name": {"type": "string", "description": "Target channel name (without #). If omitted, uses current channel."},
            "title": {"type": "string", "description": "Embed title with emoji e.g. '🌟 Welcome to the Server!'"},
            "description": {"type": "string", "description": "Embed body text. Can be multi-line with formatting"},
            "color": {"type": "string", "description": "Hex color e.g. '#FF0000' (red), '#23A55A' (green), '#5865F2' (blurple), '#F0B132' (gold), '#9B59B6' (purple), '#E91E63' (pink)"},
            "footer": {"type": "string", "description": "Optional: footer text e.g. 'Vyrion Subagent'"},
            "image_url": {"type": "string", "description": "Optional: banner image URL"},
            "thumbnail_url": {"type": "string", "description": "Optional: small thumbnail image URL (top-right)"},
            "author_name": {"type": "string", "description": "Optional: author name shown at top of embed"},
            "author_icon_url": {"type": "string", "description": "Optional: author avatar icon URL"},
            "field_name": {"type": "string"}, "field_value": {"type": "string"}, "field_inline": {"type": "boolean"},
            "field2_name": {"type": "string"}, "field2_value": {"type": "string"}, "field2_inline": {"type": "boolean"},
            "field3_name": {"type": "string"}, "field3_value": {"type": "string"}, "field3_inline": {"type": "boolean"},
            "timestamp": {"type": "boolean", "description": "Optional: set to true to show current timestamp"},
        }, ["title", "description"]),
        decl("send_poll", "Send an interactive poll with buttons. If channel_name is omitted, sends in the current channel. Users click to vote. Results shown after duration.", {
            "channel_name": {"type": "string", "description": "Target channel name. If omitted, uses current channel."},
            "question": {"type": "string", "description": "Poll question with emoji e.g. '🎮 What game should we play?'"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "2-5 poll options as strings"},
            "duration_minutes": {"type": "integer", "description": "Optional: poll duration in minutes (default 60)"},
        }, ["question", "options"]),
        decl("send_button_embed", "Send an embed with interactive buttons. If channel_name is omitted, sends in the current channel. Use for role selection, confirmation prompts, etc.", {
            "channel_name": {"type": "string", "description": "Target channel name. If omitted, uses current channel."},
            "title": {"type": "string", "description": "Embed title"},
            "description": {"type": "string", "description": "Embed description"},
            "color": {"type": "string", "description": "Hex color"},
            "buttons": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string", "description": "Button label"},
                "style": {"type": "string", "description": "Button style: 'primary' (blurple), 'success' (green), 'danger' (red), 'secondary' (grey)"},
                "custom_id": {"type": "string", "description": "Unique ID for this button e.g. 'role_gamer'"},
                "emoji": {"type": "string", "description": "Optional: emoji for the button"},
            }}, "description": "Array of 1-5 button objects"},
        }, ["title", "description", "buttons"]),
        decl("send_game", "Send a mini-game embed that users can play by clicking buttons. If channel_name is omitted, sends in the current channel. Game types: trivia, trivia_quiz, would_you_rather, this_or_that, rock_paper_scissors, number_guess, word_scramble, emoji_guess.", {
            "channel_name": {"type": "string", "description": "Target channel name. If omitted, uses current channel."},
            "game_type": {"type": "string", "description": "Game type: 'trivia', 'trivia_quiz', 'would_you_rather', 'this_or_that', 'rock_paper_scissors', 'number_guess', 'word_scramble', 'emoji_guess'"},
            "question": {"type": "string", "description": "Game question or prompt"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Game options/choices"},
        }, ["game_type", "question"]),
        decl("delete_message", "Delete a message by message ID from a channel. Can delete any bot message or messages from users (if bot has manage_messages permission).", {
            "message_id": {"type": "string", "description": "The message ID to delete"},
            "channel_name": {"type": "string", "description": "Channel name where the message is. If omitted, uses current channel."},
        }, ["message_id"]),
        decl("list_channels", "List all channels in the server with their types and categories. Useful for researching server structure before making changes.", {}, []),
        decl("list_roles", "List all roles in the server with their permissions and positions. Useful for researching role structure.", {}, []),
        decl("list_events", "List all scheduled events in the server. Useful for checking existing events before creating new ones.", {}, []),
        decl("web_search", "Search the web for current information. Use for researching topics, finding facts, or looking up information before acting. Returns top 5 result snippets.", {
            "query": {"type": "string", "description": "Search query"},
        }, ["query"]),
        decl("wikipedia_lookup", "Look up a topic on Wikipedia and get a summary. Use for factual research about people, places, concepts, etc.", {
            "query": {"type": "string", "description": "Topic to look up"},
        }, ["query"]),
        decl("add_role_to_user", "Assign a role to a user.", {
            "role_name": {"type": "string"}, "user": {"type": "string"},
        }, ["role_name", "user"]),
        decl("remove_role_from_user", "Remove a role from a user.", {
            "role_name": {"type": "string"}, "user": {"type": "string"},
        }, ["role_name", "user"]),
        decl("rename_channel", "Rename a channel.", {
            "current_name": {"type": "string"}, "new_name": {"type": "string"},
        }, ["current_name", "new_name"]),
        decl("set_slowmode", "Set slowmode on a text channel.", {
            "channel_name": {"type": "string"}, "seconds": {"type": "integer", "description": "Slowmode seconds (0-21600)"},
        }, ["channel_name", "seconds"]),
        decl("set_channel_topic", "Set the topic of a text channel.", {
            "channel_name": {"type": "string"}, "topic": {"type": "string"},
        }, ["channel_name", "topic"]),
        decl("delete_channel", "Delete a channel by name. User will be asked to confirm unless always-allow-deletes is on.", {
            "channel_name": {"type": "string"},
        }, ["channel_name"]),
        decl("create_scheduled_event", "Create a scheduled event in the server.", {
            "name": {"type": "string"}, "description": {"type": "string"},
            "start_time": {"type": "string", "description": "ISO 8601 format e.g. 2025-01-15T20:00:00"},
            "end_time": {"type": "string"}, "channel_name": {"type": "string"}, "location": {"type": "string"},
        }, ["name", "start_time"]),
        decl("edit_scheduled_event", "Edit an existing scheduled event.", {
            "event_name": {"type": "string"}, "new_name": {"type": "string"},
            "new_description": {"type": "string"}, "new_start_time": {"type": "string"},
        }, ["event_name"]),
        decl("delete_scheduled_event", "Delete a scheduled event by name. User will be asked to confirm unless always-allow-deletes is on.", {
            "event_name": {"type": "string"},
        }, ["event_name"]),
        decl("create_forum_channel", "Create a forum channel.", {
            "name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"},
        }, ["name"]),
        decl("create_announcement_channel", "Create an announcement channel.", {
            "name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"},
        }, ["name"]),
        decl("create_stage_channel", "Create a stage channel.", {
            "name": {"type": "string"}, "category": {"type": "string"},
        }, ["name"]),
        decl("create_invite", "Create an invite for a channel.", {
            "channel_name": {"type": "string"}, "max_age": {"type": "integer"}, "max_uses": {"type": "integer"},
        }, ["channel_name"]),
        decl("kick_member", "Kick a member from the server.", {
            "user": {"type": "string"}, "reason": {"type": "string"},
        }, ["user"]),
        decl("ban_member", "Ban a member from the server.", {
            "user": {"type": "string"}, "reason": {"type": "string"},
        }, ["user"]),
        decl("timeout_member", "Timeout a member.", {
            "user": {"type": "string"}, "minutes": {"type": "integer"}, "reason": {"type": "string"},
        }, ["user", "minutes"]),
        decl("send_dm", "Send a DM to a server member.", {
            "user": {"type": "string"}, "content": {"type": "string"},
        }, ["user", "content"]),
        decl("edit_role", "Edit an existing role's permissions, color, or name.", {
            "role_name": {"type": "string"}, "new_name": {"type": "string"}, "color": {"type": "string"},
            "permissions": {"type": "string"}, "hoist": {"type": "boolean"}, "mentionable": {"type": "boolean"},
        }, ["role_name"]),
        decl("delete_role", "Delete a role by name. User will be asked to confirm unless always-allow-deletes is on.", {
            "role_name": {"type": "string"},
        }, ["role_name"]),
        decl("reorder_channel", "Move a channel to a different category or reorder it.", {
            "channel_name": {"type": "string"}, "category": {"type": "string"}, "position": {"type": "integer"},
        }, ["channel_name"]),
        decl("read_logs", "Read recent bot logs. Optional level filter (INFO/WARNING/ERROR). Returns last N entries.", {
            "count": {"type": "integer"}, "level": {"type": "string"},
        }, []),
        decl("edit_bot_message", "Edit a message previously sent by the bot. Provide the message ID and new content.", {
            "message_id": {"type": "string"}, "channel_name": {"type": "string"}, "new_content": {"type": "string"},
        }, ["message_id", "channel_name", "new_content"]),
        decl("send_gif", "Search and send a GIF. If channel_name is omitted, sends in the current channel. Use for adding visual flair to announcements and messages.", {
            "channel_name": {"type": "string", "description": "Target channel name. If omitted, uses current channel."}, "query": {"type": "string"},
        }, ["query"]),
        decl("create_ticket_panel", "Create a ticket panel with category buttons in a channel. Users click a button to open a private ticket channel.", {
            "channel_name": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
            "categories": {"type": "array", "items": {"type": "string"}, "description": "1-5 category names"},
        }, ["channel_name", "title", "description", "categories"]),
        decl("set_autorole", "Configure auto-role: automatically assign roles when a member joins.", {
            "enabled": {"type": "boolean"}, "role_names": {"type": "array", "items": {"type": "string"}},
        }, ["enabled", "role_names"]),
        decl("set_welcome", "Configure welcome/goodbye messages. Placeholders: {user}, {server}, {count}.", {
            "enabled": {"type": "boolean"}, "channel_name": {"type": "string"}, "message": {"type": "string"},
            "goodbye_channel_name": {"type": "string"}, "goodbye_message": {"type": "string"},
        }, ["enabled", "channel_name", "message"]),
        decl("set_suggestion_channel", "Set the channel where suggestions are posted.", {"channel_name": {"type": "string"}}, ["channel_name"]),
        decl("create_application_form", "Create an application form with questions. Users click a button to fill it out.", {
            "name": {"type": "string"}, "description": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "string"}}, "channel_name": {"type": "string"},
        }, ["name", "description", "questions", "channel_name"]),
        decl("start_giveaway", "Start a giveaway with a button to enter. Winners auto-picked at end.", {
            "channel_name": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
            "prize": {"type": "string"}, "duration_minutes": {"type": "integer"}, "winners": {"type": "integer"},
        }, ["channel_name", "title", "prize", "duration_minutes", "winners"]),
        decl("set_verification", "Set up verification: users click a button to get a role.", {
            "enabled": {"type": "boolean"}, "role_name": {"type": "string"}, "channel_name": {"type": "string"}, "message": {"type": "string"},
        }, ["enabled", "role_name", "channel_name", "message"]),
        decl("save_server_snapshot", "Save a snapshot of server structure (channels, roles, categories).", {"name": {"type": "string"}}, ["name"]),
        decl("restore_server_snapshot", "Restore channels and roles from a snapshot. Only creates missing items.", {"snapshot_id": {"type": "string"}}, ["snapshot_id"]),
        decl("list_snapshots", "List all saved server snapshots.", {}, []),
        decl("set_automation", "Set automation trigger: perform actions when events happen (member_join/member_leave).", {
            "trigger": {"type": "string", "description": "'member_join' or 'member_leave'"},
            "role_name": {"type": "string", "description": "Role to assign (member_join). 'none' to skip"},
            "message": {"type": "string"}, "channel_name": {"type": "string"},
        }, ["trigger", "message"]),
        decl("add_scheduled_action", "Schedule a recurring message (e.g. post every Friday).", {
            "channel_name": {"type": "string"}, "content": {"type": "string"},
            "day": {"type": "string", "description": "monday...sunday or 'daily'"},
            "hour": {"type": "integer"}, "minute": {"type": "integer"},
        }, ["channel_name", "content", "day", "hour", "minute"]),
        decl("list_scheduled", "List all scheduled recurring actions.", {}, []),
        decl("remove_scheduled", "Remove a scheduled action by ID.", {"sched_id": {"type": "string"}}, ["sched_id"]),
        decl("set_always_allow_deletes", "Toggle bypassing delete confirmations. When ON, channels/roles/events delete immediately without asking.", {
            "enabled": {"type": "boolean"},
        }, ["enabled"]),
    ])]


# ── Tool declarations (OpenAI/text) ───────────────────────────────────────────

def _build_tools_json() -> list[dict]:
    def t(name, desc, props, required):
        return {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": required}}
    return [
        t("create_text_channel", "Create a new text channel.", {"name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"}}, ["name"]),
        t("create_voice_channel", "Create a new voice channel.", {"name": {"type": "string"}, "category": {"type": "string"}, "user_limit": {"type": "integer"}}, ["name"]),
        t("create_category", "Create a new channel category.", {"name": {"type": "string"}}, ["name"]),
        t("create_role", "Create a new role with permissions. Auto-detects role type if permissions omitted.", {"name": {"type": "string"}, "color": {"type": "string"}, "hoist": {"type": "boolean"}, "mentionable": {"type": "boolean"}, "permissions": {"type": "string"}}, ["name"]),
        t("set_channel_permissions", "Set permission overwrites for a channel. Use '@everyone' for everyone role.", {"channel_name": {"type": "string"}, "role_name": {"type": "string"}, "allow": {"type": "string"}, "deny": {"type": "string"}}, ["channel_name", "role_name"]),
        t("send_message", "Send a plain text message. If channel_name omitted, uses current channel.", {"channel_name": {"type": "string"}, "content": {"type": "string"}}, ["content"]),
        t("send_embed", "Send a rich embed. If channel_name omitted, uses current channel.", {"channel_name": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "color": {"type": "string"}, "footer": {"type": "string"}, "image_url": {"type": "string"}, "thumbnail_url": {"type": "string"}, "author_name": {"type": "string"}, "author_icon_url": {"type": "string"}, "field_name": {"type": "string"}, "field_value": {"type": "string"}, "field_inline": {"type": "boolean"}, "field2_name": {"type": "string"}, "field2_value": {"type": "string"}, "field2_inline": {"type": "boolean"}, "field3_name": {"type": "string"}, "field3_value": {"type": "string"}, "field3_inline": {"type": "boolean"}, "timestamp": {"type": "boolean"}}, ["title", "description"]),
        t("send_poll", "Send an interactive poll. If channel_name omitted, uses current channel.", {"channel_name": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "duration_minutes": {"type": "integer"}}, ["question", "options"]),
        t("send_button_embed", "Send an embed with interactive buttons. If channel_name omitted, uses current channel.", {"channel_name": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "color": {"type": "string"}, "buttons": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "style": {"type": "string"}, "custom_id": {"type": "string"}, "emoji": {"type": "string"}}}}}, ["title", "description", "buttons"]),
        t("send_game", "Send a mini-game embed. If channel_name omitted, uses current channel. Types: trivia, trivia_quiz, would_you_rather, this_or_that, rock_paper_scissors, number_guess, word_scramble, emoji_guess.", {"channel_name": {"type": "string"}, "game_type": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, ["game_type", "question"]),
        t("delete_message", "Delete a message by ID.", {"message_id": {"type": "string"}, "channel_name": {"type": "string"}}, ["message_id"]),
        t("list_channels", "List all channels in the server.", {}, []),
        t("list_roles", "List all roles in the server.", {}, []),
        t("list_events", "List all scheduled events.", {}, []),
        t("web_search", "Search the web for current information.", {"query": {"type": "string"}}, ["query"]),
        t("wikipedia_lookup", "Look up a topic on Wikipedia.", {"query": {"type": "string"}}, ["query"]),
        t("add_role_to_user", "Assign a role to a user.", {"role_name": {"type": "string"}, "user": {"type": "string"}}, ["role_name", "user"]),
        t("remove_role_from_user", "Remove a role from a user.", {"role_name": {"type": "string"}, "user": {"type": "string"}}, ["role_name", "user"]),
        t("rename_channel", "Rename a channel.", {"current_name": {"type": "string"}, "new_name": {"type": "string"}}, ["current_name", "new_name"]),
        t("set_slowmode", "Set slowmode on a text channel.", {"channel_name": {"type": "string"}, "seconds": {"type": "integer"}}, ["channel_name", "seconds"]),
        t("set_channel_topic", "Set the topic of a text channel.", {"channel_name": {"type": "string"}, "topic": {"type": "string"}}, ["channel_name", "topic"]),
        t("delete_channel", "Delete a channel. User confirms unless always-allow-deletes is on.", {"channel_name": {"type": "string"}}, ["channel_name"]),
        t("create_scheduled_event", "Create a scheduled event.", {"name": {"type": "string"}, "description": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}, "channel_name": {"type": "string"}, "location": {"type": "string"}}, ["name", "start_time"]),
        t("edit_scheduled_event", "Edit a scheduled event.", {"event_name": {"type": "string"}, "new_name": {"type": "string"}, "new_description": {"type": "string"}, "new_start_time": {"type": "string"}}, ["event_name"]),
        t("delete_scheduled_event", "Delete a scheduled event. User confirms unless always-allow-deletes is on.", {"event_name": {"type": "string"}}, ["event_name"]),
        t("create_forum_channel", "Create a forum channel.", {"name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"}}, ["name"]),
        t("create_announcement_channel", "Create an announcement channel.", {"name": {"type": "string"}, "category": {"type": "string"}, "topic": {"type": "string"}}, ["name"]),
        t("create_stage_channel", "Create a stage channel.", {"name": {"type": "string"}, "category": {"type": "string"}}, ["name"]),
        t("create_invite", "Create an invite.", {"channel_name": {"type": "string"}, "max_age": {"type": "integer"}, "max_uses": {"type": "integer"}}, ["channel_name"]),
        t("kick_member", "Kick a member.", {"user": {"type": "string"}, "reason": {"type": "string"}}, ["user"]),
        t("ban_member", "Ban a member.", {"user": {"type": "string"}, "reason": {"type": "string"}}, ["user"]),
        t("timeout_member", "Timeout a member.", {"user": {"type": "string"}, "minutes": {"type": "integer"}, "reason": {"type": "string"}}, ["user", "minutes"]),
        t("send_dm", "Send a DM to a member.", {"user": {"type": "string"}, "content": {"type": "string"}}, ["user", "content"]),
        t("edit_role", "Edit an existing role.", {"role_name": {"type": "string"}, "new_name": {"type": "string"}, "color": {"type": "string"}, "permissions": {"type": "string"}, "hoist": {"type": "boolean"}, "mentionable": {"type": "boolean"}}, ["role_name"]),
        t("delete_role", "Delete a role. User confirms unless always-allow-deletes is on.", {"role_name": {"type": "string"}}, ["role_name"]),
        t("reorder_channel", "Move a channel to a different category.", {"channel_name": {"type": "string"}, "category": {"type": "string"}, "position": {"type": "integer"}}, ["channel_name"]),
        t("read_logs", "Read recent bot logs.", {"count": {"type": "integer"}, "level": {"type": "string"}}, []),
        t("edit_bot_message", "Edit a bot message.", {"message_id": {"type": "string"}, "channel_name": {"type": "string"}, "new_content": {"type": "string"}}, ["message_id", "channel_name", "new_content"]),
        t("send_gif", "Search and send a GIF. If channel_name omitted, uses current channel.", {"channel_name": {"type": "string"}, "query": {"type": "string"}}, ["query"]),
        t("create_ticket_panel", "Create a ticket panel with category buttons.", {"channel_name": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "categories": {"type": "array", "items": {"type": "string"}}}, ["channel_name", "title", "description", "categories"]),
        t("set_autorole", "Configure auto-role on join.", {"enabled": {"type": "boolean"}, "role_names": {"type": "array", "items": {"type": "string"}}}, ["enabled", "role_names"]),
        t("set_welcome", "Configure welcome/goodbye messages.", {"enabled": {"type": "boolean"}, "channel_name": {"type": "string"}, "message": {"type": "string"}, "goodbye_channel_name": {"type": "string"}, "goodbye_message": {"type": "string"}}, ["enabled", "channel_name", "message"]),
        t("set_suggestion_channel", "Set the suggestions channel.", {"channel_name": {"type": "string"}}, ["channel_name"]),
        t("create_application_form", "Create an application form.", {"name": {"type": "string"}, "description": {"type": "string"}, "questions": {"type": "array", "items": {"type": "string"}}, "channel_name": {"type": "string"}}, ["name", "description", "questions", "channel_name"]),
        t("start_giveaway", "Start a giveaway.", {"channel_name": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "prize": {"type": "string"}, "duration_minutes": {"type": "integer"}, "winners": {"type": "integer"}}, ["channel_name", "title", "prize", "duration_minutes", "winners"]),
        t("set_verification", "Set up verification system.", {"enabled": {"type": "boolean"}, "role_name": {"type": "string"}, "channel_name": {"type": "string"}, "message": {"type": "string"}}, ["enabled", "role_name", "channel_name", "message"]),
        t("save_server_snapshot", "Save server structure snapshot.", {"name": {"type": "string"}}, ["name"]),
        t("restore_server_snapshot", "Restore from snapshot.", {"snapshot_id": {"type": "string"}}, ["snapshot_id"]),
        t("list_snapshots", "List all snapshots.", {}, []),
        t("set_automation", "Set automation trigger.", {"trigger": {"type": "string"}, "role_name": {"type": "string"}, "message": {"type": "string"}, "channel_name": {"type": "string"}}, ["trigger", "message"]),
        t("add_scheduled_action", "Schedule a recurring message.", {"channel_name": {"type": "string"}, "content": {"type": "string"}, "day": {"type": "string"}, "hour": {"type": "integer"}, "minute": {"type": "integer"}}, ["channel_name", "content", "day", "hour", "minute"]),
        t("list_scheduled", "List scheduled actions.", {}, []),
        t("remove_scheduled", "Remove a scheduled action.", {"sched_id": {"type": "string"}}, ["sched_id"]),
        t("set_always_allow_deletes", "Toggle bypassing delete confirmations.", {"enabled": {"type": "boolean"}}, ["enabled"]),
    ]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hex_to_int(color_hex: str) -> int:
    c = (color_hex or "").lstrip("#")
    try:
        return int(c, 16)
    except (ValueError, TypeError):
        return 0x5865F2


def _find_channel(guild: discord.Guild, name: str) -> discord.abc.GuildChannel | None:
    clean = name.lower().lstrip("#").strip()
    for ch in guild.channels:
        if ch.name.lower() == clean:
            return ch
    channel_map = {ch.name.lower(): ch for ch in guild.channels}
    matches = difflib.get_close_matches(clean, list(channel_map.keys()), n=1, cutoff=0.6)
    if matches:
        return channel_map[matches[0]]
    return None


def _find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    clean = name.lower().lstrip("@").strip()
    if clean == "everyone":
        return guild.default_role
    for r in guild.roles:
        if r.name.lower() == clean:
            return r
    role_map = {r.name.lower(): r for r in guild.roles}
    matches = difflib.get_close_matches(clean, list(role_map.keys()), n=1, cutoff=0.6)
    if matches:
        return role_map[matches[0]]
    return None


def _find_similar_channel(guild: discord.Guild, name: str) -> discord.abc.GuildChannel | None:
    clean = name.lower().lstrip("#").strip()
    channel_map = {ch.name.lower(): ch for ch in guild.channels}
    if clean in channel_map:
        return channel_map[clean]
    matches = difflib.get_close_matches(clean, list(channel_map.keys()), n=1, cutoff=0.6)
    return channel_map[matches[0]] if matches else None


def _find_similar_role(guild: discord.Guild, name: str) -> discord.Role | None:
    clean = name.lower().lstrip("@").strip()
    if clean == "everyone":
        return guild.default_role
    role_map = {r.name.lower(): r for r in guild.roles}
    if clean in role_map:
        return role_map[clean]
    matches = difflib.get_close_matches(clean, list(role_map.keys()), n=1, cutoff=0.6)
    return role_map[matches[0]] if matches else None


def _find_event(guild: discord.Guild, name: str) -> discord.ScheduledEvent | None:
    clean = name.lower().strip()
    for e in guild.scheduled_events:
        if e.name.lower() == clean:
            return e
    event_map = {e.name.lower(): e for e in guild.scheduled_events}
    matches = difflib.get_close_matches(clean, list(event_map.keys()), n=1, cutoff=0.6)
    return event_map[matches[0]] if matches else None


GIPHY_BETA_KEY = "dc6zaTOxFJmzC"


async def _search_gif(query: str) -> str | None:
    url = f"https://api.giphy.com/v1/gifs/search?api_key={GIPHY_BETA_KEY}&q={query}&limit=1&rating=pg"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                gifs = data.get("data", [])
                if gifs:
                    return gifs[0].get("images", {}).get("original", {}).get("url")
    except Exception:
        return None
    return None


async def _web_search(query: str, max_results: int = 5) -> str:
    url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return "Search failed."
                html = await resp.text()
        snippets = re.findall(r'class="result__snippet">(.*?)</a>', html, re.DOTALL)
        results = []
        for s in snippets[:max_results]:
            clean = re.sub(r'<[^>]+>', '', s).strip()
            if clean:
                results.append(clean[:300])
        if not results:
            return "No results found."
        return f"Search results for '{query}':\n\n" + "\n\n---\n\n".join(f"{i+1}. {r}" for i, r in enumerate(results))
    except Exception:
        return "Search failed."


async def _wikipedia_lookup(query: str) -> str:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{query.replace(' ', '_')}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": "VyrionBot/1.0"}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return f"No Wikipedia article found for '{query}'."
                data = await resp.json()
                extract = data.get("extract", "")
                title = data.get("title", query)
                if extract:
                    return f"**{title}**\n\n{extract[:1000]}"
                return f"No summary available for '{query}'."
    except Exception:
        return "Wikipedia lookup failed."


def _read_recent_logs(count: int = 10, level: str = "") -> str:
    log_file = Path(__file__).parent / "data" / "recent_logs.json"
    if not log_file.exists():
        return "No log file found."
    try:
        data = json.loads(log_file.read_text())
        entries = data.get("entries", [])
        if level:
            entries = [e for e in entries if e.get("level", "") == level.upper()]
        entries = entries[-count:]
        if not entries:
            return "No log entries found."
        return "\n".join(f"[{e['ts']}] {e['level']}: {e['msg']}" for e in entries)
    except (json.JSONDecodeError, OSError):
        return "Failed to read logs."


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


# ── Interactive Views ──────────────────────────────────────────────────────────

class ConfirmView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.result: bool | None = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the requester can confirm.", ephemeral=True)
            return
        self.result = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the requester can cancel.", ephemeral=True)
            return
        self.result = False
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class PollView(discord.ui.View):
    def __init__(self, question: str, options: list[str], duration_minutes: int = 60):
        super().__init__(timeout=duration_minutes * 60)
        self.question = question
        self.votes: dict[str, list[str]] = {opt: [] for opt in options}
        self.user_votes: dict[int, str] = {}

        for i, opt in enumerate(options[:5]):
            emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"][i]
            btn = discord.ui.Button(label=opt[:80], style=discord.ButtonStyle.primary, emoji=emoji, custom_id=f"poll_{i}")
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

    def _make_callback(self, option: str):
        async def callback(interaction: discord.Interaction) -> None:
            uid = interaction.user.id
            if uid in self.user_votes:
                old = self.user_votes[uid]
                if old in self.votes:
                    self.votes[old].remove(uid)
            self.user_votes[uid] = option
            self.votes[option].append(uid)
            total = sum(len(v) for v in self.votes.values())
            desc_lines = []
            for opt, voters in self.votes.items():
                pct = (len(voters) / total * 100) if total else 0
                bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
                desc_lines.append(f"{bar} **{opt}** — {len(voters)} vote(s) ({pct:.0f}%)")
            embed = discord.Embed(title=f"📊 {self.question}", description="\n".join(desc_lines), color=BOT_COLOR)
            embed.set_footer(text=f"{total} total vote(s)")
            await interaction.response.edit_message(embed=embed, view=self)
        return callback

    async def on_timeout(self) -> None:
        total = sum(len(v) for v in self.votes.values())
        desc_lines = []
        for opt, voters in sorted(self.votes.items(), key=lambda x: len(x[1]), reverse=True):
            pct = (len(voters) / total * 100) if total else 0
            bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            desc_lines.append(f"{bar} **{opt}** — {len(voters)} vote(s) ({pct:.0f}%)")
        embed = discord.Embed(title=f"📊 {self.question} [POLL CLOSED]", description="\n".join(desc_lines), color=COLOR_OK)
        embed.set_footer(text=f"Final results — {total} total vote(s)")
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass


class ButtonEmbedView(discord.ui.View):
    def __init__(self, buttons: list[dict]):
        super().__init__(timeout=None)
        for btn_data in buttons[:5]:
            style_map = {
                "primary": discord.ButtonStyle.primary,
                "success": discord.ButtonStyle.success,
                "danger": discord.ButtonStyle.danger,
                "secondary": discord.ButtonStyle.secondary,
            }
            btn = discord.ui.Button(
                label=btn_data.get("label", "Click")[:80],
                style=style_map.get(btn_data.get("style", "primary"), discord.ButtonStyle.primary),
                custom_id=btn_data.get("custom_id", btn_data.get("label", "btn").lower().replace(" ", "_")),
                emoji=btn_data.get("emoji", "") or None,
            )
            btn.callback = self._make_callback(btn_data.get("custom_id", btn_data.get("label", "btn")))
            self.add_item(btn)

    def _make_callback(self, custom_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            if custom_id.startswith("role_"):
                role_name = custom_id[5:].replace("_", " ")
                role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), interaction.guild.roles)
                if role:
                    if role in interaction.user.roles:
                        await interaction.user.remove_roles(role)
                        await interaction.response.send_message(f"Removed role **@{role.name}**", ephemeral=True)
                    else:
                        await interaction.user.add_roles(role)
                        await interaction.response.send_message(f"Added role **@{role.name}**", ephemeral=True)
                else:
                    await interaction.response.send_message("Role not found.", ephemeral=True)
            else:
                await interaction.response.send_message(f"You clicked: {custom_id}", ephemeral=True)
        return callback


class GameView(discord.ui.View):
    def __init__(self, game_type: str, question: str, options: list[str]):
        super().__init__(timeout=300)
        self.game_type = game_type
        self.question = question
        self.options = options
        self.scores: dict[str, int] = {}
        self.target_number: int | None = None
        self.attempts: dict[str, int] = {}
        self.scrambled_word: str | None = None
        self.original_word: str | None = None

        if game_type == "number_guess" and options:
            try:
                self.target_number = int(options[0])
            except ValueError:
                self.target_number = 50
            btn = discord.ui.Button(label="Guess!", style=discord.ButtonStyle.primary, emoji="🔢", custom_id="game_guess")
            btn.callback = self._number_guess_callback
            self.add_item(btn)
        elif game_type == "word_scramble" and options:
            import random
            self.original_word = options[0]
            chars = list(self.original_word)
            random.shuffle(chars)
            self.scrambled_word = "".join(chars)
            btn = discord.ui.Button(label="Unscramble!", style=discord.ButtonStyle.primary, emoji="🔤", custom_id="game_unscramble")
            btn.callback = self._word_scramble_callback
            self.add_item(btn)
        elif game_type == "emoji_guess" and options:
            for i, opt in enumerate(options[:5]):
                emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"][i]
                btn = discord.ui.Button(label=opt[:80], style=discord.ButtonStyle.primary, emoji=emoji, custom_id=f"game_{i}")
                btn.callback = self._make_callback(i, opt)
                self.add_item(btn)
        else:
            for i, opt in enumerate(options[:5]):
                emoji = ["🇦", "🇧", "🇨", "🇩", "🇪"][i]
                btn = discord.ui.Button(label=opt[:80], style=discord.ButtonStyle.primary, emoji=emoji, custom_id=f"game_{i}")
                btn.callback = self._make_callback(i, opt)
                self.add_item(btn)

    def _make_callback(self, idx: int, option: str):
        async def callback(interaction: discord.Interaction) -> None:
            uid = str(interaction.user.id)
            if self.game_type in ("trivia", "trivia_quiz"):
                correct = idx == 0
                if uid not in self.scores:
                    self.scores[uid] = 0
                if correct:
                    self.scores[uid] += 1
                    await interaction.response.send_message(f"✅ Correct! You have {self.scores[uid]} point(s).", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ Wrong! The correct answer was: **{self.options[0]}**", ephemeral=True)
            elif self.game_type in ("would_you_rather", "this_or_that"):
                await interaction.response.send_message(f"You chose: **{option}**", ephemeral=True)
            elif self.game_type == "rock_paper_scissors":
                import random
                choices = ["rock", "paper", "scissors"]
                bot_choice = random.choice(choices)
                user_choice = option.lower()
                if user_choice == bot_choice:
                    result = "It's a tie!"
                elif (user_choice == "rock" and bot_choice == "scissors") or \
                     (user_choice == "paper" and bot_choice == "rock") or \
                     (user_choice == "scissors" and bot_choice == "paper"):
                    result = "You win!"
                else:
                    result = "You lose!"
                await interaction.response.send_message(f"You chose **{option}**, I chose **{bot_choice}**. {result}", ephemeral=True)
            elif self.game_type == "emoji_guess":
                correct = idx == 0
                if correct:
                    await interaction.response.send_message(f"✅ Correct! The answer was **{self.options[0]}**.", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ Nope! The answer was **{self.options[0]}**.", ephemeral=True)
            else:
                await interaction.response.send_message(f"You selected: {option}", ephemeral=True)
        return callback

    async def _number_guess_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NumberGuessModal(self))

    async def _word_scramble_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(WordScrambleModal(self))


class NumberGuessModal(discord.ui.Modal):
    def __init__(self, game_view: GameView):
        super().__init__(title="🔢 Number Guess")
        self.game_view = game_view
        self.add_item(discord.ui.TextInput(label="Enter your guess (1-100)", placeholder="e.g. 42", max_length=3))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            guess = int(self.children[0].value)
        except ValueError:
            await interaction.response.send_message("Please enter a valid number.", ephemeral=True)
            return
        target = self.game_view.target_number or 50
        uid = str(interaction.user.id)
        if uid not in self.game_view.attempts:
            self.game_view.attempts[uid] = 0
        self.game_view.attempts[uid] += 1
        if guess == target:
            await interaction.response.send_message(f"🎉 Correct! The number was **{target}**. You got it in {self.game_view.attempts[uid]} attempt(s)!", ephemeral=True)
        elif guess < target:
            await interaction.response.send_message(f"📈 Too low! Try again. (Attempt #{self.game_view.attempts[uid]})", ephemeral=True)
        else:
            await interaction.response.send_message(f"📉 Too high! Try again. (Attempt #{self.game_view.attempts[uid]})", ephemeral=True)


class WordScrambleModal(discord.ui.Modal):
    def __init__(self, game_view: GameView):
        super().__init__(title="🔤 Unscramble the Word")
        self.game_view = game_view
        self.add_item(discord.ui.TextInput(label=f"Unscramble: {game_view.scrambled_word}", placeholder="Type the unscrambled word", max_length=100))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guess = self.children[0].value.strip().lower()
        answer = (self.game_view.original_word or "").lower()
        if guess == answer:
            await interaction.response.send_message(f"🎉 Correct! The word was **{answer}**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Wrong! The word was **{answer}**.", ephemeral=True)


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

        status_embed = discord.Embed(title=f"🤖 {BOT_NAME} Subagent", color=BOT_COLOR)
        status_embed.add_field(name="Request", value=prompt[:1024], inline=False)
        status_embed.add_field(name="Status", value="⏳ Working on it...", inline=False)
        status_embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        status_msg = await interaction.followup.send(embed=status_embed, ephemeral=True)

        async def _update_live_log() -> None:
            embed = discord.Embed(title=f"🤖 {BOT_NAME} Subagent", color=BOT_COLOR)
            embed.add_field(name="Request", value=prompt[:1024], inline=False)
            if edit_log:
                log_text = "\n".join(f"• {e}" for e in edit_log)
                if len(log_text) > 1024:
                    log_text = log_text[:1020] + "…"
                embed.add_field(name=f"Edit Log ({len(edit_log)} actions)", value=log_text, inline=False)
            embed.add_field(name="Status", value="⏳ Still working..." if not edit_log else f"⏳ Executing... ({len(edit_log)} actions done)", inline=False)
            embed.set_footer(text=f"Requested by {interaction.user.display_name}")
            try:
                await status_msg.edit(embed=embed)
            except Exception:
                pass

        async def _confirm(prompt_text: str) -> bool:
            if await get_always_allow_deletes(guild.id):
                return True
            view = ConfirmView(interaction.user.id, timeout=30)
            await interaction.followup.send(prompt_text, view=view, ephemeral=True)
            await view.wait()
            return view.result is True

        channels_info = []
        for ch in guild.channels[:50]:
            ch_type = type(ch).__name__.replace("Channel", "").lower()
            perms_info = ""
            if hasattr(ch, "overwrites") and ch.overwrites:
                perm_roles = [r.name for r in ch.overwrites.keys() if hasattr(r, "name")]
                if perm_roles:
                    perms_info = f" [perms: {', '.join(perm_roles[:5])}]"
            channels_info.append(f"  - #{ch.name} ({ch_type})" + (f" in '{ch.category.name}'" if ch.category else "") + perms_info)
        roles_info = []
        for r in guild.roles[:30]:
            perms = []
            if r.permissions.administrator:
                perms.append("admin")
            if r.permissions.manage_messages:
                perms.append("manage_msgs")
            if r.permissions.kick_members:
                perms.append("kick")
            if r.permissions.ban_members:
                perms.append("ban")
            perm_str = f" [{', '.join(perms)}]" if perms else ""
            roles_info.append(f"  - @{r.name} (id:{r.id}, pos:{r.position}){perm_str}")
        context = f"Current server: {guild.name}\nChannels:\n" + "\n".join(channels_info) + "\nRoles:\n" + "\n".join(roles_info)

        async def _execute_function(name: str, args: dict) -> str:
            try:
                if name == "create_text_channel":
                    existing = _find_similar_channel(guild, args["name"])
                    if existing:
                        entry = f"Reused existing channel #{existing.name} (similar to '{args['name']}')"
                        edit_log.append(entry); _add_changelog_entry("create_text_channel", entry)
                        await _update_live_log()
                        return entry
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_text_channel(args["name"], category=category, topic=args.get("topic"))
                    try:
                        overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                        await ch.set_permissions(guild.default_role, overwrite=overwrite)
                    except discord.Forbidden:
                        pass
                    entry = f"Created text channel #{ch.name} (with default @everyone permissions)"
                    edit_log.append(entry); _add_changelog_entry("create_text_channel", entry)
                    await add_action_history(guild.id, "create_text_channel", entry, {"name": ch.name})
                    await _update_live_log()
                    return entry

                if name == "create_voice_channel":
                    existing = _find_similar_channel(guild, args["name"])
                    if existing:
                        entry = f"Reused existing channel 🔊 {existing.name} (similar to '{args['name']}')"
                        edit_log.append(entry); _add_changelog_entry("create_voice_channel", entry)
                        await _update_live_log()
                        return entry
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_voice_channel(args["name"], category=category, user_limit=args.get("user_limit", 0))
                    try:
                        overwrite = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
                        await ch.set_permissions(guild.default_role, overwrite=overwrite)
                    except discord.Forbidden:
                        pass
                    entry = f"Created voice channel 🔊 {ch.name} (with default @everyone permissions)"
                    edit_log.append(entry); _add_changelog_entry("create_voice_channel", entry)
                    await add_action_history(guild.id, "create_voice_channel", entry, {"name": ch.name})
                    await _update_live_log()
                    return entry

                if name == "create_category":
                    existing = discord.utils.get(guild.categories, name=args["name"])
                    if existing:
                        entry = f"Reused existing category 📁 {existing.name}"
                        edit_log.append(entry); _add_changelog_entry("create_category", entry)
                        await _update_live_log()
                        return entry
                    cat = await guild.create_category(args["name"])
                    entry = f"Created category 📁 {cat.name}"
                    edit_log.append(entry); _add_changelog_entry("create_category", entry)
                    await add_action_history(guild.id, "create_category", entry, {"name": cat.name})
                    await _update_live_log()
                    return entry

                if name == "create_role":
                    existing = _find_similar_role(guild, args["name"])
                    if existing:
                        entry = f"Reused existing role @{existing.name} (similar to '{args['name']}')"
                        edit_log.append(entry); _add_changelog_entry("create_role", entry)
                        await _update_live_log()
                        return entry
                    color = _hex_to_int(args.get("color", "#5865F2"))
                    perm_str = args.get("permissions", "")
                    if not perm_str:
                        perm_str = _get_role_perms(args["name"])
                    perm_bits = _parse_perms(perm_str)
                    role = await guild.create_role(
                        name=args["name"], color=discord.Color(color),
                        hoist=args.get("hoist", False), mentionable=args.get("mentionable", False),
                        permissions=discord.Permissions(perm_bits),
                    )
                    entry = f"Created role @{role.name} with permissions: {perm_str}"
                    edit_log.append(entry); _add_changelog_entry("create_role", entry)
                    await add_action_history(guild.id, "create_role", entry, {"name": role.name})
                    await _update_live_log()
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
                    await _update_live_log()
                    return entry

                if name == "send_message":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, discord.TextChannel):
                            return f"Channel '{ch_name}' not found or not a text channel."
                    else:
                        ch = interaction.channel
                    content = _filter_content(args["content"])
                    if content is None:
                        return "Message blocked: content violates server rules."
                    content = _safe_content(content)
                    await ch.send(content[:2000])
                    entry = f"Sent message in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_message", entry)
                    await _update_live_log()
                    return entry

                if name == "send_embed":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, discord.TextChannel):
                            return f"Channel '{ch_name}' not found or not a text channel."
                    else:
                        ch = interaction.channel
                    title = args["title"]
                    desc = args.get("description", "")
                    if _filter_content(title) is None or _filter_content(desc) is None:
                        return "Embed blocked: content violates server rules."
                    embed = discord.Embed(
                        title=title[:256], description=desc[:4096],
                        color=discord.Color(_hex_to_int(args.get("color", "#5865F2"))),
                    )
                    if args.get("footer"):
                        embed.set_footer(text=args["footer"][:2048])
                    if args.get("image_url"):
                        embed.set_image(url=args["image_url"])
                    if args.get("thumbnail_url"):
                        embed.set_thumbnail(url=args["thumbnail_url"])
                    if args.get("author_name"):
                        embed.set_author(name=args["author_name"][:256], icon_url=args.get("author_icon_url", "") or None)
                    for i in range(1, 4):
                        fn = args.get(f"field{i}_name")
                        fv = args.get(f"field{i}_value")
                        if fn and fv:
                            embed.add_field(name=fn[:256], value=fv[:1024], inline=args.get(f"field{i}_inline", False))
                    if args.get("timestamp"):
                        embed.timestamp = discord.utils.utcnow()
                    await ch.send(embed=embed)
                    entry = f"Sent embed '{args['title']}' in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_embed", entry)
                    await _update_live_log()
                    return entry

                if name == "send_poll":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, discord.TextChannel):
                            return f"Channel '{ch_name}' not found."
                    else:
                        ch = interaction.channel
                    question = args["question"]
                    options = args.get("options", [])
                    if len(options) < 2 or len(options) > 5:
                        return "Poll needs 2-5 options."
                    duration = args.get("duration_minutes", 60)
                    view = PollView(question, options, duration)
                    embed = discord.Embed(title=f"📊 {question}", color=BOT_COLOR)
                    for i, opt in enumerate(options):
                        embed.add_field(name=f"{['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣'][i]} {opt}", value="0 votes (0%)", inline=False)
                    embed.set_footer(text=f"Poll ends in {duration} minutes")
                    msg = await ch.send(embed=embed, view=view)
                    view.message = msg
                    entry = f"Sent poll '{question}' in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_poll", entry)
                    await _update_live_log()
                    return entry

                if name == "send_button_embed":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, discord.TextChannel):
                            return f"Channel '{ch_name}' not found."
                    else:
                        ch = interaction.channel
                    buttons = args.get("buttons", [])
                    if not buttons or len(buttons) > 5:
                        return "Need 1-5 buttons."
                    view = ButtonEmbedView(buttons)
                    embed = discord.Embed(
                        title=args["title"][:256], description=args.get("description", "")[:4096],
                        color=discord.Color(_hex_to_int(args.get("color", "#5865F2"))),
                    )
                    embed.set_footer(text="Click a button below")
                    await ch.send(embed=embed, view=view)
                    entry = f"Sent button embed '{args['title']}' in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_button_embed", entry)
                    await _update_live_log()
                    return entry

                if name == "send_game":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, discord.TextChannel):
                            return f"Channel '{ch_name}' not found."
                    else:
                        ch = interaction.channel
                    game_type = args.get("game_type", "trivia")
                    question = args["question"]
                    options = args.get("options", [])
                    view = GameView(game_type, question, options)
                    game_emoji = {
                        "trivia": "🧠", "trivia_quiz": "🧠", "would_you_rather": "🤔",
                        "this_or_that": "⚡", "rock_paper_scissors": "✂️",
                        "number_guess": "🔢", "word_scramble": "🔤", "emoji_guess": "🎭",
                    }.get(game_type, "🎮")
                    embed = discord.Embed(title=f"{game_emoji} {question}", color=BOT_COLOR)
                    embed.description = f"Game: {game_type.replace('_', ' ').title()}\nClick a button to play!"
                    if game_type in ("trivia", "trivia_quiz") and options:
                        for i, opt in enumerate(options):
                            embed.add_field(name=f"{['🇦','🇧','🇨','🇩','🇪'][i]} {opt}", value="\u200b", inline=False)
                    elif game_type == "word_scramble" and view.scrambled_word:
                        embed.description = f"Game: Word Scramble\nUnscramble: **{view.scrambled_word}**\nClick the button to submit your answer!"
                    elif game_type == "number_guess":
                        embed.description = "Game: Number Guess\nI'm thinking of a number between 1-100. Click the button to guess!"
                    elif game_type == "emoji_guess" and options:
                        embed.description = f"Game: Emoji Guess\nWhat does this emoji represent? Click your answer!"
                    elif game_type in ("would_you_rather", "this_or_that") and options:
                        for i, opt in enumerate(options):
                            embed.add_field(name=f"{['🇦','🇧','🇨','🇩','🇪'][i]} {opt}", value="\u200b", inline=False)
                    embed.set_footer(text="Click a button to play!")
                    await ch.send(embed=embed, view=view)
                    entry = f"Sent {game_type} game in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_game", entry)
                    await _update_live_log()
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
                    await _update_live_log()
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
                    await _update_live_log()
                    return entry

                if name == "rename_channel":
                    ch = _find_channel(guild, args["current_name"])
                    if not ch:
                        return f"Channel '{args['current_name']}' not found."
                    old = ch.name
                    await ch.edit(name=args["new_name"])
                    entry = f"Renamed #{old} → #{args['new_name']}"
                    edit_log.append(entry); _add_changelog_entry("rename_channel", entry)
                    await _update_live_log()
                    return entry

                if name == "set_slowmode":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    await ch.edit(slowmode_delay=args["seconds"])
                    entry = f"Set slowmode in #{ch.name} to {args['seconds']}s"
                    edit_log.append(entry); _add_changelog_entry("set_slowmode", entry)
                    await _update_live_log()
                    return entry

                if name == "set_channel_topic":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    await ch.edit(topic=args["topic"])
                    entry = f"Set topic of #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("set_channel_topic", entry)
                    await _update_live_log()
                    return entry

                if name == "delete_channel":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch:
                        return f"Channel '{args['channel_name}' not found."
                    chname = ch.name
                    if not await _confirm(f"Delete channel #{chname}?"):
                        return f"Cancelled deletion of #{chname}."
                    await ch.delete()
                    entry = f"Deleted channel #{chname}"
                    edit_log.append(entry); _add_changelog_entry("delete_channel", entry)
                    await add_action_history(guild.id, "delete_channel", entry)
                    await _update_live_log()
                    return entry

                if name == "create_scheduled_event":
                    existing = _find_event(guild, args["name"])
                    if existing:
                        entry = f"Reused existing event '{existing.name}' (similar to '{args['name']}')"
                        edit_log.append(entry); _add_changelog_entry("create_scheduled_event", entry)
                        await _update_live_log()
                        return entry
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
                    await add_action_history(guild.id, "create_scheduled_event", entry, {"name": event.name})
                    await _update_live_log()
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
                    await _update_live_log()
                    return entry

                if name == "delete_scheduled_event":
                    event = discord.utils.find(lambda e: e.name.lower() == args["event_name"].lower(), guild.scheduled_events)
                    if not event:
                        return f"Event '{args['event_name']}' not found."
                    if not await _confirm(f"Delete event '{event.name}'?"):
                        return f"Cancelled deletion of event '{event.name}'."
                    await event.delete()
                    entry = f"Deleted event '{args['event_name']}'"
                    edit_log.append(entry); _add_changelog_entry("delete_scheduled_event", entry)
                    await add_action_history(guild.id, "delete_scheduled_event", entry)
                    await _update_live_log()
                    return entry

                if name == "create_forum_channel":
                    existing = _find_similar_channel(guild, args["name"])
                    if existing:
                        entry = f"Reused existing channel #{existing.name} (similar to '{args['name']}')"
                        edit_log.append(entry); _add_changelog_entry("create_forum_channel", entry)
                        await _update_live_log()
                        return entry
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_forum_channel(args["name"], category=category, topic=args.get("topic"))
                    entry = f"Created forum channel #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_forum_channel", entry)
                    await _update_live_log()
                    return entry

                if name == "create_announcement_channel":
                    existing = _find_similar_channel(guild, args["name"])
                    if existing:
                        entry = f"Reused existing channel #{existing.name} (similar to '{args['name']}')"
                        edit_log.append(entry); _add_changelog_entry("create_announcement_channel", entry)
                        await _update_live_log()
                        return entry
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_text_channel(args["name"], category=category, topic=args.get("topic"), news=True)
                    entry = f"Created announcement channel #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_announcement_channel", entry)
                    await _update_live_log()
                    return entry

                if name == "create_stage_channel":
                    existing = _find_similar_channel(guild, args["name"])
                    if existing:
                        entry = f"Reused existing channel 🔭 {existing.name} (similar to '{args['name']}')"
                        edit_log.append(entry); _add_changelog_entry("create_stage_channel", entry)
                        await _update_live_log()
                        return entry
                    category = discord.utils.get(guild.categories, name=args["category"]) if args.get("category") else None
                    ch = await guild.create_stage_channel(args["name"], category=category)
                    entry = f"Created stage channel {ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_stage_channel", entry)
                    await _update_live_log()
                    return entry

                if name == "create_invite":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
                        return f"Channel '{args['channel_name']}' not found."
                    invite = await ch.create_invite(max_age=args.get("max_age", 0), max_uses=args.get("max_uses", 0))
                    entry = f"Created invite {invite.url} for #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_invite", entry)
                    await _update_live_log()
                    return entry

                if name == "kick_member":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    if member.id == BOT_OWNER_ID or member.id == guild.owner_id:
                        return "Cannot kick the server owner or bot owner."
                    if guild.me.top_role <= member.top_role:
                        return "Cannot kick a member with equal or higher role."
                    await member.kick(reason=args.get("reason", "Subagent action"))
                    entry = f"Kicked {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("kick_member", entry)
                    await _update_live_log()
                    return entry

                if name == "ban_member":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        uid = int(args["user"]) if args["user"].isdigit() else None
                        if uid:
                            if uid == BOT_OWNER_ID or uid == guild.owner_id:
                                return "Cannot ban the server owner or bot owner."
                            await guild.ban(discord.Object(id=uid), reason=args.get("reason", "Subagent action"))
                            entry = f"Banned user ID {uid}"
                            edit_log.append(entry); _add_changelog_entry("ban_member", entry)
                            await _update_live_log()
                            return entry
                        return f"User '{args['user']}' not found."
                    if member.id == BOT_OWNER_ID or member.id == guild.owner_id:
                        return "Cannot ban the server owner or bot owner."
                    if guild.me.top_role <= member.top_role:
                        return "Cannot ban a member with equal or higher role."
                    await member.ban(reason=args.get("reason", "Subagent action"), delete_message_days=0)
                    entry = f"Banned {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("ban_member", entry)
                    await _update_live_log()
                    return entry

                if name == "timeout_member":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    if member.id == BOT_OWNER_ID or member.id == guild.owner_id:
                        return "Cannot timeout the server owner or bot owner."
                    if guild.me.top_role <= member.top_role:
                        return "Cannot timeout a member with equal or higher role."
                    until = discord.utils.utcnow() + datetime.timedelta(minutes=args["minutes"])
                    await member.timeout(until, reason=args.get("reason", "Subagent action"))
                    entry = f"Timed out {member.display_name} for {args['minutes']}m"
                    edit_log.append(entry); _add_changelog_entry("timeout_member", entry)
                    await _update_live_log()
                    return entry

                if name == "send_dm":
                    member = await _find_member(guild, args["user"])
                    if not member:
                        return f"User '{args['user']}' not found."
                    content = _filter_content(args["content"])
                    if content is None:
                        return "DM blocked: content violates server rules."
                    content = _safe_content(content)
                    dm = await member.create_dm()
                    await dm.send(content[:2000])
                    entry = f"Sent DM to {member.display_name}"
                    edit_log.append(entry); _add_changelog_entry("send_dm", entry)
                    await _update_live_log()
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
                    await _update_live_log()
                    return entry

                if name == "delete_role":
                    role = _find_role(guild, args["role_name"])
                    if not role:
                        return f"Role '{args['role_name']}' not found."
                    rname = role.name
                    if not await _confirm(f"Delete role @{rname}?"):
                        return f"Cancelled deletion of role @{rname}."
                    await role.delete()
                    entry = f"Deleted role @{rname}"
                    edit_log.append(entry); _add_changelog_entry("delete_role", entry)
                    await add_action_history(guild.id, "delete_role", entry)
                    await _update_live_log()
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
                    await _update_live_log()
                    return entry

                if name == "read_logs":
                    count = args.get("count", 10)
                    level = args.get("level", "")
                    logs = _read_recent_logs(count, level)
                    entry = f"Read {count} log entries"
                    edit_log.append(entry); _add_changelog_entry("read_logs", entry)
                    await _update_live_log()
                    return logs[:1500]

                if name == "edit_bot_message":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, discord.TextChannel):
                            return f"Channel '{ch_name}' not found."
                    else:
                        ch = interaction.channel
                    try:
                        msg = await ch.fetch_message(int(args["message_id"]))
                    except (discord.NotFound, ValueError):
                        return f"Message {args['message_id']} not found in #{ch.name}."
                    if msg.author.id != self.bot.user.id:
                        return "I can only edit my own messages."
                    content = _filter_content(args["new_content"])
                    if content is None:
                        return "Edit blocked: content violates server rules."
                    content = _safe_content(content)
                    await msg.edit(content=content[:2000])
                    entry = f"Edited message {args['message_id']} in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("edit_bot_message", entry)
                    await _update_live_log()
                    return entry

                if name == "send_gif":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, (discord.TextChannel, discord.Thread)):
                            return f"Channel '{ch_name}' not found."
                    else:
                        ch = interaction.channel
                    gif_url = await _search_gif(args["query"])
                    if not gif_url:
                        return f"No GIF found for '{args['query']}'."
                    embed = discord.Embed(title=f"🎬 {args['query']}", color=BOT_COLOR)
                    embed.set_image(url=gif_url)
                    await ch.send(embed=embed)
                    entry = f"Sent GIF '{args['query']}' to #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("send_gif", entry)
                    await _update_live_log()
                    return entry

                if name == "delete_message":
                    ch_name = args.get("channel_name")
                    if ch_name:
                        ch = _find_channel(guild, ch_name)
                        if not ch or not isinstance(ch, discord.TextChannel):
                            return f"Channel '{ch_name}' not found."
                    else:
                        ch = interaction.channel
                    try:
                        msg = await ch.fetch_message(int(args["message_id"]))
                    except (discord.NotFound, ValueError):
                        return f"Message {args['message_id']} not found in #{ch.name}."
                    await msg.delete()
                    entry = f"Deleted message {args['message_id']} in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("delete_message", entry)
                    await _update_live_log()
                    return entry

                if name == "list_channels":
                    lines = []
                    for cat in guild.categories:
                        lines.append(f"📁 {cat.name}")
                        for ch in cat.text_channels:
                            lines.append(f"  # {ch.name} (text)")
                        for ch in cat.voice_channels:
                            lines.append(f"  🔊 {ch.name} (voice)")
                        for ch in cat.forums:
                            lines.append(f"  💬 {ch.name} (forum)")
                    for ch in guild.text_channels:
                        if ch.category is None:
                            lines.append(f"# {ch.name} (text, no category)")
                    for ch in guild.voice_channels:
                        if ch.category is None:
                            lines.append(f"🔊 {ch.name} (voice, no category)")
                    return "\n".join(lines)[:1500]

                if name == "list_roles":
                    lines = []
                    for r in guild.roles:
                        if r.is_default():
                            lines.append(f"@everyone (id:{r.id})")
                            continue
                        perms = []
                        if r.permissions.administrator:
                            perms.append("admin")
                        if r.permissions.manage_messages:
                            perms.append("manage_msgs")
                        if r.permissions.kick_members:
                            perms.append("kick")
                        if r.permissions.ban_members:
                            perms.append("ban")
                        if r.permissions.manage_roles:
                            perms.append("manage_roles")
                        if r.permissions.manage_channels:
                            perms.append("manage_channels")
                        perm_str = f" [{', '.join(perms)}]" if perms else ""
                        lines.append(f"@{r.name} (id:{r.id}, pos:{r.position}, color:#{r.color.value:06X}){perm_str}")
                    return "\n".join(lines)[:1500]

                if name == "list_events":
                    lines = []
                    for e in guild.scheduled_events:
                        status = str(e.status).split('.')[-1] if e.status else "unknown"
                        lines.append(f"📅 {e.name} (status:{status}, starts:{e.start_time.isoformat() if e.start_time else 'N/A'})")
                    if not lines:
                        return "No scheduled events found."
                    return "\n".join(lines)[:1500]

                if name == "web_search":
                    result = await _web_search(args["query"])
                    entry = f"Searched web for '{args['query']}'"
                    edit_log.append(entry); _add_changelog_entry("web_search", entry)
                    await _update_live_log()
                    return result[:1500]

                if name == "wikipedia_lookup":
                    result = await _wikipedia_lookup(args["query"])
                    entry = f"Looked up '{args['query']}' on Wikipedia"
                    edit_log.append(entry); _add_changelog_entry("wikipedia_lookup", entry)
                    await _update_live_log()
                    return result[:1500]

                if name == "create_ticket_panel":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found."
                    cats = args.get("categories", [])
                    if not cats or len(cats) > 5:
                        return "Need 1-5 categories."
                    import secrets
                    panel_id = secrets.token_hex(3)
                    from .systems import TicketPanelView
                    await data_store.create_ticket_panel(guild.id, panel_id, ch.id, args["title"], args["description"], cats)
                    embed = discord.Embed(title=args["title"], description=args["description"], color=BOT_COLOR)
                    embed.set_footer(text=f"Panel ID: {panel_id}")
                    view = TicketPanelView(panel_id, cats)
                    msg = await ch.send(embed=embed, view=view)
                    await data_store.set_panel_message_id(guild.id, panel_id, msg.id)
                    entry = f"Created ticket panel '{args['title']}' in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_ticket_panel", entry)
                    await add_action_history(guild.id, "create_ticket_panel", entry, {"panel_id": panel_id})
                    await _update_live_log()
                    return entry

                if name == "set_autorole":
                    role_ids = []
                    for rn in args.get("role_names", []):
                        r = _find_role(guild, rn)
                        if r:
                            role_ids.append(r.id)
                    await data_store.set_autorole(guild.id, args["enabled"], role_ids)
                    entry = f"Set autorole: enabled={args['enabled']}, {len(role_ids)} roles"
                    edit_log.append(entry); _add_changelog_entry("set_autorole", entry)
                    await add_action_history(guild.id, "set_autorole", entry)
                    await _update_live_log()
                    return entry

                if name == "set_welcome":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch:
                        return f"Channel '{args['channel_name']}' not found."
                    gch_id = None
                    if args.get("goodbye_channel_name"):
                        gch = _find_channel(guild, args["goodbye_channel_name"])
                        gch_id = gch.id if gch else None
                    await data_store.set_welcome(guild.id, args["enabled"], ch.id, args["message"], gch_id or ch.id, args.get("goodbye_message", ""))
                    entry = f"Set welcome/goodbye in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("set_welcome", entry)
                    await add_action_history(guild.id, "set_welcome", entry)
                    await _update_live_log()
                    return entry

                if name == "set_suggestion_channel":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch:
                        return f"Channel '{args['channel_name']}' not found."
                    await data_store.set_suggestion_channel(guild.id, ch.id)
                    entry = f"Set suggestion channel to #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("set_suggestion_channel", entry)
                    await _update_live_log()
                    return entry

                if name == "create_application_form":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found."
                    qs = args.get("questions", [])
                    if not qs or len(qs) > 5:
                        return "Need 1-5 questions."
                    import secrets
                    app_id = secrets.token_hex(3)
                    await data_store.create_application(guild.id, app_id, args["name"], args["description"], qs, ch.id)
                    from .systems import ApplicationView
                    embed = discord.Embed(title=f"📝 {args['name']}", description=args["description"], color=BOT_COLOR)
                    embed.add_field(name="Questions", value="\n".join(f"{i+1}. {q}" for i, q in enumerate(qs)), inline=False)
                    embed.set_footer(text=f"App ID: {app_id}")
                    view = ApplicationView(app_id, qs)
                    msg = await ch.send(embed=embed, view=view)
                    await data_store.set_application_message_id(guild.id, app_id, msg.id)
                    entry = f"Created application form '{args['name']}' in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("create_application_form", entry)
                    await _update_live_log()
                    return entry

                if name == "start_giveaway":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found."
                    import secrets
                    gid = secrets.token_hex(3)
                    gw = await data_store.create_giveaway(guild.id, gid, ch.id, args["title"], args.get("description", ""), args["prize"], args["duration_minutes"], args.get("winners", 1))
                    from .systems import GiveawayView
                    embed = discord.Embed(title=f"🎉 {args['title']}", description=args.get("description", ""), color=COLOR_OK)
                    embed.add_field(name="Prize", value=args["prize"], inline=False)
                    embed.add_field(name="Winners", value=str(args.get("winners", 1)), inline=True)
                    embed.add_field(name="Ends", value=f"<t:{gw['end_ts']}:R>", inline=True)
                    embed.set_footer(text=f"Giveaway ID: {gid}")
                    view = GiveawayView(gid)
                    msg = await ch.send(embed=embed, view=view)
                    await data_store.set_giveaway_message_id(guild.id, gid, msg.id)
                    entry = f"Started giveaway '{args['title']}' in #{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("start_giveaway", entry)
                    await _update_live_log()
                    return entry

                if name == "set_verification":
                    role = _find_role(guild, args["role_name"])
                    if not role:
                        return f"Role '{args['role_name']}' not found."
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found."
                    await data_store.set_verification(guild.id, args["enabled"], role.id, ch.id, args["message"])
                    if args["enabled"]:
                        from .systems import VerificationView
                        embed = discord.Embed(title="✅ Verification", description=args["message"], color=COLOR_OK)
                        embed.set_footer(text="Click the button below to verify")
                        await ch.send(embed=embed, view=VerificationView())
                    entry = f"Set verification: role=@{role.name}, channel=#{ch.name}"
                    edit_log.append(entry); _add_changelog_entry("set_verification", entry)
                    await _update_live_log()
                    return entry

                if name == "save_server_snapshot":
                    snap_data: dict = {"categories": [], "channels": [], "roles": []}
                    for cat in guild.categories:
                        snap_data["categories"].append({"name": cat.name, "position": cat.position})
                    for ch in guild.channels:
                        snap_data["channels"].append({
                            "name": ch.name, "type": type(ch).__name__,
                            "category": ch.category.name if ch.category else None,
                            "topic": getattr(ch, "topic", None), "position": ch.position,
                        })
                    for r in guild.roles:
                        if r.is_default():
                            continue
                        snap_data["roles"].append({
                            "name": r.name, "color": f"#{r.color.value:06X}", "hoist": r.hoist,
                            "mentionable": r.mentionable, "position": r.position, "permissions": r.permissions.value,
                        })
                    import secrets
                    sid = secrets.token_hex(3)
                    await data_store.save_snapshot(guild.id, sid, args["name"], snap_data)
                    entry = f"Saved snapshot '{args['name']}' ({len(snap_data['channels'])} channels, {len(snap_data['roles'])} roles)"
                    edit_log.append(entry); _add_changelog_entry("save_server_snapshot", entry)
                    await _update_live_log()
                    return entry

                if name == "restore_server_snapshot":
                    snap = await data_store.get_snapshot(guild.id, args["snapshot_id"])
                    if not snap:
                        return f"Snapshot '{args['snapshot_id']}' not found."
                    data = snap["data"]
                    created = 0
                    for r in sorted(data.get("roles", []), key=lambda x: x.get("position", 0)):
                        if not discord.utils.get(guild.roles, name=r["name"]):
                            try:
                                await guild.create_role(name=r["name"], color=discord.Color(int(r["color"].lstrip("#"), 16)), hoist=r["hoist"], mentionable=r["mentionable"], permissions=discord.Permissions(r["permissions"]))
                                created += 1
                            except Exception:
                                pass
                    for c in data.get("categories", []):
                        if not discord.utils.get(guild.categories, name=c["name"]):
                            try:
                                await guild.create_category(c["name"])
                                created += 1
                            except Exception:
                                pass
                    for ch in data.get("channels", []):
                        if not discord.utils.get(guild.channels, name=ch["name"]):
                            cat = discord.utils.get(guild.categories, name=ch["category"]) if ch.get("category") else None
                            try:
                                if ch["type"] == "TextChannel":
                                    await guild.create_text_channel(ch["name"], category=cat, topic=ch.get("topic"))
                                elif ch["type"] == "VoiceChannel":
                                    await guild.create_voice_channel(ch["name"], category=cat)
                                elif ch["type"] == "ForumChannel":
                                    await guild.create_forum_channel(ch["name"], category=cat)
                                elif ch["type"] == "StageChannel":
                                    await guild.create_stage_channel(ch["name"], category=cat)
                                created += 1
                            except Exception:
                                pass
                    entry = f"Restored {created} items from snapshot '{snap['name']}'"
                    edit_log.append(entry); _add_changelog_entry("restore_server_snapshot", entry)
                    await _update_live_log()
                    return entry

                if name == "list_snapshots":
                    snaps = await data_store.list_snapshots(guild.id)
                    if not snaps:
                        return "No snapshots found."
                    lines = []
                    for sid, s in snaps.items():
                        ts = datetime.datetime.fromtimestamp(s["ts"]).strftime("%Y-%m-%d %H:%M")
                        lines.append(f"📸 {s['name']} (ID: {sid}, {ts})")
                    return "\n".join(lines)[:1500]

                if name == "set_automation":
                    trigger = args["trigger"]
                    if trigger not in ("member_join", "member_leave"):
                        return "Trigger must be 'member_join' or 'member_leave'."
                    role_id = None
                    if args.get("role_name") and args["role_name"].lower() != "none":
                        r = _find_role(guild, args["role_name"])
                        role_id = r.id if r else None
                    ch_id = None
                    if args.get("channel_name"):
                        ch = _find_channel(guild, args["channel_name"])
                        ch_id = ch.id if ch else None
                    await data_store.set_automation(guild.id, trigger, {"role_id": role_id, "message": args["message"], "channel_id": ch_id})
                    entry = f"Set automation: {trigger}"
                    edit_log.append(entry); _add_changelog_entry("set_automation", entry)
                    await _update_live_log()
                    return entry

                if name == "add_scheduled_action":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found."
                    day = args["day"].lower().strip()
                    valid = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "daily"]
                    if day not in valid:
                        return f"Invalid day. Use: {', '.join(valid)}"
                    import secrets
                    sid = secrets.token_hex(3)
                    await data_store.add_scheduled_action(guild.id, sid, ch.id, args["content"], day, args["hour"], args["minute"])
                    entry = f"Scheduled message in #{ch.name} on {day} at {args['hour']:02d}:{args['minute']:02d}"
                    edit_log.append(entry); _add_changelog_entry("add_scheduled_action", entry)
                    await _update_live_log()
                    return entry

                if name == "list_scheduled":
                    entries = await data_store.list_scheduled_actions(guild.id)
                    if not entries:
                        return "No scheduled actions."
                    lines = []
                    for e in entries:
                        lines.append(f"⏰ {e['id']}: {e['day']} at {e['hour']:02d}:{e['minute']:02d} UTC in <#{e['channel_id']}> — {e['content'][:80]}")
                    return "\n".join(lines)[:1500]

                if name == "remove_scheduled":
                    ok = await data_store.remove_scheduled_action(guild.id, args["sched_id"])
                    entry = f"Removed scheduled action {args['sched_id']}" if ok else f"Scheduled action {args['sched_id']} not found"
                    edit_log.append(entry); _add_changelog_entry("remove_scheduled", entry)
                    await _update_live_log()
                    return entry

                if name == "set_always_allow_deletes":
                    await data_store.set_always_allow_deletes(guild.id, args["enabled"])
                    entry = f"Always-allow-deletes is now {'ON' if args['enabled'] else 'OFF'}"
                    edit_log.append(entry); _add_changelog_entry("set_always_allow_deletes", entry)
                    await _update_live_log()
                    return entry

                return f"Unknown function: {name}"
            except discord.Forbidden:
                msg = f"Missing permissions for: {name}"
                edit_log.append(f"[FAILED] {msg}")
                _add_changelog_entry(name, msg, "failed")
                await _update_live_log()
                return msg
            except Exception:
                msg = f"Error in {name}"
                edit_log.append(f"[FAILED] {msg}")
                _add_changelog_entry(name, msg, "failed")
                log.exception("Subagent function error: %s", name)
                await _update_live_log()
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
        try:
            await status_msg.edit(embed=embed)
        except Exception:
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
