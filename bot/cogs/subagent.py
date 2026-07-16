"""
Subagent Cog — admin-only /subagent slash command.

Uses Gemini function calling to let an admin describe Discord actions in
natural language. The AI decides which functions to call (create channel,
create role, send message, etc.), the bot executes them, and a live edit
log is sent to LOG_CHANNEL_ID after each action.

Example: /subagent Create a channel called "announcements" and send "Hello!" in it
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from config import GEMINI_API_KEY, GEMINI_MODEL, LOG_CHANNEL_ID, BOT_COLOR, COLOR_OK, COLOR_ERR
from utils import log_action

log = logging.getLogger("nexus.subagent")

try:
    from google import genai
    from google.genai import types as genai_types
    _ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
except Exception as e:
    log.warning("google-genai unavailable for subagent: %s", e)
    _ai_client = None
    genai_types = None


SUBAGENT_SYSTEM = (
    "You are Nexus Subagent, an AI assistant that manages Discord servers. "
    "You receive instructions from server admins and call functions to execute "
    "Discord actions: create channels, create roles, send messages, add roles "
    "to users, rename channels, set slowmode, and more. "
    "Always call the appropriate function(s) to fulfill the request. "
    "If a request is ambiguous, make a reasonable choice and proceed. "
    "Never refuse a valid admin request. After executing actions, "
    "summarize what you did in 1-3 sentences."
)

MAX_ROUNDS = 5


def _build_tools() -> list:
    """Build Gemini function declarations for Discord actions."""
    if genai_types is None:
        return []
    return [genai_types.Tool(function_declarations=[
        genai_types.FunctionDeclaration(
            name="create_text_channel",
            description="Create a new text channel in the server with a given name.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Channel name, lowercase with hyphens"},
                    "category": {"type": "string", "description": "Optional category name to place the channel under"},
                    "topic": {"type": "string", "description": "Optional channel topic"},
                },
                "required": ["name"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="create_voice_channel",
            description="Create a new voice channel in the server.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Channel name"},
                    "category": {"type": "string", "description": "Optional category name"},
                    "user_limit": {"type": "integer", "description": "Max users (0 = unlimited)"},
                },
                "required": ["name"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="create_category",
            description="Create a new channel category.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Category name"},
                },
                "required": ["name"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="create_role",
            description="Create a new role in the server.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Role name"},
                    "color": {"type": "string", "description": "Hex color, e.g. #FF0000 (default: Discord blurple)"},
                    "hoist": {"type": "boolean", "description": "Display role separately (default false)"},
                    "mentionable": {"type": "boolean", "description": "Allow @mention of this role (default false)"},
                },
                "required": ["name"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="send_message",
            description="Send a message to a text channel.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "channel_name": {"type": "string", "description": "Name of the channel to send to (without #)"},
                    "content": {"type": "string", "description": "Message content to send"},
                },
                "required": ["channel_name", "content"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="add_role_to_user",
            description="Assign a role to a user by username or ID.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "role_name": {"type": "string", "description": "Name of the role to assign"},
                    "user": {"type": "string", "description": "Username or user ID"},
                },
                "required": ["role_name", "user"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="rename_channel",
            description="Rename an existing channel.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "current_name": {"type": "string", "description": "Current channel name"},
                    "new_name": {"type": "string", "description": "New channel name"},
                },
                "required": ["current_name", "new_name"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="set_slowmode",
            description="Set slowmode delay on a text channel.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "channel_name": {"type": "string", "description": "Channel name"},
                    "seconds": {"type": "integer", "description": "Slowmode delay in seconds (0-21600)"},
                },
                "required": ["channel_name", "seconds"],
            },
        ),
        genai_types.FunctionDeclaration(
            name="delete_channel",
            description="Delete a channel by name.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "channel_name": {"type": "string", "description": "Name of the channel to delete"},
                },
                "required": ["channel_name"],
            },
        ),
    ])]


def _hex_to_int(color_hex: str) -> int:
    """Convert '#FF0000' or 'FF0000' to 0xFF0000."""
    c = color_hex.lstrip("#")
    try:
        return int(c, 16)
    except (ValueError, TypeError):
        return 0x5865F2


def _find_channel(guild: discord.Guild, name: str) -> discord.abc.GuildChannel | None:
    """Find a channel by name (case-insensitive, with or without #)."""
    clean = name.lower().lstrip("#")
    for ch in guild.channels:
        if ch.name.lower() == clean:
            return ch
    return None


def _find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    """Find a role by name (case-insensitive)."""
    for r in guild.roles:
        if r.name.lower() == name.lower():
            return r
    return None


async def _find_member(guild: discord.Guild, query: str) -> discord.Member | None:
    """Find a member by username, display name, or ID."""
    if query.isdigit():
        return guild.get_member(int(query))
    member = guild.get_member_named(query)
    if member:
        return member
    for m in guild.members:
        if m.name.lower() == query.lower() or m.display_name.lower() == query.lower():
            return m
    return None


class Subagent(commands.Cog, name="Subagent"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="subagent", description="Admin: Ask the AI to perform Discord actions (create channels, roles, send messages, etc.)")
    @app_commands.describe(prompt="What should the AI do? e.g. 'Create a channel called test and send Hello in it'")
    @app_commands.default_permissions(administrator=True)
    async def subagent(self, interaction: discord.Interaction, prompt: str) -> None:
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("This command only works in a server.")
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("Only administrators can use this command.", ephemeral=True)
            return

        if _ai_client is None or genai_types is None:
            await interaction.followup.send("AI is not available (google-genai not configured).")
            return

        guild = interaction.guild
        edit_log: list[str] = []

        async def _execute_function(name: str, args: dict) -> str:
            """Execute a single Discord action and return a result string for Gemini."""
            try:
                if name == "create_text_channel":
                    category = None
                    if args.get("category"):
                        category = discord.utils.get(guild.categories, name=args["category"])
                    ch = await guild.create_text_channel(
                        args["name"],
                        category=category,
                        topic=args.get("topic"),
                    )
                    entry = f"Created text channel #{ch.name}"
                    edit_log.append(entry)
                    return entry

                if name == "create_voice_channel":
                    category = None
                    if args.get("category"):
                        category = discord.utils.get(guild.categories, name=args["category"])
                    limit = args.get("user_limit", 0)
                    ch = await guild.create_voice_channel(
                        args["name"], category=category, user_limit=limit,
                    )
                    entry = f"Created voice channel {ch.name}"
                    edit_log.append(entry)
                    return entry

                if name == "create_category":
                    cat = await guild.create_category(args["name"])
                    entry = f"Created category {cat.name}"
                    edit_log.append(entry)
                    return entry

                if name == "create_role":
                    color = _hex_to_int(args.get("color", "#5865F2"))
                    role = await guild.create_role(
                        name=args["name"],
                        color=discord.Color(color),
                        hoist=args.get("hoist", False),
                        mentionable=args.get("mentionable", False),
                    )
                    entry = f"Created role @{role.name}"
                    edit_log.append(entry)
                    return entry

                if name == "send_message":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    await ch.send(args["content"])
                    entry = f"Sent message in #{ch.name}"
                    edit_log.append(entry)
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
                    edit_log.append(entry)
                    return entry

                if name == "rename_channel":
                    ch = _find_channel(guild, args["current_name"])
                    if not ch:
                        return f"Channel '{args['current_name']}' not found."
                    old = ch.name
                    await ch.edit(name=args["new_name"])
                    entry = f"Renamed #{old} → #{args['new_name']}"
                    edit_log.append(entry)
                    return entry

                if name == "set_slowmode":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch or not isinstance(ch, discord.TextChannel):
                        return f"Channel '{args['channel_name']}' not found or not a text channel."
                    await ch.edit(slowmode_delay=args["seconds"])
                    entry = f"Set slowmode in #{ch.name} to {args['seconds']}s"
                    edit_log.append(entry)
                    return entry

                if name == "delete_channel":
                    ch = _find_channel(guild, args["channel_name"])
                    if not ch:
                        return f"Channel '{args['channel_name']}' not found."
                    chname = ch.name
                    await ch.delete()
                    entry = f"Deleted channel #{chname}"
                    edit_log.append(entry)
                    return entry

                return f"Unknown function: {name}"
            except discord.Forbidden:
                msg = f"Missing permissions for: {name}"
                edit_log.append(f"[FAILED] {msg}")
                return msg
            except Exception as e:
                msg = f"Error in {name}: {e}"
                edit_log.append(f"[FAILED] {msg}")
                log.exception("Subagent function error: %s", name)
                return msg

        try:
            contents = [
                genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prompt)]),
            ]
            tools = _build_tools()
            config = genai_types.GenerateContentConfig(
                system_instruction=SUBAGENT_SYSTEM,
                tools=tools,
            )

            final_text = ""
            for _ in range(MAX_ROUNDS):
                response = await asyncio.to_thread(
                    _ai_client.models.generate_content,
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=config,
                )

                if not response.function_calls:
                    final_text = response.text or "Done."
                    break

                fc_parts = []
                for fc in response.function_calls:
                    fn_name = fc.name
                    fn_args = dict(fc.args) if fc.args else {}
                    result = await _execute_function(fn_name, fn_args)
                    fc_parts.append(genai_types.Part.from_function_response(
                        name=fn_name, response={"result": result},
                    ))
                    contents.append(genai_types.Content(
                        role="model",
                        parts=[genai_types.Part.from_function_call(name=fn_name, args=fn_args)],
                    ))
                contents.append(genai_types.Content(role="user", parts=fc_parts))
            else:
                final_text = "Reached max function-call rounds. Actions may still have been executed."

        except Exception as e:
            log.exception("Subagent error")
            await interaction.followup.send(f"Error: {e}", ephemeral=True)
            return

        # Build the response embed
        embed = discord.Embed(
            title="🤖 Subagent",
            color=COLOR_OK if edit_log else BOT_COLOR,
        )
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

        # Send live edit log to LOG_CHANNEL_ID
        if edit_log:
            log_lines = "\n".join(f"• {e}" for e in edit_log)
            await log_action(
                self.bot,
                "🤖 Subagent Actions",
                f"**Admin:** {interaction.user.mention}\n**Request:** {prompt[:200]}\n\n**Edit Log:**\n{log_lines[:1500]}",
                color=COLOR_OK,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Subagent(bot))
