"""
General Cog
───────────
!ping, !uptime, !userinfo, !serverinfo, !help
"""

from __future__ import annotations

import time
import datetime
import discord
from discord.ext import commands

from config import BOT_COLOR
from utils import parse_user_id


class General(commands.Cog, name="General"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._start = time.monotonic()

    # ── !ping ─────────────────────────────────────────────────────────────────

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Check bot and API latency."""
        ws_latency = round(self.bot.latency * 1000)
        before = time.monotonic()
        msg = await ctx.send("Pinging…")
        rtt = round((time.monotonic() - before) * 1000)
        embed = discord.Embed(title="🏓 Pong!", color=BOT_COLOR)
        embed.add_field(name="WebSocket", value=f"`{ws_latency} ms`", inline=True)
        embed.add_field(name="Round-trip", value=f"`{rtt} ms`", inline=True)
        await msg.edit(content=None, embed=embed)

    # ── !uptime ───────────────────────────────────────────────────────────────

    @commands.command(name="uptime")
    async def uptime(self, ctx: commands.Context) -> None:
        """Show how long the bot has been running."""
        elapsed = int(time.monotonic() - self._start)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        embed = discord.Embed(
            title="⏱️ Uptime",
            description=f"**{hours}h {minutes}m {seconds}s**",
            color=BOT_COLOR,
        )
        await ctx.send(embed=embed)

    # ── !userinfo ─────────────────────────────────────────────────────────────

    @commands.command(name="userinfo")
    async def userinfo(self, ctx: commands.Context, *, target_str: str | None = None) -> None:
        """Show information about a user. Usage: !userinfo [user_id|@mention]"""
        if target_str is None:
            member = ctx.author
        else:
            uid = parse_user_id(target_str)
            if uid is None:
                await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
                return
            member = ctx.guild.get_member(uid)
            if member is None:
                try:
                    member = await ctx.guild.fetch_member(uid)
                except discord.NotFound:
                    await ctx.send("❌ Member not found in this server.", delete_after=10)
                    return

        roles = [r.mention for r in member.roles if r != ctx.guild.default_role]
        joined = discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown"
        created = discord.utils.format_dt(member.created_at, style="R")

        embed = discord.Embed(title=str(member), color=member.color or BOT_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Joined Server", value=joined, inline=True)
        embed.add_field(name="Account Created", value=created, inline=True)
        embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Status", value=str(member.status).title(), inline=True)
        if roles:
            embed.add_field(
                name=f"Roles ({len(roles)})",
                value=", ".join(roles[:10]) + ("…" if len(roles) > 10 else ""),
                inline=False,
            )
        await ctx.send(embed=embed)

    # ── !serverinfo ───────────────────────────────────────────────────────────

    @commands.command(name="serverinfo")
    async def serverinfo(self, ctx: commands.Context) -> None:
        """Show server information."""
        g = ctx.guild
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
            "`@Bot <question>` — Ask me anything (60 s cooldown)\n"
            "`@Bot I'm being bullied by @user` — Trigger anti-bully investigation"
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
