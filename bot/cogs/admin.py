"""
Admin Cog — Server admin utility commands.

Commands:
  /embed <channel> <title> <description> — Post a branded embed to any channel
  /announce <channel> <message>         — Post an announcement
"""
from __future__ import annotations

import discord
from discord.ext import commands

from config import BOT_COLOR
from utils import log_action


class Admin(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="embed", description="Post a branded embed to a channel")
    @commands.has_permissions(administrator=True)
    @discord.app_commands.describe(
        channel="Channel to post to",
        title="Embed title",
        description="Embed description",
    )
    async def embed_cmd(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        title: str,
        *,
        description: str,
    ) -> None:
        embed = discord.Embed(title=title, description=description, color=BOT_COLOR)
        embed.set_footer(text=f"Posted by {ctx.author.display_name}")
        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.", delete_after=10)
            return

        await log_action(
            self.bot, "📢 Embed Posted",
            f"**Channel:** {channel.mention}\n**Admin:** {ctx.author.mention}\n**Title:** {title}",
            color=0x9B59B6,
        )
        await ctx.send(f"✅ Embed posted in {channel.mention}.", delete_after=10)

    @embed_cmd.error
    async def embed_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Administrator permission required.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❌ Usage: `/embed <channel> <title> <description>`",
                delete_after=15,
            )
        elif isinstance(error, commands.ChannelNotFound):
            await ctx.send("❌ Channel not found.", delete_after=10)
        else:
            await ctx.send(f"❌ Error: {error}", delete_after=10)

    @commands.hybrid_command(name="announce", description="Post an announcement to a channel")
    @commands.has_permissions(administrator=True)
    @discord.app_commands.describe(
        channel="Channel to announce in",
        message="Announcement text",
    )
    async def announce_cmd(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        *,
        message: str,
    ) -> None:
        embed = discord.Embed(
            title="📢 Announcement",
            description=message,
            color=BOT_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Announced by {ctx.author.display_name}")
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(roles=True, everyone=True))
        except discord.Forbidden:
            await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.", delete_after=10)
            return

        await log_action(
            self.bot, "📢 Announcement Posted",
            f"**Channel:** {channel.mention}\n**Admin:** {ctx.author.mention}",
        )
        await ctx.send(f"✅ Announcement posted in {channel.mention}.", delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
