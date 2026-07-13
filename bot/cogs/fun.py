"""
Fun Cog
───────
!roll, !flip, !8ball, !poll, !avatar, !botinfo, !snipe, !afk
"""

from __future__ import annotations

import datetime
import random
from typing import Optional

import discord
from discord.ext import commands

from config import BOT_COLOR

_8BALL_ANSWERS = [
    # Positive
    "Definitely! ✅", "Yes! 😄", "Looks good! 👍", "For sure! 🌟",
    "Absolutely! 🚀", "Without a doubt! ✨",
    # Neutral
    "Maybe… 🤔", "Hard to say. 🌀", "Could go either way! ⚖️",
    "Ask again later. ⏳", "Not sure yet. 🤷",
    # Negative
    "Probably not. 😬", "Don't count on it. ❌", "Nope! 🙅",
    "I wouldn't bet on it. 🎲",
]


class Fun(commands.Cog, name="Fun"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # channel_id → last deleted message
        self._snipe_cache: dict[int, discord.Message] = {}
        # user_id → afk reason
        self._afk: dict[int, str] = {}

    # ── Snipe cache ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.author.bot:
            self._snipe_cache[message.channel.id] = message

    # ── AFK auto-clear ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        # Clear AFK if the user sends a message
        if message.author.id in self._afk:
            reason = self._afk.pop(message.author.id)
            try:
                await message.reply(f"Welcome back! 👋 Removed your AFK: *{reason}*", delete_after=10)
            except discord.HTTPException:
                pass
        # Notify if a user pinged someone who is AFK
        for user in message.mentions:
            if user.id in self._afk:
                reason = self._afk[user.id]
                try:
                    await message.reply(
                        f"**{user.display_name}** is AFK: *{reason}*", delete_after=15
                    )
                except discord.HTTPException:
                    pass

    # ── !roll ──────────────────────────────────────────────────────────────────

    @commands.command(name="roll")
    async def roll(self, ctx: commands.Context, sides: int = 6) -> None:
        """Roll a die. Usage: !roll [sides]"""
        if sides < 2 or sides > 1000:
            await ctx.send("❌ Sides must be between 2 and 1000.", delete_after=8)
            return
        result = random.randint(1, sides)
        await ctx.send(
            embed=discord.Embed(
                title="🎲 Dice Roll",
                description=f"**{result}** / {sides}",
                color=BOT_COLOR,
            )
        )

    # ── !flip ──────────────────────────────────────────────────────────────────

    @commands.command(name="flip")
    async def flip(self, ctx: commands.Context) -> None:
        """Flip a coin."""
        result = random.choice(["Heads 🪙", "Tails 🔵"])
        await ctx.send(
            embed=discord.Embed(title="🪙 Coin Flip", description=f"**{result}**", color=BOT_COLOR)
        )

    # ── !8ball ─────────────────────────────────────────────────────────────────

    @commands.command(name="8ball")
    async def eightball(self, ctx: commands.Context, *, question: str) -> None:
        """Ask the magic 8-ball a question."""
        answer = random.choice(_8BALL_ANSWERS)
        embed = discord.Embed(color=BOT_COLOR)
        embed.add_field(name="❓ Question", value=question[:200], inline=False)
        embed.add_field(name="🎱 Answer", value=answer, inline=False)
        await ctx.send(embed=embed)

    # ── !poll ──────────────────────────────────────────────────────────────────

    @commands.command(name="poll")
    async def poll(self, ctx: commands.Context, *, question: str) -> None:
        """Start a quick yes/no poll. Usage: !poll <question>"""
        embed = discord.Embed(
            title="📊 Poll",
            description=f"**{question[:200]}**\n\nVote below!",
            color=BOT_COLOR,
        )
        embed.set_footer(text=f"Started by {ctx.author.display_name}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # ── !avatar ────────────────────────────────────────────────────────────────

    @commands.command(name="avatar")
    async def avatar(self, ctx: commands.Context, *, member: Optional[discord.Member] = None) -> None:
        """Show a user's avatar. Usage: !avatar [user]"""
        target = member or ctx.author
        embed = discord.Embed(title=f"{target.display_name}'s Avatar", color=BOT_COLOR)
        embed.set_image(url=target.display_avatar.url)
        embed.add_field(
            name="Links",
            value=f"[PNG]({target.display_avatar.with_format('png').url}) • "
                  f"[JPG]({target.display_avatar.with_format('jpg').url}) • "
                  f"[WEBP]({target.display_avatar.with_format('webp').url})",
        )
        await ctx.send(embed=embed)

    # ── !botinfo ───────────────────────────────────────────────────────────────

    @commands.command(name="botinfo")
    async def botinfo(self, ctx: commands.Context) -> None:
        """Show Nexus bot information."""
        cmd_count = len(self.bot.commands)
        cog_count = len(self.bot.cogs)
        guild_count = len(self.bot.guilds)
        latency = round(self.bot.latency * 1000)

        embed = discord.Embed(title="🤖 Nexus — Bot Info", color=BOT_COLOR)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Commands", value=str(cmd_count), inline=True)
        embed.add_field(name="Modules", value=str(cog_count), inline=True)
        embed.add_field(name="Servers", value=str(guild_count), inline=True)
        embed.add_field(name="Ping", value=f"{latency}ms", inline=True)
        embed.add_field(name="AI Model", value="Gemini Flash", inline=True)
        embed.add_field(name="Prefix", value="`!`", inline=True)
        embed.set_footer(text="Powered by Gemini • discord.py")
        await ctx.send(embed=embed)

    # ── !snipe ─────────────────────────────────────────────────────────────────

    @commands.command(name="snipe")
    @commands.has_permissions(manage_messages=True)
    async def snipe(self, ctx: commands.Context) -> None:
        """Show the last deleted message in this channel. (Staff only)"""
        msg = self._snipe_cache.get(ctx.channel.id)
        if msg is None:
            await ctx.send("Nothing to snipe! 🤷", delete_after=8)
            return
        embed = discord.Embed(
            description=msg.content[:1000] or "*[no text content]*",
            color=0xED4245,
            timestamp=msg.created_at,
        )
        embed.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
        embed.set_footer(text=f"Deleted in #{ctx.channel.name}")
        await ctx.send(embed=embed)

    # ── !afk ──────────────────────────────────────────────────────────────────

    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK") -> None:
        """Set yourself as AFK. Usage: !afk [reason]"""
        self._afk[ctx.author.id] = reason[:100]
        await ctx.send(
            f"✅ You're now AFK: *{reason[:100]}*. I'll let people know! 💤",
            delete_after=10,
        )
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    # ── !choose ────────────────────────────────────────────────────────────────

    @commands.command(name="choose")
    async def choose(self, ctx: commands.Context, *, options: str) -> None:
        """Pick one of your options. Usage: !choose option1 | option2 | option3"""
        choices = [o.strip() for o in options.split("|") if o.strip()]
        if len(choices) < 2:
            await ctx.send("❌ Give me at least 2 options separated by `|`.", delete_after=8)
            return
        picked = random.choice(choices)
        await ctx.send(
            embed=discord.Embed(
                title="🎯 I choose…",
                description=f"**{picked}**",
                color=BOT_COLOR,
            )
        )

    # ── !rps ──────────────────────────────────────────────────────────────────

    @commands.command(name="rps")
    async def rps(self, ctx: commands.Context, choice: str) -> None:
        """Rock Paper Scissors. Usage: !rps <rock|paper|scissors>"""
        choice = choice.lower()
        if choice not in ("rock", "paper", "scissors"):
            await ctx.send("❌ Choose `rock`, `paper`, or `scissors`!", delete_after=8)
            return
        bot_choice = random.choice(["rock", "paper", "scissors"])
        icons = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
        wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

        if choice == bot_choice:
            outcome, color = "It's a tie! 🤝", BOT_COLOR
        elif wins[choice] == bot_choice:
            outcome, color = "You win! 🎉", 0x23A55A
        else:
            outcome, color = "I win! 😄", 0xED4245

        embed = discord.Embed(title="✂️ Rock Paper Scissors", color=color)
        embed.add_field(name="You", value=f"{icons[choice]} {choice.title()}", inline=True)
        embed.add_field(name="Nexus", value=f"{icons[bot_choice]} {bot_choice.title()}", inline=True)
        embed.add_field(name="Result", value=outcome, inline=False)
        await ctx.send(embed=embed)

    # ── !math ─────────────────────────────────────────────────────────────────

    @commands.command(name="math")
    async def math_cmd(self, ctx: commands.Context, *, expression: str) -> None:
        """Evaluate a safe math expression. Usage: !math 2 + 2"""
        allowed = set("0123456789+-*/(). ")
        if not all(c in allowed for c in expression):
            await ctx.send("❌ Only basic math operators allowed.", delete_after=8)
            return
        try:
            result = eval(expression, {"__builtins__": {}})  # noqa: S307
            await ctx.send(
                embed=discord.Embed(
                    description=f"`{expression[:80]}` = **{result}**",
                    color=BOT_COLOR,
                )
            )
        except Exception:
            await ctx.send("❌ Couldn't calculate that.", delete_after=8)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
