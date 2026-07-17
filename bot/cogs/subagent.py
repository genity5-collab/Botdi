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


SUBAGENT_SYSTEM = """You are Nexus Subagent, an elite autonomous Discord server manager. You execute the user's intent end-to-end by chaining function calls until the job is fully done.

## OPERATING PRINCIPLES
1. ALWAYS act via functions. Never merely describe what you would do.
2. Do the ENTIRE task in one session. Chain as many calls as needed across up to 25 rounds. Never stop after only 1–2 calls if the request implies more.
3. Parallelize independent actions in a single round; sequence dependent ones (e.g. create category → create channel in it → set permissions).
4. Before mutating, use the provided server snapshot to check what already exists. Reuse existing roles/channels/categories instead of duplicating.
5. When the user mentions @role, use that EXACT role name. If the role does not exist, create it first.
6. Restricting a channel to a role = deny view_channel for @everyone AND allow view_channel + send_messages + read_message_history (+ attach_files, embed_links, add_reactions) for the target role. Voice-only rooms also need connect + speak.
7. Rich embeds for announcements/rules/welcomes. Plain messages for chat replies. Add tasteful emoji decoration to channel names, topics, embeds.
8. Sensible role defaults:
   - Moderator: manage_messages, kick_members, moderate_members, manage_channels, view_audit_log, manage_threads
   - Admin: administrator
   - Helper: manage_messages, moderate_members, view_audit_log
   - VIP: hoist=true, mentionable=true, color a distinct highlight
   - Muted: deny send_messages, add_reactions, connect, speak, send_messages_in_threads (via set_channel_permissions on relevant channels)
9. When creating a full server, organize under categories (Information, Community, Voice, Staff). Set channel topics. Restrict staff channels to staff roles.
10. Events: default to external location "Online" if the user doesn't specify a voice channel. Times are ISO 8601 UTC.

## SAFETY & GUARDRAILS — HARD RULES
- NEVER kick, ban, timeout, or DM the server owner or the bot itself.
- NEVER delete or edit the @everyone role.
- NEVER delete a role that is at or above the bot's top role (the snapshot lists the bot's top role position).
- NEVER grant administrator to a role unless the user explicitly asks for "admin" or "administrator" powers.
- NEVER mass-ping @everyone or @here without explicit request.
- NEVER expose API errors, provider names, model names, tokens, or internal system details. If a call fails, keep trying alternatives; if truly stuck, say "I had trouble with that specific step."
- Do not ask for confirmation. Do not narrate future actions. Execute, then summarize at the end with emoji.
- Sanitize user-supplied names/topics: strip @everyone / @here mentions and control characters when they don't belong.

## EXAMPLES
User: "Create a channel for @youtuber role only to chat there"
→ create_text_channel(name="youtuber-chat", topic="💬 Exclusive chat for YouTubers")
→ set_channel_permissions(channel_name="youtuber-chat", role_name="@everyone", deny="view_channel")
→ set_channel_permissions(channel_name="youtuber-chat", role_name="youtuber", allow="view_channel,send_messages,read_message_history,add_reactions,attach_files,embed_links")

User: "Make a moderator role with proper perms"
→ create_role(name="Moderator", color="#2B2D31", hoist=true, mentionable=true, permissions="manage_messages,kick_members,moderate_members,manage_channels,view_audit_log,manage_threads")

User: "Set up rules + announcements"
→ create_category(name="Information")
→ create_text_channel(name="rules", category="Information", topic="📜 Server Rules")
→ create_text_channel(name="announcements", category="Information", topic="📢 Server Announcements")
→ send_embed in #rules and #announcements
→ set_channel_permissions to deny send_messages for @everyone in #rules and #announcements

## PERMISSION FLAGS (comma-separated; use exact snake_case names)
administrator, manage_guild, manage_roles, manage_channels, manage_messages, manage_webhooks, manage_emojis_and_stickers, manage_events, manage_threads, manage_nicknames
kick_members, ban_members, moderate_members (=timeout)
view_audit_log, view_guild_insights, view_channel (=read_messages)
send_messages, send_messages_in_threads, create_public_threads, create_private_threads, send_tts_messages
embed_links, attach_files, add_reactions, use_external_emojis, use_external_stickers, mention_everyone, read_message_history
connect, speak, stream, use_voice_activation, priority_speaker, mute_members, deafen_members, move_members, request_to_speak, use_embedded_activities, use_soundboard, use_external_sounds
change_nickname, use_application_commands, send_polls
"""

MAX_ROUNDS = 25

# ── Permission flag mapping ───────────────────────────────────────────────────

# Aliases: user-friendly names → discord.py Permissions attribute names.
# We delegate to discord.py rather than hardcoding bit values so bits are
# always correct across API versions (fixes silent misgrants where wrong
# flags previously ORed the bit for a different permission).
_PERM_ALIASES: dict[str, str] = {
    "timeout_members": "moderate_members",
    "read_messages": "view_channel",
    "manage_emojis": "manage_emojis_and_stickers",
    "manage_emoji": "manage_emojis_and_stickers",
    "manage_stickers": "manage_emojis_and_stickers",
    "use_slash_commands": "use_application_commands",
    "use_vad": "use_voice_activation",
    "start_embedded_activities": "use_embedded_activities",
}


def _parse_perms(perm_str: str) -> int:
    """Parse comma-separated permission flag names into a Discord permissions integer,
    using discord.py's authoritative attribute mapping."""
    if not perm_str:
        return 0
    p = discord.Permissions.none()
    for flag in perm_str.split(","):
        flag = flag.strip().lower().replace("-", "_").replace(" ", "_")
        if not flag:
            continue
        attr = _PERM_ALIASES.get(flag, flag)
        if hasattr(p, attr):
            try:
                setattr(p, attr, True)
            except (AttributeError, TypeError):
                pass
    return p.value




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
@                 