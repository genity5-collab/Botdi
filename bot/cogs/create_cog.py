"""
Create Cog — /create command for server management.

Lets users with manage permissions create things via AI-like natural language:
  /create channel <name> [type]   — Create a text/voice/announcement channel
  /create category <name>         — Create a category
  /create role <name> [color]     — Create a role with optional color
  /create emoji <name> <url>      — Add an emoji from an image URL
  /create voice <name> [limit]    — Create a voice channel with optional user limit

Also: /create text <name> [topic] — Create a text channel with topic
"""
from __future__ import annotations

import discord
from discord.ext import commands

from config import BOT_COLOR, COLOR_OK, COLOR_ERR, COLOR_INFO
from utils import log_action


class CreateCog(commands.Cog, name="Create"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = discord.app_commands.Group(name="create", description="Create channels, roles, categories, and more")

    @group.command(name="channel", description="Create a text, voice, or announcement channel")
    @discord.app_commands.describe(
        name="Channel name",
        type="Channel type: text, voice, or announcement",
        category="Parent category (optional)",
    )
    @commands.has_permissions(manage_channels=True)
    async def create_channel(
        self, interaction: discord.Interaction,
        name: str,
        type: str = "text",
        category: discord.CategoryChannel | None = None,
    ) -> None:
        type = type.lower().strip()
        guild = interaction.guild
        try:
            if type == "voice":
                channel = await guild.create_voice_channel(name, category=category, reason=f"Created by {interaction.user}")
            elif type == "announcement":
                channel = await guild.create_text_channel(name, category=category, news=True, reason=f"Created by {interaction.user}")
            elif type == "text":
                channel = await guild.create_text_channel(name, category=category, reason=f"Created by {interaction.user}")
            else:
                await interaction.response.send_message(f"❌ Unknown type `{type}`. Use: text, voice, or announcement.", ephemeral=True)
                return
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Channel Created",
                         f"**Channel:** {channel.mention}\n**Type:** {type}\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created {type} channel {channel.mention}!")

    @group.command(name="text", description="Create a text channel with an optional topic")
    @discord.app_commands.describe(
        name="Channel name",
        topic="Channel topic (optional)",
        category="Parent category (optional)",
    )
    @commands.has_permissions(manage_channels=True)
    async def create_text(
        self, interaction: discord.Interaction,
        name: str,
        topic: str | None = None,
        category: discord.CategoryChannel | None = None,
    ) -> None:
        try:
            channel = await interaction.guild.create_text_channel(
                name, category=category, topic=topic, reason=f"Created by {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Text Channel Created",
                         f"**Channel:** {channel.mention}\n**Topic:** {topic or 'None'}\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created text channel {channel.mention}!")

    @group.command(name="voice", description="Create a voice channel with optional user limit")
    @discord.app_commands.describe(
        name="Channel name",
        limit="Max users (0 = unlimited, default 0)",
        category="Parent category (optional)",
    )
    @commands.has_permissions(manage_channels=True)
    async def create_voice(
        self, interaction: discord.Interaction,
        name: str,
        limit: int = 0,
        category: discord.CategoryChannel | None = None,
    ) -> None:
        if limit < 0 or limit > 99:
            await interaction.response.send_message("❌ Limit must be 0–99 (0 = unlimited).", ephemeral=True)
            return
        try:
            channel = await interaction.guild.create_voice_channel(
                name, category=category, user_limit=limit, reason=f"Created by {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Voice Channel Created",
                         f"**Channel:** {channel.mention}\n**Limit:** {limit or 'unlimited'}\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created voice channel {channel.mention}!")

    @group.command(name="category", description="Create a category")
    @discord.app_commands.describe(name="Category name")
    @commands.has_permissions(manage_channels=True)
    async def create_category(self, interaction: discord.Interaction, name: str) -> None:
        try:
            category = await interaction.guild.create_category(name, reason=f"Created by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Category Created",
                         f"**Category:** {category.name}\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created category **{category.name}**!")

    @group.command(name="role", description="Create a role with optional color")
    @discord.app_commands.describe(
        name="Role name",
        color="Hex color (e.g. #FF0000) or leave empty for default",
        mentionable="Whether the role can be @mentioned (default true)",
    )
    @commands.has_permissions(manage_roles=True)
    async def create_role(
        self, interaction: discord.Interaction,
        name: str,
        color: str | None = None,
        mentionable: bool = True,
    ) -> None:
        role_color = discord.Color.default()
        if color:
            try:
                hex_str = color.lstrip("#")
                role_color = discord.Color(int(hex_str, 16))
            except (ValueError, TypeError):
                await interaction.response.send_message("❌ Invalid color. Use hex format like `#FF0000`.", ephemeral=True)
                return
        try:
            role = await interaction.guild.create_role(
                name=name, color=role_color, mentionable=mentionable,
                reason=f"Created by {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Role Created",
                         f"**Role:** {role.mention}\n**Color:** {color or 'default'}\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created role {role.mention}!")

    @group.command(name="emoji", description="Add an emoji from an image URL")
    @discord.app_commands.describe(name="Emoji name (without colons)", url="Direct image URL (png/jpg/gif)")
    @commands.has_permissions(manage_emojis=True)
    async def create_emoji(self, interaction: discord.Interaction, name: str, url: str) -> None:
        import aiohttp
        name = name.replace(" ", "_").replace(":", "")
        if not name:
            await interaction.response.send_message("❌ Invalid emoji name.", ephemeral=True)
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await interaction.response.send_message("❌ Could not download image.", ephemeral=True)
                        return
                    image_data = await resp.read()
            emoji = await interaction.guild.create_custom_emoji(name=name, image=image_data, reason=f"Created by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions to manage emojis.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Emoji Created",
                         f"**Emoji:** :{name}:\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created emoji {emoji} `:{name}:`!")

    @group.command(name="stage", description="Create a stage channel")
    @discord.app_commands.describe(name="Channel name", category="Parent category (optional)")
    @commands.has_permissions(manage_channels=True)
    async def create_stage(
        self, interaction: discord.Interaction,
        name: str,
        category: discord.CategoryChannel | None = None,
    ) -> None:
        try:
            channel = await interaction.guild.create_stage_channel(name, category=category, reason=f"Created by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Stage Channel Created",
                         f"**Channel:** {channel.mention}\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created stage channel {channel.mention}!")

    @group.command(name="forum", description="Create a forum channel")
    @discord.app_commands.describe(name="Channel name", category="Parent category (optional)")
    @commands.has_permissions(manage_channels=True)
    async def create_forum(
        self, interaction: discord.Interaction,
        name: str,
        category: discord.CategoryChannel | None = None,
    ) -> None:
        try:
            channel = await interaction.guild.create_forum_channel(name, category=category, reason=f"Created by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await log_action(self.bot, "📝 Forum Channel Created",
                         f"**Channel:** {channel.mention}\n**Created by:** {interaction.user.mention}")
        await interaction.response.send_message(f"✅ Created forum channel {channel.mention}!")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CreateCog(bot))
