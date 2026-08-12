"""
Moderation Cog — /strike, /kick, /ban, /strikes, /mute, /unmute, /warn, /purge, /slowmode, /lock, /unlock

Strike escalation:
  1 strike = DM warning + 10 hour timeout
  2 strikes = DM warning + 2 day timeout
  3 strikes = ban (strikes reset)
"""
from __future__ import annotations
import re
import time
import datetime

import discord
from discord.ext import commands

from config import (
    STRIKES_FOR_BAN,
    STRIKE_1_TIMEOUT_SECONDS,
    STRIKE_2_TIMEOUT_SECONDS,
    AUTOMOD_TIMEOUT_SECONDS,
    FILTER_COOLDOWN_SECONDS,
    BLACKLISTED_WORDS,
    BOT_COLOR, COLOR_ERR, COLOR_WARN, COLOR_OK,
)
from data_store import add_strike, get_strikes, reset_strikes
from utils import build_appeal_embed, log_action, parse_user_id

_BLACKLIST: list[str] = [w.lower() for w in BLACKLISTED_WORDS]

_EMOJI_RE = re.compile(
    r"<a?:\w+:\d+>"
    r"|[\U0001F000-\U0001FAFF]"
    r"|[\U00010000-\U0010FFFF]"
    r"|[\u2600-\u27BF]"
    r"|[\u2300-\u23FF]",
    re.UNICODE,
)


def _strip_content(text: str) -> str:
    text = _EMOJI_RE.sub(" ", text)
    text = re.sub(r"<@!?\d+>", " ", text)
    text = re.sub(r"<#\d+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return text.strip()


def _find_blacklisted(text: str) -> str | None:
    clean = _strip_content(text).lower()
    if len(clean) < 2:
        return None
    for word in _BLACKLIST:
        if re.search(r"\b" + re.escape(word) + r"\b", clean):
            return word
    return None


def _strike_action_text(strike_num: int) -> str:
    """Human-readable description of what happens at each strike level."""
    if strike_num >= STRIKES_FOR_BAN:
        return f"Ban (reached {STRIKES_FOR_BAN} strikes)"
    elif strike_num == 2:
        return "Strike 2 — 2 day timeout + DM warning"
    elif strike_num == 1:
        return "Strike 1 — 10 hour timeout + DM warning"
    return f"Strike {strike_num} issued"


def _strike_timeout_seconds(strike_num: int) -> int:
    """Get timeout duration for a given strike number."""
    if strike_num >= STRIKES_FOR_BAN:
        return 0  # Ban, no timeout
    elif strike_num == 2:
        return STRIKE_2_TIMEOUT_SECONDS
    else:
        return STRIKE_1_TIMEOUT_SECONDS


def _strike_dm_embed(strike_num: int, reason: str) -> discord.Embed:
    """Build the DM warning embed for a strike."""
    if strike_num >= STRIKES_FOR_BAN:
        return build_appeal_embed(reason)

    if strike_num == 2:
        timeout_text = "2 days"
        next_text = f"One more strike and you will be **banned**."
        color = COLOR_ERR
    else:
        timeout_text = "10 hours"
        next_text = f"Next strike: 2 day timeout + warning. Strike 3 = ban."
        color = COLOR_WARN

    embed = discord.Embed(
        title="⚠️ You received a strike",
        description=(
            f"**Reason:** {reason}\n"
            f"**Strike count:** {strike_num}/{STRIKES_FOR_BAN}\n\n"
            f"**Action taken:** {timeout_text} timeout\n\n"
            f"{next_text}\n\n"
            f"To appeal, use: [Appeal Form]({build_appeal_embed(reason).url if hasattr(build_appeal_embed(reason), 'url') else ''})"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="Botdi Moderation • Please review the server rules")
    return embed


class Moderation(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._filter_cooldowns: dict[int, float] = {}

    async def apply_strike(
        self,
        guild: discord.Guild,
        target: discord.Member | discord.User,
        reason: str,
        moderator: discord.Member | discord.User,
    ) -> None:
        total = await add_strike(target.id)
        member: discord.Member | None = guild.get_member(target.id)
        if member is None:
            try:
                member = await guild.fetch_member(target.id)
            except discord.NotFound:
                member = None

        action_taken = _strike_action_text(total)

        if total >= STRIKES_FOR_BAN:
            # ── Strike 3 = ban ──────────────────────────────────────────────────
            await reset_strikes(target.id)
            try:
                if member:
                    await member.ban(reason=f"[Auto] {STRIKES_FOR_BAN} strikes — {reason}")
                else:
                    await guild.ban(discord.Object(id=target.id), reason=f"[Auto] {reason}")
                action_taken = f"Banned (reached {STRIKES_FOR_BAN} strikes)"
            except discord.Forbidden:
                action_taken = f"Strike {total} — ban failed (missing permissions)"
        elif member:
            # ── Strike 1 or 2 = timeout + DM warning ────────────────────────────
            timeout_secs = _strike_timeout_seconds(total)
            until = discord.utils.utcnow() + datetime.timedelta(seconds=timeout_secs)
            try:
                await member.timeout(until, reason=f"Strike {total}: {reason}")
                if total == 2:
                    action_taken = "Strike 2 — 2 day timeout + DM warning"
                else:
                    action_taken = "Strike 1 — 10 hour timeout + DM warning"
            except discord.Forbidden:
                action_taken = f"Strike {total} issued (timeout failed — missing permissions)"

        # ── DM the user about the strike ───────────────────────────────────────
        try:
            dm = await target.create_dm()
            embed = _strike_dm_embed(total, reason)
            await dm.send(embed=embed)
        except discord.HTTPException:
            pass

        await log_action(
            self.bot, "⚠️ Strike Issued",
            f"**User:** {target.mention} (`{target.id}`)\n"
            f"**Moderator:** {moderator.mention}\n"
            f"**Reason:** {reason}\n**Outcome:** {action_taken}\n"
            f"**Strike count:** {total}/{STRIKES_FOR_BAN}",
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not _BLACKLIST:
            return
        now = time.monotonic()
        last = self._filter_cooldowns.get(message.author.id, 0.0)
        if now - last < FILTER_COOLDOWN_SECONDS:
            return
        self._filter_cooldowns[message.author.id] = now
        triggered = _find_blacklisted(message.content)
        if triggered is None:
            return
        member = message.author
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        until = discord.utils.utcnow() + datetime.timedelta(seconds=AUTOMOD_TIMEOUT_SECONDS)
        action_taken = "1h timeout applied"
        try:
            await member.timeout(until, reason="Auto-mod: prohibited word")
        except discord.Forbidden:
            action_taken = "timeout failed (missing permissions)"
        try:
            dm_embed = discord.Embed(
                title="🔇 Auto-Moderation",
                description="Your message was removed because it contained a prohibited word.\n\n"
                            "**Action:** 1-hour timeout\nPlease review the server rules.",
                color=COLOR_ERR, timestamp=discord.utils.utcnow(),
            )
            dm_embed.set_footer(text="Botdi Auto-Mod • Repeated violations lead to strikes")
            await member.send(embed=dm_embed)
        except discord.HTTPException:
            pass
        await log_action(
            self.bot, "🔇 Auto-Mod Timeout",
            f"**User:** {member.mention} (`{member.id}`)\n"
            f"**Channel:** {message.channel.mention}\n**Trigger:** `{triggered}`\n**Action:** {action_taken}",
        )

    @commands.hybrid_command(name="strike", description="Issue a strike (1=10h timeout, 2=2d timeout, 3=ban)")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(user="User ID or @mention", reason="Reason for the strike")
    async def strike_cmd(self, ctx: commands.Context, user: str, *, reason: str = "No reason provided") -> None:
        uid = parse_user_id(user)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        try:
            target = await self.bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send("❌ User not found.", delete_after=10)
            return
        await self.apply_strike(ctx.guild, target, reason, ctx.author)
        total = await get_strikes(target.id) if total < STRIKES_FOR_BAN else 0
        # Note: if banned, strikes are reset, so total shows 0 after ban
        if total == 0 and "Banned" in _strike_action_text(3):
            await ctx.send(f"🔨 {target.mention} has been **banned** (3 strikes).", delete_after=15)
        else:
            timeout_text = "10 hour" if total == 1 else "2 day"
            await ctx.send(f"⚠️ Strike {total} issued to {target.mention}. **{timeout_text} timeout** applied. They now have **{total}/{STRIKES_FOR_BAN}** strikes.", delete_after=15)

    @commands.hybrid_command(name="kick", description="Kick a member from the server")
    @commands.has_permissions(kick_members=True)
    @discord.app_commands.describe(user="User ID or @mention", reason="Reason for the kick")
    async def kick_cmd(self, ctx: commands.Context, user: str, *, reason: str = "No reason provided") -> None:
        uid = parse_user_id(user)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        member = ctx.guild.get_member(uid)
        if member is None:
            await ctx.send("❌ Member not found in this server.", delete_after=10)
            return
        try:
            await member.send(embed=build_appeal_embed(reason))
        except discord.HTTPException:
            pass
        try:
            await member.kick(reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to kick this user.", delete_after=10)
            return
        await log_action(self.bot, "👢 Member Kicked",
                         f"**User:** {member.mention} (`{member.id}`)\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}")
        await ctx.send(f"✅ **{member}** has been kicked.", delete_after=15)

    @commands.hybrid_command(name="ban", description="Ban a user from the server")
    @commands.has_permissions(ban_members=True)
    @discord.app_commands.describe(user="User ID or @mention", reason="Reason for the ban")
    async def ban_cmd(self, ctx: commands.Context, user: str, *, reason: str = "No reason provided") -> None:
        uid = parse_user_id(user)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        try:
            target = await self.bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send("❌ User not found.", delete_after=10)
            return
        try:
            await target.send(embed=build_appeal_embed(reason))
        except discord.HTTPException:
            pass
        try:
            await ctx.guild.ban(target, reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to ban this user.", delete_after=10)
            return
        await log_action(self.bot, "🔨 Member Banned",
                         f"**User:** {target.mention} (`{target.id}`)\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}")
        await ctx.send(f"✅ **{target}** has been banned.", delete_after=15)

    @commands.hybrid_command(name="strikes", description="View a user's strike count")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(user="User ID or @mention")
    async def strikes_cmd(self, ctx: commands.Context, user: str) -> None:
        uid = parse_user_id(user)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        try:
            target = await self.bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send("❌ User not found.", delete_after=10)
            return
        total = await get_strikes(target.id)
        embed = discord.Embed(
            title="📋 Strike Record",
            description=f"**{target}** (`{target.id}`) has **{total}/{STRIKES_FOR_BAN}** strike(s).",
            color=COLOR_WARN if total > 0 else COLOR_OK,
        )
        if total == 0:
            embed.add_field(name="Status", value="✅ No strikes — clean record", inline=False)
        elif total == 1:
            embed.add_field(name="Last Action", value="10 hour timeout + DM warning", inline=False)
            embed.add_field(name="Next Strike", value="2 day timeout + DM warning", inline=False)
        elif total == 2:
            embed.add_field(name="Last Action", value="2 day timeout + DM warning", inline=False)
            embed.add_field(name="⚠️ Next Strike", value="**Ban** — one more strike and they're banned", inline=False)
        embed.set_footer(text=f"Strike system: 1=10h timeout, 2=2d timeout, 3=ban")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="mute", description="Timeout a user")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(user="User ID or @mention", minutes="Duration in minutes (1-40320, default 10)", reason="Reason for the mute")
    async def mute_cmd(self, ctx: commands.Context, user: str, minutes: int = 10, *, reason: str = "No reason provided") -> None:
        uid = parse_user_id(user)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        if minutes < 1 or minutes > 40320:
            await ctx.send("❌ Duration must be 1–40320 minutes (max 28 days).", delete_after=10)
            return
        member = ctx.guild.get_member(uid)
        if member is None:
            await ctx.send("❌ Member not found in this server.", delete_after=10)
            return
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        try:
            await member.timeout(until, reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to timeout this user.", delete_after=10)
            return
        await log_action(self.bot, "🔇 Member Muted",
                         f"**User:** {member.mention} (`{member.id}`)\n**Moderator:** {ctx.author.mention}\n**Duration:** {minutes} min\n**Reason:** {reason}")
        await ctx.send(f"🔇 **{member}** has been muted for **{minutes}** minute(s).", delete_after=15)

    @commands.hybrid_command(name="unmute", description="Remove timeout from a user")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(user="User ID or @mention")
    async def unmute_cmd(self, ctx: commands.Context, user: str) -> None:
        uid = parse_user_id(user)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        member = ctx.guild.get_member(uid)
        if member is None:
            await ctx.send("❌ Member not found in this server.", delete_after=10)
            return
        try:
            await member.timeout(None, reason=f"{ctx.author}: unmuted")
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to unmute this user.", delete_after=10)
            return
        await log_action(self.bot, "🔊 Member Unmuted",
                         f"**User:** {member.mention} (`{member.id}`)\n**Moderator:** {ctx.author.mention}")
        await ctx.send(f"🔊 **{member}** has been unmuted.", delete_after=15)

    @commands.hybrid_command(name="warn", description="Send a warning DM to a user")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(user="User ID or @mention", reason="Warning reason")
    async def warn_cmd(self, ctx: commands.Context, user: str, *, reason: str = "No reason provided") -> None:
        uid = parse_user_id(user)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        try:
            target = await self.bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send("❌ User not found.", delete_after=10)
            return
        try:
            dm = await target.create_dm()
            embed = discord.Embed(
                title="⚠️ Warning",
                description=f"You have received a warning from **{ctx.guild.name}**.\n\n**Reason:** {reason}\n\nPlease review the server rules. Further violations may result in a strike.",
                color=COLOR_WARN,
            )
            await dm.send(embed=embed)
        except discord.HTTPException:
            await ctx.send("❌ Could not DM this user.", delete_after=10)
            return
        await log_action(self.bot, "⚠️ Member Warned",
                         f"**User:** {target.mention} (`{target.id}`)\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}")
        await ctx.send(f"✅ Warning sent to {target.mention}.", delete_after=15)

    @commands.hybrid_command(name="purge", description="Delete recent messages (1-100)")
    @commands.has_permissions(manage_messages=True)
    @discord.app_commands.describe(count="Number of messages to delete (1-100)")
    async def purge_cmd(self, ctx: commands.Context, count: int) -> None:
        if count < 1 or count > 100:
            await ctx.send("❌ Count must be 1–100.", delete_after=10)
            return
        deleted = await ctx.channel.purge(limit=count + 1)
        await ctx.send(f"🧹 Deleted **{len(deleted) - 1}** message(s).", delete_after=10)

    @commands.hybrid_command(name="slowmode", description="Set channel slowmode")
    @commands.has_permissions(manage_channels=True)
    @discord.app_commands.describe(seconds="Slowmode in seconds (0-21600, 0=off)")
    async def slowmode_cmd(self, ctx: commands.Context, seconds: int) -> None:
        if seconds < 0 or seconds > 21600:
            await ctx.send("❌ Seconds must be 0–21600.", delete_after=10)
            return
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"✅ Slowmode set to **{seconds}s**." if seconds > 0 else "✅ Slowmode disabled.", delete_after=10)

    @commands.hybrid_command(name="lock", description="Lock a channel (prevent messages)")
    @commands.has_permissions(manage_channels=True)
    @discord.app_commands.describe(channel="Channel to lock (optional, defaults to current)")
    async def lock_cmd(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        target = channel or ctx.channel
        overwrite = target.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await target.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await log_action(self.bot, "🔒 Channel Locked",
                         f"**Channel:** {target.mention}\n**Moderator:** {ctx.author.mention}")
        await ctx.send(f"🔒 {target.mention} has been locked.", delete_after=10)

    @commands.hybrid_command(name="unlock", description="Unlock a channel")
    @commands.has_permissions(manage_channels=True)
    @discord.app_commands.describe(channel="Channel to unlock (optional, defaults to current)")
    async def unlock_cmd(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        target = channel or ctx.channel
        overwrite = target.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await target.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await log_action(self.bot, "🔓 Channel Unlocked",
                         f"**Channel:** {target.mention}\n**Moderator:** {ctx.author.mention}")
        await ctx.send(f"🔓 {target.mention} has been unlocked.", delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
