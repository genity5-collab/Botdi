"""
Fun Cog — Useful utility commands (cleaned up — removed roll/flip/8ball/snipe).

Kept: /poll, /avatar, /botinfo, /afk
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


class Fun(commands.Cog, name="Utility"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._afk: dict[int, str] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        # Remove AFK status when user sends a message
        if message.author.id in self._afk:
            del self._afk[message.author.id]
            try:
                await message.reply("Welcome back! You're no longer AFK.", delete_after=5)
            except discord.HTTPException:
                pass
        # Mention AFK users
        for user in message.mentions:
            if user.id in self._afk:
                reason = self._afk[user.id]
                try:
                    await message.reply(f"💤 **{user.display_name}** is AFK: {reason}", delete_after=10)
                except discord.HTTPException:
                    pass

    @app_commands.command(name="poll", description="Create a yes/no poll.")
    @app_commands.describe(question="Poll question")
    async def poll(self, interaction: discord.Interaction, question: str) -> None:
        embed = discord.Embed(
            title="📊 Poll",
            description=f"**{question}**\n\n✅ Yes • ❌ No",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Asked by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

    @app_commands.command(name="avatar", description="Show a user's avatar in full size.")
    @app_commands.describe(user="User (optional, defaults to you)")
    async def avatar(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        embed = discord.Embed(
            title=f"🖼️ {target.display_name}'s Avatar",
            color=0x5865F2,
        )
        embed.set_image(url=target.display_avatar.url)
        embed.add_field(name="PNG", value=f"[Link]({target.display_avatar.replace(format='png', size=4096).url})")
        embed.add_field(name="User ID", value=f"`{target.id}`")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="botinfo", description="Show bot information and stats.")
    async def botinfo(self, interaction: discord.Interaction) -> None:
        bot = self.bot
        embed = discord.Embed(
            title="🤖 Vyrion",
            description="Your AI assistant for Discord — chat, site engineering, moderation, and more.",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
        embed.add_field(name="Users", value=str(sum(g.member_count or 0 for g in bot.guilds)), inline=True)
        embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Commands", value="/site /create /strike /poll /avatar /help", inline=False)
        embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else None)
        embed.set_footer(text="Vyrion • Powered by gpt-oss-20b & Gemini")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="afk", description="Set yourself as AFK.")
    @app_commands.describe(reason="Why are you AFK? (optional)")
    async def afk(self, interaction: discord.Interaction, reason: str = "No reason provided") -> None:
        self._afk[interaction.user.id] = reason
        await interaction.response.send_message(
            f"💤 {interaction.user.mention} is now AFK: **{reason}**", delete_after=10
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
