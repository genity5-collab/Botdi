"""
General Cog — /ping, /uptime, /userinfo, /serverinfo, /help
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
            "Say `forget me` in DMs to clear history"
        ), inline=False)
        embed.add_field(name="🏗️ App Engineering", value=(
            "`/site <description>` — Build a website\n"
            "`/site <edit>` — Edit your latest project\n"
            "`/myprojects` — List your projects\n"
            "`/sitecredits` — Check remaining credits\n"
            "`/deleteproject <id>` — Delete a project\n"
            "`/setkey <key>` — Add your Gemini key (DM only)\n"
            "`/removekey` — Remove your key (DM only)"
        ), inline=False)
        embed.add_field(name="⚠️ Moderation", value=(
            "`/strike` — 1=10h timeout, 2=2d timeout, 3=ban\n"
            "`/strikes` `/resetstrikes` — View/reset strikes\n"
            "`/kick` `/ban` `/softban` `/cleanban`\n"
            "`/mute` `/unmute` `/warn` `/purge` `/nuke`\n"
            "`/slowmode` `/lock` `/unlock`\n"
            "`/roleinfo` `/roleadd` `/roletake`\n"
            "`/timeoutinfo` — Check timeout expiry"
        ), inline=False)
        embed.add_field(name="🎫 Support", value=(
            "`/ticket` — Open a ticket\n"
            "`/reply` `/close` `/decline` — Staff commands\n"
            "`/tickets` — List open tickets (staff)\n"
            f"⏰ Auto-close after 12h of inactivity"
        ), inline=False)
        embed.add_field(name="🎮 Fun", value=(
            "`/roll` `/flip` `/8ball` `/poll`\n"
            "`/avatar` `/botinfo` `/snipe` `/afk`"
        ), inline=False)
        embed.add_field(name="ℹ️ General", value=(
            "`/ping` `/uptime` `/userinfo`\n"
            "`/serverinfo` `/help`"
        ), inline=False)
        embed.set_footer(text="Vyrion • 5 free /site credits/month • Owner has unlimited")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
