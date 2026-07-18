"""
Studio Cog — keeps the Supabase database in sync with Discord events.

Listens to guild join/leave, member join/leave, and provides the
/studio command for server owners to check their Studio sync status.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import studio_sync
from config import BOT_OWNER_ID

log = logging.getLogger("vyrion.studio_cog")


class StudioCog(commands.Cog, name="Studio"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        studio_sync.sync_guild(guild)
        studio_sync.log_analytics(
            event_type="guild_join",
            event_category="system",
            event_name="bot_added_to_guild",
            value={"guild_id": str(guild.id), "name": guild.name},
            guild_id=str(guild.id),
        )
        log.info("Synced new guild: %s (%s)", guild.name, guild.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        studio_sync.log_analytics(
            event_type="guild_remove",
            event_category="system",
            event_name="bot_removed_from_guild",
            value={"guild_id": str(guild.id), "name": guild.name},
            guild_id=str(guild.id),
        )
        log.info("Guild removed: %s (%s)", guild.name, guild.id)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        studio_sync.sync_guild(after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        studio_sync.sync_guild(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, guild: discord.Guild, member: discord.Member) -> None:
        studio_sync.sync_guild(guild)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild:
            studio_sync.sync_guild(channel.guild)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild:
            studio_sync.sync_guild(channel.guild)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        if role.guild:
            studio_sync.sync_guild(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if role.guild:
            studio_sync.sync_guild(role.guild)

    @app_commands.command(name="studio", description="Check Studio Dashboard sync status and get the dashboard link.")
    async def studio_cmd(self, interaction: discord.Interaction) -> None:
        is_owner = interaction.user.id == BOT_OWNER_ID
        is_guild_owner = interaction.guild and interaction.user.id == interaction.guild.owner_id

        embed = discord.Embed(
            title="Vyrion Studio Dashboard",
            color=0x5865F2,
        )
        embed.description = (
            "The Studio Dashboard lets you manage your AI Agent, SubAgent tasks, "
            "edit logs, analytics, and settings from a web interface."
        )
        embed.add_field(
            name="Sync Status",
            value="✅ Active — this server is being synced to the Studio Dashboard.",
            inline=False,
        )
        embed.add_field(
            name="Server",
            value=f"{interaction.guild.name} ({interaction.guild.member_count} members)" if interaction.guild else "DM",
            inline=True,
        )
        embed.add_field(
            name="Your Access",
            value="Bot Owner (full access)" if is_owner else ("Guild Owner" if is_guild_owner else "Member"),
            inline=True,
        )
        embed.add_field(
            name="SubAgent Limit",
            value="Infinite" if is_owner else "2 per week (8 with a custom API key)",
            inline=False,
        )
        embed.set_footer(text="Connect your Discord account at the Studio Dashboard to manage everything from the web.")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StudioCog(bot))
