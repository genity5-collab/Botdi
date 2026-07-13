"""
Moderation Cog
──────────────
Commands : !strike, !kick, !ban, !strikes
Auto-mod  : Blacklisted words → 1 h timeout (15 s filter cooldown per user)
Logic     : 1 strike = 24 h timeout  |  3 strikes = ban
            All actions logged to LOG_CHANNEL_ID.
            Violating users receive a branded appeal DM.
"""

from __future__ import annotations

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
)
from data_store import add_strike, get_strikes, reset_strikes
from utils import build_appeal_embed, log_action, parse_user_id

# Pre-compile lower-case word set for fast lookup
_BLACKLIST: set[str] = {w.lower() for w in BLACKLISTED_WORDS}


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
        """
        Add a strike. Applies:
          - 1+ strike → 24 h timeout
          - 3+ strikes → ban
        Logs the action and DMs the user an appeal embed.
        """
        total = await add_strike(target.id)

        # Fetch member if we only have a User object
        member: discord.Member | None = guild.get_member(target.id)
        if member is None:
            try:
                member = await guild.fetch_member(target.id)
            except discord.NotFound:
                member = None

        action_taken = f"Strike {total} issued"

        if total >= STRIKES_FOR_BAN:
            # Ban
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
            # 24 h timeout per strike
            until = discord.utils.utcnow() + datetime.timedelta(seconds=STRIKE_TIMEOUT_SECONDS)
            try:
                await member.timeout(until, reason=f"Strike {total}: {reason}")
                action_taken = f"Strike {total} — 24 h timeout applied"
            except discord.Forbidden:
                action_taken = f"Strike {total} issued (timeout failed — missing permissions)"

        # Log
        await log_action(
            self.bot,
            "⚠️ Strike Issued",
            f"**User:** {target.mention} (`{target.id}`)\n"
            f"**Moderator:** {moderator.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Outcome:** {action_taken}",
        )

        # Appeal DM
        try:
            dm = await target.create_dm()
            await dm.send(embed=build_appeal_embed(reason))
        except discord.HTTPException:
            pass

    # ── Auto-mod listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not _BLACKLIST:
            return

        now = time.monotonic()
        last = self._filter_cooldowns.get(message.author.id, 0.0)
        if now - last < FILTER_COOLDOWN_SECONDS:
            return
        self._filter_cooldowns[message.author.id] = now

        lower_content = message.content.lower()
        triggered = next((w for w in _BLACKLIST if w in lower_content), None)
        if triggered is None:
            return

        member = message.author
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        # 1 h timeout
        until = discord.utils.utcnow() + datetime.timedelta(seconds=AUTOMOD_TIMEOUT_SECONDS)
        action_taken = "1 h timeout applied"
        try:
            await member.timeout(until, reason=f"Auto-mod: blacklisted word '{triggered}'")
        except discord.Forbidden:
            action_taken = "timeout failed (missing permissions)"

        try:
            await member.send(
                embed=discord.Embed(
                    title="🔇 Auto-Moderation",
                    description=(
                        f"Your message was removed for containing a prohibited word.\n"
                        f"You have been muted for **1 hour**."
                    ),
                    color=0xE74C3C,
                )
            )
        except discord.HTTPException:
            pass

        await log_action(
            self.bot,
            "🔇 Auto-Mod Timeout",
            f"**User:** {member.mention} (`{member.id}`)\n"
            f"**Trigger:** `{triggered}`\n"
            f"**Action:** {action_taken}",
        )

    # ── !strike ───────────────────────────────────────────────────────────────

    @commands.command(name="strike")
    @commands.has_permissions(moderate_members=True)
    async def strike_cmd(self, ctx: commands.Context, target_str: str, *, reason: str = "No reason provided") -> None:
        """!strike <user_id|@mention> [reason]"""
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
        await ctx.send(
            f"✅ Strike issued to {target.mention}. They now have **{total}** strike(s).",
            delete_after=15,
        )

    # ── !kick ────────────────────────────────────────────────────────────────

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def kick_cmd(self, ctx: commands.Context, target_str: str, *, reason: str = "No reason provided") -> None:
        """!kick <user_id|@mention> [reason]"""
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

        await log_action(
            self.bot,
            "👢 Member Kicked",
            f"**User:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {ctx.author.mention}\n"
            f"**Reason:** {reason}",
        )
        await ctx.send(f"✅ **{member}** has been kicked.", delete_after=15)

    # ── !ban ──────────────────────────────────────────────────────────────────

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban_cmd(self, ctx: commands.Context, target_str: str, *, reason: str = "No reason provided") -> None:
        """!ban <user_id|@mention> [reason]"""
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

        await log_action(
            self.bot,
            "🔨 Member Banned",
            f"**User:** {target.mention} (`{target.id}`)\n"
            f"**Moderator:** {ctx.author.mention}\n"
            f"**Reason:** {reason}",
        )
        await ctx.send(f"✅ **{target}** has been banned.", delete_after=15)

    # ── !strikes ──────────────────────────────────────────────────────────────

    @commands.command(name="strikes")
    @commands.has_permissions(moderate_members=True)
    async def strikes_cmd(self, ctx: commands.Context, target_str: str) -> None:
        """!strikes <user_id|@mention>"""
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

    # ── Error handlers ────────────────────────────────────────────────────────

    @strike_cmd.error
    @kick_cmd.error
    @ban_cmd.error
    @strikes_cmd.error
    async def mod_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`.", delete_after=10)
        else:
            await ctx.send(f"❌ Error: {error}", delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
