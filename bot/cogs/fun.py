"""
Fun Cog (Slash Commands)
────────────────────────
/roll, /flip, /8ball, /poll, /avatar, /botinfo, /snipe, /afk
"""

from __future__ import annotations

import datetime
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_COLOR

_8BALL_ANSWERS = [
    "Definitely! ✅", "Yes! 😄", "Looks good! 👍", "For sure! 🌟",
    "Absolutely! 🚀", "Without a doubt! ✨",
    "Maybe… 🤔", "Hard to say. 🌀", "Could go either way! ⚖️",
    "Ask again later. ⏳", "Not sure yet. 🤷",
    "Probably not. 😬", "Don't count on it. ❌", "Nope! 🙅",
    "I wouldn't bet on it. 🎲",
]


class Fun(commands.Cog, name="Fun"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._snipe_cache: dict[int, discord.Message] = {}
        self._afk: dict[int, str] = {}

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.author.bot:
            self._snipe_cache[message.channel.id] = message

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.author.id in self._afk:
            reason = self._afk.pop(message.author.id)
            try:
                await message.reply(f"Welcome back! 👋 Removed your AFK: *{reason}*", delete_after=10)
            except discord.HTTPException:
                pass
        for user in message.mentions:
            if user.id in self._afk:
                reason = self._afk[user.id]
                try:
                    await message.reply(
                        f"**{user.display_name}** is AFK: *{reason}*", delete_after=15
                    )
                except discord.HTTPException:
                    pass

    @app_commands.command(name="roll", description="Roll a die.")
    @app_commands.describe(sides="Number of sides (default 6)")
    async def roll(self, interaction: discord.Interaction, sides: int = 6) -> None:
        if sides < 2 or sides > 1000:
            await interaction.response.send_message(
                "❌ Sides must be between 2 and 1000.",
                ephemeral=True
            )
            return
        result = random.randint(1, sides)
        embed = discord.Embed(
            title=f"🎲 d{sides}",
            description=f"**{result}**",
            color=BOT_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="flip", description="Flip a coin.")
    async def flip(self, interaction: discord.Interaction) -> None:
        result = random.choice(["Heads 🪙", "Tails 🪙"])
        embed = discord.Embed(title="Coin Flip", description=result, color=BOT_COLOR)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="8ball", description="Ask the magic 8-ball a question.")
    @app_commands.describe(question="Your yes/no question")
    async def eight_ball(self, interaction: discord.Interaction, question: str) -> None:
        answer = random.choice(_8BALL_ANSWERS)
        embed = discord.Embed(
            title="🎱 Magic 8-Ball",
            description=f"**Q:** {question}\n**A:** {answer}",
            color=BOT_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="poll", description="Create a yes/no poll.")
    @app_commands.describe(question="Your poll question")
    async def poll(self, interaction: discord.Interaction, question: str) -> None:
        embed = discord.Embed(
            title="📊 Poll",
            description=question,
            color=BOT_COLOR,
        )
        embed.set_footer(text=f"Poll by {interaction.user.display_name}")
        msg = await interaction.response.send_message(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

    @app_commands.command(name="avatar", description="Show a user's avatar.")
    @app_commands.describe(user="User to show avatar of")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        embed = discord.Embed(title=f"{target.display_name}'s Avatar", color=BOT_COLOR)
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="botinfo", description="Show bot statistics.")
    async def botinfo(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="🤖 Bot Info", color=BOT_COLOR)
        embed.add_field(name="Name", value=str(self.bot.user), inline=True)
        embed.add_field(name="ID", value=self.bot.user.id, inline=True)
        embed.add_field(name="Guilds", value=len(self.bot.guilds), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="snipe", description="Show the last deleted message.")
    async def snipe(self, interaction: discord.Interaction) -> None:
        msg = self._snipe_cache.get(interaction.channel_id)
        if not msg:
            await interaction.response.send_message(
                "❌ No deleted messages to snipe.",
                ephemeral=True
            )
            return
        embed = discord.Embed(
            title="💬 Sniped Message",
            description=msg.content or "*[No text content]*",
            color=BOT_COLOR,
            timestamp=msg.created_at,
        )
        embed.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="afk", description="Set an AFK status.")
    @app_commands.describe(reason="AFK reason")
    async def afk(self, interaction: discord.Interaction, reason: str | None = None) -> None:
        reason = reason or "AFK"
        self._afk[interaction.user.id] = reason
        await interaction.response.send_message(
            f"✅ You're now AFK: *{reason}*",
            ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
