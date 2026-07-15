"""
Fun Cog (Slash Commands)
"""

from __future__ import annotations

import random
import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_COLOR

_8BALL = ["Yes! ✅", "Definitely! ✅", "Maybe 🤔", "Ask later ⏳", "Nope ❌", "Don't count on it ❌"]

class Fun(commands.Cog, name="Fun"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._snipe = {}
        self._afk = {}

    @commands.Cog.listener()
    async def on_message_delete(self, msg: discord.Message) -> None:
        if not msg.author.bot:
            self._snipe[msg.channel.id] = msg

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message) -> None:
        if msg.author.bot:
            return
        if msg.author.id in self._afk:
            reason = self._afk.pop(msg.author.id)
            try:
                await msg.reply(f"Welcome back! 👋 Removed AFK: *{reason}*", delete_after=10)
            except:
                pass
        for user in msg.mentions:
            if user.id in self._afk:
                try:
                    await msg.reply(f"**{user.display_name}** is AFK: *{self._afk[user.id]}*", delete_after=15)
                except:
                    pass

    @app_commands.command(name="roll", description="Roll a die.")
    @app_commands.describe(sides="Number of sides (default 6)")
    async def roll(self, interaction: discord.Interaction, sides: int = 6) -> None:
        if sides < 2 or sides > 1000:
            await interaction.response.send_message("❌ Sides must be 2-1000", ephemeral=True)
            return
        result = random.randint(1, sides)
        embed = discord.Embed(title=f"🎲 d{sides}", description=f"**{result}**", color=BOT_COLOR)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="flip", description="Flip a coin.")
    async def flip(self, interaction: discord.Interaction) -> None:
        result = random.choice(["Heads 🪙", "Tails 🪙"])
        embed = discord.Embed(title="Coin Flip", description=result, color=BOT_COLOR)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="8ball", description="Ask the magic 8-ball.")
    @app_commands.describe(question="Your question")
    async def eight_ball(self, interaction: discord.Interaction, question: str) -> None:
        answer = random.choice(_8BALL)
        embed = discord.Embed(title="🎱 Magic 8-Ball", description=f"**Q:** {question}\n**A:** {answer}", color=BOT_COLOR)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="poll", description="Create a yes/no poll.")
    @app_commands.describe(question="Poll question")
    async def poll(self, interaction: discord.Interaction, question: str) -> None:
        embed = discord.Embed(title="📊 Poll", description=question, color=BOT_COLOR)
        msg = await interaction.response.send_message(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

    @app_commands.command(name="avatar", description="Show avatar.")
    @app_commands.describe(user="User (optional)")
    async def avatar(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        embed = discord.Embed(title=f"{target.display_name}'s Avatar", color=BOT_COLOR)
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="botinfo", description="Bot stats.")
    async def botinfo(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="🤖 Bot Info", color=BOT_COLOR)
        embed.add_field(name="Guilds", value=len(self.bot.guilds), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="snipe", description="Last deleted message.")
    async def snipe(self, interaction: discord.Interaction) -> None:
        msg = self._snipe.get(interaction.channel_id)
        if not msg:
            await interaction.response.send_message("❌ No deleted messages", ephemeral=True)
            return
        embed = discord.Embed(description=msg.content or "*[empty]*", color=BOT_COLOR)
        embed.set_author(name=str(msg.author), icon_url=msg.author.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="afk", description="Set AFK.")
    @app_commands.describe(reason="AFK reason")
    async def afk(self, interaction: discord.Interaction, reason: str | None = None) -> None:
        reason = reason or "AFK"
        self._afk[interaction.user.id] = reason
        await interaction.response.send_message(f"✅ AFK: *{reason}*", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
