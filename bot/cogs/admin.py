"""
Admin Cog
─────────
!embed <#channel> <title> <desc> — Post a branded embed to any channel.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from config import BOT_COLOR
from utils import log_action, parse_user_id


class Admin(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="embed")
    @commands.has_permissions(administrator=True)
    async def embed_cmd(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        title: str,
        *,
        desc: str,
    ) -> None:
        """
        !embed <#channel> <title> <description>
        Titles/descriptions with spaces must be quoted: !embed #chan "My Title" Some description here
        """
        embed = discord.Embed(title=title, description=desc, color=BOT_COLOR)
        embed.set_footer(text=f"Posted by {ctx.author}")

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.", delete_after=10)
            return

        await log_action(
            self.bot,
            "📢 Embed Posted",
            f"**Channel:** {channel.mention}\n"
            f"**Admin:** {ctx.author.mention}\n"
            f"**Title:** {title}",
            color=0x9B59B6,
        )
        await ctx.send(f"✅ Embed posted in {channel.mention}.", delete_after=10)

    @embed_cmd.error
    async def embed_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Administrator permission required.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❌ Usage: `!embed <#channel> <title> <description>`\n"
                'Wrap multi-word titles in quotes: `!embed #chan "My Title" Description here`',
                delete_after=15,
            )
        elif isinstance(error, commands.ChannelNotFound):
            await ctx.send("❌ Channel not found. Use a #mention or channel ID.", delete_after=10)
        else:
            await ctx.send(f"❌ Error: {error}", delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
