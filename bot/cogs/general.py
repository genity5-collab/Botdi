"""
General Cog — Server utility commands.

Commands:
  /ping          — Bot and API latency
  /uptime        — Bot uptime
  /userinfo      — User information
  /serverinfo    — Server information
  /membercount   — Member count breakdown
  /rolelist      — List all roles in the server
  /channelinfo    — Channel information
  /help          — Show all commands
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

    @commands.hybrid_command(name="ping", description="Check bot and API latency")
    async def ping(self, ctx: commands.Context) -> None:
        ws_latency = round(self.bot.latency * 1000)
        before = time.monotonic()
        msg = await ctx.send("Pinging…")
        rtt = round((time.monotonic() - before) * 1000)
        embed = discord.Embed(title="🏓 Pong!", color=BOT_COLOR)
        embed.add_field(name="WebSocket", value=f"`{ws_latency} ms`", inline=True)
        embed.add_field(name="Round-trip", value=f"`{rtt} ms`", inline=True)
        await msg.edit(content=None, embed=embed)

    @commands.hybrid_command(name="uptime", description="Show how long the bot has been running")
    async def uptime(self, ctx: commands.Context) -> None:
        elapsed = int(time.monotonic() - self._start)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        embed = discord.Embed(
            title="⏱️ Uptime",
            description=f"**{hours}h {minutes}m {seconds}s**",
            color=BOT_COLOR,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="userinfo", description="Show information about a user")
    @discord.app_commands.describe(user="User ID or @mention (optional, defaults to you)")
    async def userinfo(self, ctx: commands.Context, *, target_str: str | None = None) -> None:
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

    @commands.hybrid_command(name="serverinfo", description="Show server information")
    async def serverinfo(self, ctx: commands.Context) -> None:
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

    @commands.hybrid_command(name="membercount", description="Show detailed member count breakdown")
    async def membercount(self, ctx: commands.Context) -> None:
        g = ctx.guild
        total = g.member_count or 0
        bots = sum(1 for m in g.members if m.bot)
        humans = total - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline)
        embed = discord.Embed(
            title="👥 Member Count",
            color=BOT_COLOR,
        )
        embed.add_field(name="Total", value=str(total), inline=True)
        embed.add_field(name="Humans", value=str(humans), inline=True)
        embed.add_field(name="Bots", value=str(bots), inline=True)
        embed.add_field(name="Online", value=str(online), inline=True)
        embed.add_field(name="Boosts", value=str(g.premium_subscription_count), inline=True)
        embed.add_field(name="Boost Level", value=f"Level {g.premium_tier}", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="rolelist", description="List all roles in the server")
    @commands.has_permissions(manage_roles=True)
    async def rolelist(self, ctx: commands.Context) -> None:
        roles = [r for r in ctx.guild.roles if r != ctx.guild.default_role]
        if not roles:
            await ctx.send("No roles found.", delete_after=10)
            return
        lines = []
        for role in roles[:20]:
            member_count = len(role.members)
            lines.append(f"{role.mention} — {member_count} members (pos {role.position})")
        embed = discord.Embed(
            title=f"📋 Roles ({len(roles)} total, showing {min(20, len(roles))})",
            description="\n".join(lines) or "No roles",
            color=BOT_COLOR,
        )
        if len(roles) > 20:
            embed.set_footer(text=f"…and {len(roles) - 20} more roles")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="channelinfo", description="Show information about a channel")
    @discord.app_commands.describe(channel="Channel to inspect (optional, defaults to current)")
    async def channelinfo(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        ch = channel or ctx.channel
        created = discord.utils.format_dt(ch.created_at, style="R")
        embed = discord.Embed(
            title=f"📢 #{ch.name}",
            color=BOT_COLOR,
        )
        embed.add_field(name="ID", value=f"`{ch.id}`", inline=True)
        embed.add_field(name="Type", value=str(ch.type).replace("TextChannel", "Text").replace("VoiceChannel", "Voice"), inline=True)
        embed.add_field(name="Created", value=created, inline=True)
        embed.add_field(name="Category", value=ch.category.name if ch.category else "None", inline=True)
        embed.add_field(name="NSFW", value="Yes" if ch.nsfw else "No", inline=True)
        embed.add_field(name="Slowmode", value=f"{ch.slowmode_delay}s", inline=True)
        if ch.topic:
            embed.add_field(name="Topic", value=ch.topic[:200], inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="help", description="Show all available commands")
    async def help_cmd(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="📖 Vyrion Commands",
            description="All commands work as **slash commands** (type `/`) or with `!` prefix.",
            color=BOT_COLOR,
        )
        embed.add_field(name="🤖 AI", value=(
            "`@Vyrion` — Ask anything (guild)\n"
            "DM me directly — private chat\n"
            "📷 Send images/videos — I can see them!\n"
            "🧠 Long-term memory — I remember your name, interests\n"
            "Say `forget me` in DMs to wipe memory"
        ), inline=False)
        embed.add_field(name="🏗️ App Engineering", value=(
            "`/site <description>` — Build a website (owner only)\n"
            "`/myprojects` — List your projects\n"
            "`/deleteproject <id>` — Delete a project\n"
            "💡 Big projects cost 3 credits, small ones cost 1"
        ), inline=False)
        embed.add_field(name="⚠️ Moderation", value=(
            "`/strike` — 1=10h timeout, 2=2d timeout, 3=ban\n"
            "`/strikes` `/resetstrikes` — View/reset strikes\n"
            "`/kick` `/ban` `/unban` `/softban` `/cleanban`\n"
            "`/banlist` — List all banned users\n"
            "`/mute` `/unmute` `/warn` `/purge` `/nuke`\n"
            "`/purgeuser` — Delete messages from a specific user\n"
            "`/slowmode` `/lock` `/unlock`\n"
            "`/channellock` `/channelunlock` — Lock for specific roles\n"
            "`/roleinfo` `/roleadd` `/roletake`\n"
            "`/massrole` `/massremoverole` — Bulk role management\n"
            "`/timeoutinfo` — Check timeout expiry"
        ), inline=False)
        embed.add_field(name="🎫 Support", value=(
            "`/ticket` — Open a ticket\n"
            "`/reply` `/close` `/decline` — Staff commands\n"
            "`/tickets` — List open tickets (staff)\n"
            f"⏰ Auto-close after 12h of inactivity"
        ), inline=False)
        embed.add_field(name="📝 Create", value=(
            "`/create channel` — Text/voice/announcement channel\n"
            "`/create text` — Text channel with topic\n"
            "`/create voice` — Voice channel with user limit\n"
            "`/create category` — New category\n"
            "`/create role` — Role with color\n"
            "`/create emoji` — Emoji from image URL\n"
            "`/create stage` — Stage channel\n"
            "`/create forum` — Forum channel"
        ), inline=False)
        embed.add_field(name="🛠️ Server", value=(
            "`/ping` `/uptime` `/botinfo`\n"
            "`/userinfo` `/serverinfo` `/membercount`\n"
            "`/rolelist` `/channelinfo` `/avatar`\n"
            "`/poll` `/afk` `/embed` `/announce` `/help`"
        ), inline=False)
        embed.set_footer(text="Vyrion • AI App Engineering • Smart Moderation")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
