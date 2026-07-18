"""
Moderation Cog
──────────────
Commands : !strike, !kick, !ban, !strikes, !mute, !unmute, !warn, !purge, !slowmode, !lock, !unlock
Auto-mod  : Blacklisted words → 1 h timeout (15 s filter cooldown per user)
Logic     : 1 strike = 24 h timeout  |  3 strikes = ban
            All actions logged to LOG_CHANNEL_ID and Studio Dashboard.
            Violating users receive a branded appeal DM.
"""

from __future__ import annotations

import re
import time
import datetime
import discord
from discord.ext import commands

from config import (
    STRIKES_FOR_BAN,
    STRIKE_TIMEOUT_SECONDS,
    AUTOMOD_TIMEOUT_SECONDS,
    FILTER_COOLDOWN_SECONDS,
    BLACKLISTED_WORDS,
    BOT_COLOR,
    COLOR_ERR,
    COLOR_WARN,
    COLOR_OK,
)
from data_store import add_strike, get_strikes, reset_strikes
from utils import build_appeal_embed, log_action, parse_user_id
import studio_sync

# Pre-compile lower-case word list for fast lookup
_BLACKLIST: list[str] = [w.lower() for w in BLACKLISTED_WORDS]

# Emoji regex — Discord custom (<:name:id>) + common unicode emoji ranges
_EMOJI_RE = re.compile(
    r"<a?:[A-Za-z0-9_]+:\d+>"       # Discord custom emoji
    r"|[\U0001F000-\U0001FAFF]"      # Extended emoji block
    r"|[\U00010000-\U0010FFFF]"      # Supplementary (misc emoji)
    r"|[\u2600-\u27BF]"              # Misc symbols
    r"|[\u2300-\u23FF]",             # Misc technical
    re.UNICODE,
)

def _strip_content(text: str) -> str:
    """Remove emojis, mentions, URLs, and extra whitespace from a message."""
    text = _EMOJI_RE.sub(" ", text)
    text = re.sub(r"<@!?\d+>", " ", text)          # user mentions
    text = re.sub(r"<#\d+>", " ", text)             # channel mentions
    text = re.sub(r"https?://\S+", " ", text)        # URLs
    return text.strip()

def _find_blacklisted(text: str) -> str | None:
    """
    Return the first blacklisted word found in text using whole-word matching.
    Returns None if no match (ignores partial matches like 'ass' in 'class').
    """
    clean = _strip_content(text).lower()
    if len(clean) < 2:   # emoji-only or nearly empty after stripping
        return None
    for word in _BLACKLIST:
        if re.search(r"\b" + re.escape(word) + r"\b", clean):
            return word
    return None


class Moderation(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # user_id -> last automod-check UNIX timestamp (15 s cooldown)
        self._filter_cooldowns: dict[int, float] = {}

    # ── Internal: apply a strike and enforce escalation ────────────────────────

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

        action_taken = f"Strike {total} issued"

        if total >= STRIKES_FOR_BAN:
            await reset_strikes(target.id)
            try:
                if member:
                    await member.ban(reason=f"[Auto] {STRIKES_FOR_BAN} strikes — {reason}", delete_message_days=0)
                else:
                    await guild.ban(discord.Object(id=target.id), reason=f"[Auto] {reason}")
                action_taken = f"Banned (reached {STRIKES_FOR_BAN} strikes)"
            except discord.Forbidden:
                action_taken = f"Strike {total} — ban failed (missing permissions)"
        elif member:
            until = discord.utils.utcnow() + datetime.timedelta(seconds=STRIKE_TIMEOUT_SECONDS)
            try:
                await member.timeout(until, reason=f"Strike {total}: {reason}")
                action_taken = f"Strike {total} — 24 h timeout applied"
            except discord.Forbidden:
                action_taken = f"Strike {total} issued (timeout failed — missing permissions)"

        await log_action(
            self.bot,
            "⚠️ Strike Issued",
            f"**User:** {target.mention} (`{target.id}`)\n"
            f"**Moderator:** {moderator.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Outcome:** {action_taken}",
        )

        studio_sync.log_automod(
            guild_id=guild.id,
            channel_id=None,
            user_discord_id=target.id,
            severity="high" if total >= STRIKES_FOR_BAN else "medium",
            category="other",
            action_taken="ban" if total >= STRIKES_FOR_BAN else "timeout",
            content_snippet=reason[:200],
        )
        studio_sync.log_edit(
            action_type="strike",
            action_category="config",
            target=f"user:{target.id}",
            after_value={"strikes": total, "action": action_taken},
            triggered_by="moderator",
            triggered_by_name=str(moderator),
            status="success",
            guild_id=str(guild.id),
        )

        try:
            dm = await target.create_dm()
            await dm.send(embed=build_appeal_embed(reason))
        except discord.HTTPException:
            pass

    # ── Auto-mod listener ─────────────────────────────────────────────────────
    # Smart checks:
    #  • Strips emojis/mentions/URLs before scanning — emoji-only messages are ignored
    #  • Whole-word boundary matching — "ass" in "classic" does NOT trigger
    #  • 15 s cooldown per user to avoid spam-checking

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not _BLACKLIST:
            return

        now  = time.monotonic()
        last = self._filter_cooldowns.get(message.author.id, 0.0)
        if now - last < FILTER_COOLDOWN_SECONDS:
            return
        self._filter_cooldowns[message.author.id] = now

        triggered = _find_blacklisted(message.content)
        if triggered is None:
            return   # emoji-only, clean text, or no whole-word match

        member = message.author
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        until        = discord.utils.utcnow() + datetime.timedelta(seconds=AUTOMOD_TIMEOUT_SECONDS)
        action_taken = "1 h timeout applied"
        try:
            await member.timeout(until, reason=f"Auto-mod: prohibited word")
        except discord.Forbidden:
            action_taken = "timeout failed (missing permissions)"

        try:
            dm_embed = discord.Embed(
                title="🔇 Auto-Moderation",
                description=(
                    "Your message was removed because it contained a prohibited word.\n\n"
                    "**Action:** 1-hour timeout\n"
                    "Please review the server rules to avoid further action."
                ),
                color=COLOR_ERR,
                timestamp=discord.utils.utcnow(),
            )
            dm_embed.set_footer(text="Vyrion Auto-Mod • Repeated violations lead to a strike")
            await member.send(embed=dm_embed)
        except discord.HTTPException:
            pass

        await log_action(
            self.bot,
            "🔇 Auto-Mod Timeout",
            f"**User:** {member.mention} (`{member.id}`)\n"
            f"**Channel:** {message.channel.mention}\n"
            f"**Trigger:** `{triggered}`\n"
            f"**Action:** {action_taken}",
        )

        studio_sync.log_automod(
            guild_id=message.guild.id,
            channel_id=message.channel.id if hasattr(message.channel, "id") else None,
            user_discord_id=member.id,
            severity="medium",
            category="other",
            action_taken="timeout",
            content_snippet=triggered,
        )
        studio_sync.log_analytics(
            event_type="automod_action",
            event_category="automod",
            event_name="blacklisted_word_timeout",
            value={"trigger": triggered, "action": action_taken},
            success=True,
            guild_id=str(message.guild.id),
        )

    # ── !strike ───────────────────────────────────────────────────────────────

    @commands.command(name="strike")
    @commands.has_permissions(moderate_members=True)
    async def strike_cmd(self, ctx: commands.Context, target_str: str, *, reason: str = "No reason provided") -> None:
        """Issue a strike to a user."""
        uid = parse_user_id(target_str)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        try:
            target = await self.bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send("❌ User not found.", delete_after=10)
            return
        await self.apply_strike(ctx.guild, target, reason, ctx.author)
        total = await get_strikes(target.id)
        await ctx.send(f"✅ Strike issued to {target.mention}. They now have **{total}** strike(s).", delete_after=15)

    # ── !kick ─────────────────────────────────────────────────────────────────

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick_cmd(self, ctx: commands.Context, target_str: str, *, reason: str = "No reason provided") -> None:
        """Kick a member from the server."""
        uid = parse_user_id(target_str)
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
        studio_sync.log_edit(
            action_type="kick",
            action_category="config",
            target=f"user:{member.id}",
            after_value={"reason": reason[:200]},
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )
        await ctx.send(f"✅ **{member}** has been kicked.", delete_after=15)

    # ── !ban ──────────────────────────────────────────────────────────────────

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban_cmd(self, ctx: commands.Context, target_str: str, *, reason: str = "No reason provided") -> None:
        """Ban a user from the server."""
        uid = parse_user_id(target_str)
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
            await ctx.guild.ban(target, reason=f"{ctx.author}: {reason}", delete_message_days=0)
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to ban this user.", delete_after=10)
            return
        await log_action(self.bot, "🔨 Member Banned",
            f"**User:** {target.mention} (`{target.id}`)\n**Moderator:** {ctx.author.mention}\n**Reason:** {reason}")
        studio_sync.log_edit(
            action_type="ban",
            action_category="config",
            target=f"user:{target.id}",
            after_value={"reason": reason[:200]},
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )
        await ctx.send(f"✅ **{target}** has been banned.", delete_after=15)

    # ── !strikes ──────────────────────────────────────────────────────────────

    @commands.command(name="strikes")
    @commands.has_permissions(moderate_members=True)
    async def strikes_cmd(self, ctx: commands.Context, target_str: str) -> None:
        """View a user's strike count."""
        uid = parse_user_id(target_str)
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
            description=f"**{target}** (`{target.id}`) has **{total}** strike(s).",
            color=BOT_COLOR,
        )
        await ctx.send(embed=embed)

    # ── !mute ─────────────────────────────────────────────────────────────────

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def mute_cmd(self, ctx: commands.Context, target_str: str, minutes: int = 10, *, reason: str = "No reason provided") -> None:
        """Timeout a user. Usage: !mute <user> [minutes] [reason]"""
        uid = parse_user_id(target_str)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        if minutes < 1 or minutes > 40320:  # Discord max is 28 days
            await ctx.send("❌ Duration must be between 1 and 40320 minutes.", delete_after=10)
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
        try:
            await member.send(embed=discord.Embed(
                title="🔇 You have been muted",
                description=f"**Duration:** {minutes} minute(s)\n**Reason:** {reason}",
                color=0xE74C3C,
            ))
        except discord.HTTPException:
            pass
        await log_action(self.bot, "🔇 Member Muted",
            f"**User:** {member.mention} (`{member.id}`)\n**Moderator:** {ctx.author.mention}\n"
            f"**Duration:** {minutes}m\n**Reason:** {reason}")
        studio_sync.log_edit(
            action_type="mute",
            action_category="config",
            target=f"user:{member.id}",
            after_value={"duration_minutes": minutes, "reason": reason[:200]},
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )
        await ctx.send(f"✅ {member.mention} has been muted for **{minutes}** minute(s).", delete_after=15)

    # ── !unmute ───────────────────────────────────────────────────────────────

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def unmute_cmd(self, ctx: commands.Context, target_str: str) -> None:
        """Remove a timeout from a user."""
        uid = parse_user_id(target_str)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        member = ctx.guild.get_member(uid)
        if member is None:
            await ctx.send("❌ Member not found in this server.", delete_after=10)
            return
        try:
            await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        except discord.Forbidden:
            await ctx.send("❌ Missing permissions to remove timeout.", delete_after=10)
            return
        await log_action(self.bot, "🔊 Member Unmuted",
            f"**User:** {member.mention} (`{member.id}`)\n**Moderator:** {ctx.author.mention}", color=0x2ECC71)
        studio_sync.log_edit(
            action_type="unmute",
            action_category="config",
            target=f"user:{member.id}",
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )
        await ctx.send(f"✅ {member.mention} has been unmuted.", delete_after=15)

    # ── !warn ─────────────────────────────────────────────────────────────────

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def warn_cmd(self, ctx: commands.Context, target_str: str, *, reason: str = "No reason provided") -> None:
        """Send a warning DM to a user (no strike applied)."""
        uid = parse_user_id(target_str)
        if uid is None:
            await ctx.send("❌ Invalid user ID or mention.", delete_after=10)
            return
        try:
            target = await self.bot.fetch_user(uid)
        except discord.NotFound:
            await ctx.send("❌ User not found.", delete_after=10)
            return
        embed = discord.Embed(
            title="⚠️ Official Warning",
            description=(
                f"**Reason:** {reason}\n\n"
                "This is a formal warning. Further violations may result in a strike or ban."
            ),
            color=0xF0B132,
        )
        try:
            dm = await target.create_dm()
            await dm.send(embed=embed)
            dm_sent = True
        except discord.HTTPException:
            dm_sent = False
        await log_action(self.bot, "⚠️ Warning Issued",
            f"**User:** {target.mention} (`{target.id}`)\n**Moderator:** {ctx.author.mention}\n"
            f"**Reason:** {reason}\n**DM Sent:** {'Yes' if dm_sent else 'No (DMs closed)'}", color=0xF0B132)
        studio_sync.log_automod(
            guild_id=ctx.guild.id,
            channel_id=None,
            user_discord_id=target.id,
            severity="low",
            category="other",
            action_taken="warn",
            content_snippet=reason[:200],
        )
        status = "✅" if dm_sent else "⚠️ (DMs closed, warning not delivered)"
        await ctx.send(f"{status} Warning sent to {target.mention}.", delete_after=15)

    # ── !purge ────────────────────────────────────────────────────────────────

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    async def purge_cmd(self, ctx: commands.Context, count: int) -> None:
        """Delete recent messages. Usage: !purge <1-100>"""
        if count < 1 or count > 100:
            await ctx.send("❌ Count must be between 1 and 100.", delete_after=10)
            return
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=count)
        await log_action(self.bot, "🗑️ Messages Purged",
            f"**Channel:** {ctx.channel.mention}\n**Moderator:** {ctx.author.mention}\n**Count:** {len(deleted)}", color=0x9B59B6)
        studio_sync.log_edit(
            action_type="purge",
            action_category="delete",
            target=f"channel:{ctx.channel.id}",
            after_value={"count": len(deleted)},
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )
        msg = await ctx.send(f"✅ Deleted **{len(deleted)}** message(s).")
        await discord.utils.sleep_until(discord.utils.utcnow() + datetime.timedelta(seconds=5))
        try:
            await msg.delete()
        except discord.HTTPException:
            pass

    # ── !slowmode ─────────────────────────────────────────────────────────────

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    async def slowmode_cmd(self, ctx: commands.Context, seconds: int) -> None:
        """Set channel slowmode. Usage: !slowmode <seconds> (0 to disable)"""
        if seconds < 0 or seconds > 21600:
            await ctx.send("❌ Slowmode must be between 0 and 21600 seconds.", delete_after=10)
            return
        before = ctx.channel.slowmode_delay
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("✅ Slowmode disabled.", delete_after=10)
        else:
            await ctx.send(f"✅ Slowmode set to **{seconds}s**.", delete_after=10)
        await log_action(self.bot, "⏱️ Slowmode Changed",
            f"**Channel:** {ctx.channel.mention}\n**Moderator:** {ctx.author.mention}\n**Delay:** {seconds}s", color=0x3498DB)
        studio_sync.log_edit(
            action_type="slowmode",
            action_category="edit",
            target=f"channel:{ctx.channel.id}",
            before_value={"slowmode": before},
            after_value={"slowmode": seconds},
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )

    # ── !lock ─────────────────────────────────────────────────────────────────

    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    async def lock_cmd(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        """Lock a channel so no one can send messages."""
        ch = channel or ctx.channel
        overwrite = ch.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await ch.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ch.send(embed=discord.Embed(
            title="🔒 Channel Locked",
            description="This channel has been locked by a moderator.",
            color=0xE74C3C,
        ))
        if channel and channel != ctx.channel:
            await ctx.send(f"✅ {ch.mention} has been locked.", delete_after=10)
        await log_action(self.bot, "🔒 Channel Locked",
            f"**Channel:** {ch.mention}\n**Moderator:** {ctx.author.mention}")
        studio_sync.log_edit(
            action_type="lock_channel",
            action_category="permission",
            target=f"channel:{ch.id}",
            after_value={"send_messages": False},
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )

    # ── !unlock ───────────────────────────────────────────────────────────────

    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    async def unlock_cmd(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        """Unlock a channel."""
        ch = channel or ctx.channel
        overwrite = ch.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None  # reset to default
        await ch.set_permissions(ctx.guild.default_role, overwrite=overwrite)
        await ch.send(embed=discord.Embed(
            title="🔓 Channel Unlocked",
            description="This channel has been unlocked.",
            color=0x2ECC71,
        ))
        if channel and channel != ctx.channel:
            await ctx.send(f"✅ {ch.mention} has been unlocked.", delete_after=10)
        await log_action(self.bot, "🔓 Channel Unlocked",
            f"**Channel:** {ch.mention}\n**Moderator:** {ctx.author.mention}", color=0x2ECC71)
        studio_sync.log_edit(
            action_type="unlock_channel",
            action_category="permission",
            target=f"channel:{ch.id}",
            after_value={"send_messages": None},
            triggered_by="moderator",
            triggered_by_name=str(ctx.author),
            status="success",
            guild_id=str(ctx.guild.id),
        )

    # ── Error handlers ────────────────────────────────────────────────────────

    async def cog_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`.", delete_after=10)
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"❌ Invalid argument: {error}", delete_after=10)
        else:
            await ctx.send(f"❌ Error: {error}", delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
