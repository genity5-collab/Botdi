"""
General Cog (Slash Commands)
────────────────────────────
/ping, /uptime, /userinfo, /serverinfo, /help
"""

from __future__ import annotations

import time
import datetime
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
        """Check bot and API latency."""
        ws_latency = round(self.bot.latency * 1000)
        before = time.monotonic()
        msg = await interaction.response.send_message("Pinging…")
        rtt = round((time.monotonic() - before) * 1000)
        embed = discord.Embed(title="🏓 Pong!", color=BOT_COLOR)
        embed.add_field(name="WebSocket", value=f"`{ws_latency} ms`", inline=True)
        embed.add_field(name="Round-trip", value=f"`{rtt} ms`", inline=True)
        await msg.edit(content=None, embed=embed)

    @app_commands.command(name="uptime", description="Show how long the bot has been running.")
    async def uptime(self, interaction: discord.Interaction) -> None:
        """Show how long the bot has been running."""
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
    @app_commands.describe(target="User ID or @mention (optional)")
    async def userinfo(self, interaction: discord.Interaction, target: discord.User | None = None) -> None:
        """Show information about a user."""
        await interaction.response.defer()
        
        member = target or interaction.user
        if isinstance(member, discord.User) and interaction.guild:
            try:
                member = await interaction.guild.fetch_member(member.id)
            except discord.NotFound:
                await interaction.followup.send("❌ Member not found in this server.")
                return

        roles = [r.mention for r in member.roles if hasattr(member, 'roles') and r != interaction.guild.default_role] if interaction.guild and hasattr(member, 'roles') else []
        joined = discord.utils.format_dt(member.joined_at, style="R") if hasattr(member, 'joined_at') and member.joined_at else "Unknown"
        created = discord.utils.format_dt(member.created_at, style="R")

        embed = discord.Embed(title=str(member), color=BOT_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        if joined != "Unknown":
            embed.add_field(name="Joined Server", value=joined, inline=True)
        embed.add_field(name="Account Created", value=created, inline=True)
        if hasattr(member, 'top_role'):
            embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
        if hasattr(member, 'status'):
            embed.add_field(name="Status", value=str(member.status).title(), inline=True)
        if roles:
            embed.add_field(
                name=f"Roles ({len(roles)})",
                value=", ".join(roles[:10]) + ("…" if len(roles) > 10 else ""),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="serverinfo", description="Show server information.")
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        """Show server information."""
        await interaction.response.defer()
        
        g = interaction.guild
        if not g:
            await interaction.followup.send("❌ This command only works in servers.")
            return
            
        created = discord.utils.format_dt(g.created_at, style="R")
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots

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
        """Show all available commands."""
        embed = discord.Embed(
            title="📖 Command Reference",
            description="All available slash commands",
            color=BOT_COLOR,
        )

        embed.add_field(name="🤖 AI", value=(
            "`/ask <question>` — Ask anything\n"
            "`/forget` — Clear your chat history"
        ), inline=False)

        embed.add_field(name="⚠️ Moderation", value=(
            "`/strike <user> [reason]` — Issue a strike\n"
            "`/strikes <user>` — View strike count\n"
            "`/mute <user> [minutes]` — Timeout a user\n"
            "`/unmute <user>` — Remove timeout\n"
            "`/kick <user> [reason]` — Kick a member\n"
            "`/ban <user> [reason]` — Ban a member"
        ), inline=False)

        embed.add_field(name="🎮 Fun", value=(
            "`/roll [sides]` — Roll a die\n"
            "`/flip` — Coin flip\n"
            "`/8ball <question>` — Magic 8-ball\n"
            "`/poll <question>` — Yes/no poll\n"
            "`/avatar [user]` — Show avatar\n"
            "`/botinfo` — Bot stats"
        ), inline=False)

        embed.add_field(name="ℹ️ General", value=(
            "`/ping` — Check latency\n"
            "`/uptime` — Show uptime\n"
            "`/userinfo [user]` — Show user info\n"
            "`/serverinfo` — Show server info\n"
            "`/help` — This menu"
        ), inline=False)

        embed.set_footer(text="Type / in Discord to see commands")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))        embed.add_field(name="Created", value=created, inline=True)
        embed.add_field(name="Members", value=f"{humans} humans · {bots} bots", inline=True)
        embed.add_field(name="Channels", value=f"{len(g.text_channels)} text · {len(g.voice_channels)} voice", inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Boost Level", value=f"Level {g.premium_tier}", inline=True)
        if g.description:
            embed.add_field(name="Description", value=g.description, inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="help", description="Show all available commands.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        """Show all available commands."""
        embed = discord.Embed(
            title="📖 Command Reference",
            description="All available slash commands",
            color=BOT_COLOR,
        )

        embed.add_field(name="🤖 AI", value=(
            "`@Nexus <question>` — Ask anything (guild, 45 s cooldown)\n"
            "`nexus <question>` — Say my name anywhere — no @ needed!\n"
            "💬 **DM me directly** — unlimited private chat, I remember our history\n"
            "*Say* `forget me` *in DMs to clear your chat history*"
        ), inline=False)

        embed.add_field(name="⚠️ Moderation", value=(
            "`/strike <user> [reason]` — Issue a strike (1=24h timeout, 3=ban)\n"
            "`/strikes <user>` — View strike count\n"
            "`/mute <user> [minutes] [reason]` — Timeout a user\n"
            "`/unmute <user>` — Remove timeout\n"
            "`/warn <user> [reason]` — Send a warning DM (no strike)\n"
            "`/kick <user> [reason]` — Kick a member\n"
            "`/ban <user> [reason]` — Ban a member\n"
            "`/purge <1–100>` — Delete recent messages\n"
            "`/slowmode <seconds>` — Set channel slowmode (0 = off)\n"
            "`/lock [#channel]` — Lock channel (no one can send)\n"
            "`/unlock [#channel]` — Unlock channel"
        ), inline=False)

        embed.add_field(name="🎫 Support", value=(
            "DM the bot — Open a support ticket\n"
            "`/reply <id> <msg>` — Reply to a ticket (staff)\n"
            "`/close <id>` — Close a ticket (staff)"
        ), inline=False)

        embed.add_field(name="📢 Admin", value=(
            '`/embed <#channel> "Title" <desc>` — Post a branded embed'
        ), inline=False)

        embed.add_field(name="🎮 Fun", value=(
            "`/roll [sides]` — Roll a die (default d6)\n"
            "`/flip` — Coin flip\n"
            "`/8ball <question>` — Magic 8-ball\n"
            "`/poll <question>` — Yes/no poll\n"
            "`/choose opt1 | opt2 | …` — Pick randomly\n"
            "`/rps <rock|paper|scissors>` — Play RPS\n"
            "`/math <expr>` — Calculate (e.g. `!math 5*12`)\n"
            "`/avatar [user]` — Show avatar\n"
            "`/botinfo` — Nexus stats\n"
            "`/snipe` — Last deleted message (staff)\n"
            "`/afk [reason]` — Set AFK status"
        ), inline=False)

        embed.add_field(name="ℹ️ General", value=(
            "`/ping` — Check latency\n"
            "`/uptime` — Show uptime\n"
            "`/userinfo [user]` — Show user info\n"
            "`/serverinfo` — Show server info\n"
            "`/help` — This menu"
        ), inline=False)

        embed.set_footer(text="<user> = ID or @mention  •  [optional]")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))        embed.add_field(name="Members", value=f"{humans} humans · {bots} bots", inline=True)
        embed.add_field(name="Channels", value=f"{len(g.text_channels)} text · {len(g.voice_channels)} voice", inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Boost Level", value=f"Level {g.premium_tier}", inline=True)
        if g.description:
            embed.add_field(name="Description", value=g.description, inline=False)
        await ctx.send(embed=embed)

    # ── !help ─────────────────────────────────────────────────────────────────

    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context) -> None:
        """Show all available commands."""
        embed = discord.Embed(
            title="📖 Command Reference",
            description="All available commands. `<required>` `[optional]`",
            color=BOT_COLOR,
        )

        embed.add_field(name="🤖 AI", value=(
            "`@Nexus <question>` — Ask anything (guild, 45 s cooldown)\n"
            "`nexus <question>` — Say my name anywhere — no @ needed!\n"
            "💬 **DM me directly** — unlimited private chat, I remember our history\n"
            "`@Nexus I'm being bullied by @user` — Trigger anti-bully investigation\n"
            "*Say* `forget me` *in DMs to clear your chat history*"
        ), inline=False)

        embed.add_field(name="⚠️ Moderation", value=(
            "`!strike <user> [reason]` — Issue a strike (1=24h timeout, 3=ban)\n"
            "`!strikes <user>` — View strike count\n"
            "`!mute <user> [minutes] [reason]` — Timeout a user\n"
            "`!unmute <user>` — Remove timeout\n"
            "`!warn <user> [reason]` — Send a warning DM (no strike)\n"
            "`!kick <user> [reason]` — Kick a member\n"
            "`!ban <user> [reason]` — Ban a member\n"
            "`!purge <1–100>` — Delete recent messages\n"
            "`!slowmode <seconds>` — Set channel slowmode (0 = off)\n"
            "`!lock [#channel]` — Lock channel (no one can send)\n"
            "`!unlock [#channel]` — Unlock channel"
        ), inline=False)

        embed.add_field(name="🎫 Support", value=(
            "DM the bot — Open a support ticket\n"
            "`!reply <id> <msg>` — Reply to a ticket (staff)\n"
            "`!close <id>` — Close a ticket (staff)"
        ), inline=False)

        embed.add_field(name="📢 Admin", value=(
            '`!embed <#channel> "Title" <desc>` — Post a branded embed'
        ), inline=False)

        embed.add_field(name="🎮 Fun", value=(
            "`!roll [sides]` — Roll a die (default d6)\n"
            "`!flip` — Coin flip\n"
            "`!8ball <question>` — Magic 8-ball\n"
            "`!poll <question>` — Yes/no poll\n"
            "`!choose opt1 | opt2 | …` — Pick randomly\n"
            "`!rps <rock|paper|scissors>` — Play RPS\n"
            "`!math <expr>` — Calculate (e.g. `!math 5*12`)\n"
            "`!avatar [user]` — Show avatar\n"
            "`!botinfo` — Nexus stats\n"
            "`!snipe` — Last deleted message (staff)\n"
            "`!afk [reason]` — Set AFK status"
        ), inline=False)

        embed.add_field(name="ℹ️ General", value=(
            "`!ping` — Check latency\n"
            "`!uptime` — Show uptime\n"
            "`!userinfo [user]` — Show user info\n"
            "`!serverinfo` — Show server info\n"
            "`!help` — This menu"
        ), inline=False)

        embed.set_footer(text="<user> = ID or @mention  •  [optional]")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
