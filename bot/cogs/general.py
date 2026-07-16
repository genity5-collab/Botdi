"""
General Cog (Slash Commands)
────────────────────────────
/ping, /uptime, /userinfo, /serverinfo, /help
"""

from __future__ import annotations

import time
import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_COLOR


class General(commands.Cog, name="General"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._start = time.monotonic()

    @app_commands.command(name="ping", description="Check bot and API latency.")
    async def ping(self, interaction: discord.Interaction) -> None:
        ws_latency = round(self.bot.latency * 1000)
        before = time.monotonic()
        await interaction.response.send_message("Pinging…")
        rtt = round((time.monotonic() - before) * 1000)
        embed = discord.Embed(title="🏓 Pong!", color=BOT_COLOR)
        embed.add_field(name="WebSocket", value=f"`{ws_latency} ms`", inline=True)
        embed.add_field(name="Round-trip", value=f"`{rtt} ms`", inline=True)
        await interaction.edit_original_response(content=None, embed=embed)

    @app_commands.command(name="uptime", description="Show how long the bot has been running.")
    async def uptime(self, interaction: discord.Interaction) -> None:
        elapsed = int(time.monotonic() - self._start)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        embed = discord.Embed(
            title="⏱️ Uptime",
            description=f"**{hours}h {minutes}m {seconds}s**",
            color=BOT_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Show information about a user.")
    @app_commands.describe(target="User (optional)")
    async def userinfo(self, interaction: discord.Interaction, target: discord.User | None = None) -> None:
        await interaction.response.defer()
        member = target or interaction.user
        if isinstance(member, discord.User) and interaction.guild:
            try:
                member = await interaction.guild.fetch_member(member.id)
            except discord.NotFound:
                await interaction.followup.send("❌ Member not found in this server.")
                return
        roles = (
            [r.mention for r in getattr(member, "roles", []) if r != interaction.guild.default_role]
            if interaction.guild else []
        )
        joined = discord.utils.format_dt(member.joined_at, style="R") if getattr(member, "joined_at", None) else "Unknown"
        created = discord.utils.format_dt(member.created_at, style="R")

        embed = discord.Embed(title=str(member), color=BOT_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        if joined != "Unknown":
            embed.add_field(name="Joined Server", value=joined, inline=True)
        embed.add_field(name="Account Created", value=created, inline=True)
        if hasattr(member, "top_role"):
            embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
        if roles:
            embed.add_field(
                name=f"Roles ({len(roles)})",
                value=", ".join(roles[:10]) + ("…" if len(roles) > 10 else ""),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="serverinfo", description="Show server information.")
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        g = interaction.guild
        if not g:
            await interaction.followup.send("❌ This command only works in servers.")
            return
        created = discord.utils.format_dt(g.created_at, style="R")
        bots = sum(1 for m in g.members if m.bot)
        humans = (g.member_count or 0) - bots

        embed = discord.Embed(title=g.name, color=BOT_COLOR)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner", value=g.owner.mention if g.owner else "Unknown", inline=True)
        embed.add_field(name="Created", value=created, inline=True)
        embed.add_field(name="Members", value=f"{humans} humans · {bots} bots", inline=True)
        embed.add_field(name="Channels", value=f"{len(g.text_channels)} text · {len(g.voice_channels)} voice", inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Boost Level", value=f"Level {g.premium_tier}", inline=True)
        if g.description:
            embed.add_field(name="Description", value=g.description, inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="help", description="Show all available commands.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="📖 Nexus — Command Reference",
            description="All available slash commands",
            color=BOT_COLOR,
        )
        embed.add_field(name="🤖 AI", value=(
            "`/ask <question>` — Ask Nexus anything\n"
            "`/forget` — Clear your chat history\n"
            "`/teach <fact>` — Admin: teach Nexus about this server\n"
            "`/untutor` — Admin: clear taught facts\n"
            "`/roblox <kind> [query]` — Live Roblox lookup (game/user/trending)\n"
            "*Or @mention me, say `nexus …`, or DM me — I understand images too.*"
        ), inline=False)
        embed.add_field(name="🤖 Subagent", value=(
            "`/subagent <prompt>` — Admin: AI performs Discord actions from text\n"
            "Creates channels, roles, sends messages, and more. Includes live edit log."
        ), inline=False)
        embed.add_field(name="⚠️ Moderation", value=(
            "`/strike` `/strikes` `/mute` `/unmute` `/warn` "
            "`/kick` `/ban` `/purge` `/slowmode` `/lock` `/unlock`"
        ), inline=False)
        embed.add_field(name="🎫 Support", value=(
            "DM the bot to open a ticket · `!reply <id> <msg>` · `!close <id>` (staff)"
        ), inline=False)
        embed.add_field(name="🎮 Fun", value=(
            "`/roll` `/flip` `/8ball` `/poll` `/avatar` `/botinfo`"
        ), inline=False)
        embed.add_field(name="ℹ️ General", value=(
            "`/ping` `/uptime` `/userinfo` `/serverinfo` `/help`"
        ), inline=False)
        embed.set_footer(text="Nexus — type / in Discord to see all commands")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
