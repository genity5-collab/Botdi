"""
Site Cog — /site slash command for App Engineering.

Pipeline: Prompt -> Plan -> Generate -> Build -> Test -> Debug -> Screenshot -> Preview -> Deliver

Completely separate from SubAgent mode. Own isolated project/build/preview workflow.
"""
from __future__ import annotations

import datetime
import io
import logging
import os

import discord
from discord.ext import commands

from config import BOT_COLOR, COLOR_ERR, COLOR_WARN, COLOR_OK, COLOR_INFO, SITE_FREE_MONTHLY_LIMIT
import site_store
import site_engine
from utils import log_action

log = logging.getLogger(__name__)


class SiteView(discord.ui.View):
    def __init__(self, project_id: str, files: dict[str, str], preview_url: str) -> None:
        super().__init__(timeout=300)
        self.project_id = project_id
        self.files = files
        self.preview_url = preview_url

    @discord.ui.button(label="Open Preview", style=discord.ButtonStyle.link, emoji="\U0001F310")
    async def open_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.url = self.preview_url
        pass

    @discord.ui.button(label="Download Project", style=discord.ButtonStyle.secondary, emoji="\U0001F4E6")
    async def download_project(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        project = await site_store.get_project(self.project_id)
        if not project or project["owner_id"] != interaction.user.id:
            await interaction.response.send_message("You can only download your own projects.", ephemeral=True)
            return
        zip_buf = site_engine._create_zip(self.files)
        await interaction.response.send_message(
            file=discord.File(zip_buf, filename="botdi-site.zip"),
            ephemeral=True,
        )

    @discord.ui.button(label="Edit Log", style=discord.ButtonStyle.secondary, emoji="\U0001F4DD")
    async def show_edit_log(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        project = await site_store.get_project(self.project_id)
        if not project:
            await interaction.response.send_message("Project not found.", ephemeral=True)
            return
        if project["owner_id"] != interaction.user.id:
            await interaction.response.send_message("You can only view your own project logs.", ephemeral=True)
            return
        entries = project.get("edit_log", [])
        if not entries:
            await interaction.response.send_message("No edit history.", ephemeral=True)
            return
        lines = []
        for entry in entries[-15:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            lines.append(f"`{ts}` — {entry['description']}")
        embed = discord.Embed(
            title="\U0001F4DD Edit Log",
            description="\n".join(lines) or "No edits yet.",
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"Project {self.project_id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SiteCog(commands.Cog, name="AppEngineering"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="site",
        description="Build a website from a description — AI generates, builds, and previews it",
    )
    @discord.app_commands.describe(
        description="What do you want to build? (e.g. 'modern portfolio website')",
        project_id="Edit an existing project by ID (optional)",
    )
    async def site_cmd(
        self,
        ctx: commands.Context,
        *,
        description: str,
        project_id: str | None = None,
    ) -> None:
        await ctx.defer()
        user = ctx.author
        user_gemini_key = await site_store.get_user_gemini_key(user.id)

        if not user_gemini_key:
            allowed, remaining = await site_store.check_site_usage(user.id)
            if not allowed:
                embed = discord.Embed(
                    title="\u26A0\uFE0F Free Limit Reached",
                    description=(
                        f"You've used your **{SITE_FREE_MONTHLY_LIMIT}** free `/site` messages this month.\n\n"
                        "Add your own supported API key to continue, subject to the provider's limits.\n\n"
                        "Use `/setkey` to configure your Gemini API key."
                    ),
                    color=COLOR_WARN,
                )
                await ctx.send(embed=embed)
                return

        is_edit = False
        target_project_id = project_id

        if project_id:
            project = await site_store.get_project(project_id)
            if not project:
                await ctx.send(embed=discord.Embed(description=f"\u274C Project `{project_id}` not found.", color=COLOR_ERR))
                return
            if project["owner_id"] != user.id:
                await ctx.send(embed=discord.Embed(description="\u274C You can only edit your own projects.", color=COLOR_ERR))
                return
            is_edit = True
        else:
            latest = await site_store.get_user_latest_project(user.id)
            if latest and latest["build_status"] == "success":
                edit_keywords = ["make it", "add", "fix", "change", "update", "remove",
                                 "make the", "dark mode", "mobile friendly", "blue",
                                 "navbar", "login", "animation", "button"]
                lower_desc = description.lower()
                if any(kw in lower_desc for kw in edit_keywords):
                    target_project_id = latest["id"]
                    is_edit = True

        if not user_gemini_key:
            remaining = await site_store.use_site_message(user.id)
        else:
            remaining = None

        progress_embed = discord.Embed(
            title="\U0001F3D7\uFE0F App Engineering" if not is_edit else "\u270F\uFE0F Editing Project",
            description=(
                f"{'\U0001F9E0 Planning...' if not is_edit else '\U0001F9E0 Understanding edit...'}\n"
                "\u2699\uFE0F Generating files...\n"
                "\U0001F9EA Building...\n"
                "\U0001F527 Testing...\n"
                "\U0001F4F8 Capturing preview..."
            ),
            color=COLOR_INFO,
        )
        progress_embed.set_footer(text=f"Requested by {user.display_name}")
        progress_msg = await ctx.send(embed=progress_embed)

        try:
            if is_edit and target_project_id:
                result = await site_engine.edit_project(target_project_id, description, user_gemini_key)
            else:
                result = await site_engine.generate_project(description, user.id, user_gemini_key)
        except Exception as exc:
            log.error("[site] Pipeline error: %s", exc, exc_info=True)
            await progress_msg.edit(embed=discord.Embed(
                title="\u274C I couldn't generate the site.",
                description=f"An unexpected error occurred. The project has been preserved.\n`{str(exc)[:200]}`",
                color=COLOR_ERR,
            ))
            return

        if result is None:
            await progress_msg.edit(embed=discord.Embed(
                title="\u274C I couldn't generate the site.",
                description="All AI providers are currently unavailable.\nPlease try again in a moment.",
                color=COLOR_ERR,
            ))
            return

        if result["build_status"] == "failed":
            error = result.get("error", "Unknown error")
            await progress_msg.edit(embed=discord.Embed(
                title="\u274C Build failed",
                description=(
                    f"\U0001F527 Attempted automatic repair...\n\n"
                    f"\u26A0\uFE0F I couldn't automatically fix this error.\n"
                    f"The project has been preserved so you don't lose your work.\n\n"
                    f"**Error:** `{error[:300]}`"
                ),
                color=COLOR_ERR,
            ))
            return

        files = result["files"]
        preview_url = result["preview_url"]
        pid = result["project_id"]
        info = result.get("info", {})

        project = await site_store.get_project(pid)
        edit_entries = project.get("edit_log", []) if project else []
        log_lines = []
        for entry in edit_entries[-6:]:
            ts = entry["timestamp"][11:16]
            log_lines.append(f"{ts} — {entry['description']}")
        edit_log_text = "\n".join(log_lines) if log_lines else "No edits yet."

        info_lines = [
            f"**Contains:** {info.get('contains', 'HTML/CSS/JS')}",
            f"**Dependencies:** {info.get('dependencies', 0)}",
            f"**External requests:** {info.get('external_requests') or 'None'}",
            f"**API usage:** {info.get('api_usage', 'None')}",
            f"**Permissions:** {info.get('permissions', 'Sandbox only')}",
        ]

        result_embed = discord.Embed(
            title="\u2705 Site ready!",
            description=(
                f"**Project ID:** `{pid}`\n"
                f"{'**Summary:** ' + result.get('summary', '') + chr(10) if result.get('summary') else ''}"
            ),
            color=COLOR_OK,
            timestamp=discord.utils.utcnow(),
        )

        if result.get("screenshot"):
            result_embed.set_image(url="attachment://preview.png")

        result_embed.add_field(name="\U0001F4DD Edit Log", value=f"```\n{edit_log_text[:1000]}\n```", inline=False)

        warning_text = "\n".join(info_lines)
        if info.get("external_requests"):
            warning_text = "\u26A0\uFE0F **External API detected** — This project makes requests to external services.\n" + warning_text
        result_embed.add_field(name="\u26A0\uFE0F Project Info", value=warning_text, inline=False)

        footer = "Botdi App Engineering • GPT-OSS-20B"
        if remaining is not None:
            footer += f" • {remaining}/{SITE_FREE_MONTHLY_LIMIT} messages left this month"
        result_embed.set_footer(text=footer)

        view = SiteView(pid, files, preview_url)
        for child in view.children:
            if isinstance(child, discord.ui.Button) and child.label == "Open Preview":
                child.url = preview_url

        if result.get("screenshot"):
            screenshot_file = discord.File(io.BytesIO(result["screenshot"]), filename="preview.png")
            await progress_msg.edit(embed=result_embed, view=view, attachments=[screenshot_file])
        else:
            await progress_msg.edit(embed=result_embed, view=view)

        await log_action(
            self.bot, "\U0001F3D7\uFE0F Site Generated",
            f"**User:** {user.mention} (`{user.id}`)\n**Project:** `{pid}`\n**Prompt:** {description[:200]}",
            color=COLOR_OK,
        )

    @commands.hybrid_command(name="setkey", description="Set your own Gemini API key for /site (stored securely, never shared)")
    @discord.app_commands.describe(api_key="Your Gemini API key (starts with AIza)")
    async def setkey_cmd(self, ctx: commands.Context, *, api_key: str) -> None:
        if ctx.guild is not None:
            await ctx.send(embed=discord.Embed(description="\u274C For security, please use `/setkey` in a DM to Botdi.", color=COLOR_ERR), delete_after=10)
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return
        if not api_key.startswith("AIza"):
            await ctx.send(embed=discord.Embed(description="\u274C That doesn't look like a valid Gemini API key. It should start with `AIza`.", color=COLOR_ERR))
            return
        await site_store.set_user_gemini_key(ctx.author.id, api_key)
        await ctx.send(embed=discord.Embed(
            title="\u2705 API Key Saved",
            description=(
                "Your Gemini API key has been stored securely.\n\n"
                "• It will be used for your `/site` requests only.\n"
                "• It is never shared, sent to Discord, or embedded in projects.\n"
                "• Your free Botdi limit no longer applies — you're subject to your provider's limits.\n\n"
                "Use `/removekey` to delete it at any time."
            ),
            color=COLOR_OK,
        ))

    @commands.hybrid_command(name="removekey", description="Remove your stored Gemini API key")
    async def removekey_cmd(self, ctx: commands.Context) -> None:
        if ctx.guild is not None:
            await ctx.send("\u274C For security, please use `/removekey` in a DM to Botdi.", delete_after=10)
            return
        removed = await site_store.remove_user_gemini_key(ctx.author.id)
        if removed:
            await ctx.send(embed=discord.Embed(description="\u2705 Your Gemini API key has been removed. You're back on the free Botdi limit.", color=COLOR_OK))
        else:
            await ctx.send(embed=discord.Embed(description="\u2139\uFE0F You didn't have a stored API key.", color=COLOR_INFO))

    @commands.hybrid_command(name="myprojects", description="List your App Engineering projects")
    async def myprojects_cmd(self, ctx: commands.Context) -> None:
        projects = await site_store.get_user_projects(ctx.author.id)
        if not projects:
            await ctx.send(embed=discord.Embed(description="You don't have any projects yet. Use `/site` to build one!", color=COLOR_INFO))
            return
        lines = []
        for p in projects[:10]:
            status_emoji = "\u2705" if p["build_status"] == "success" else "\u274C"
            created = p["created_at"][:10]
            lines.append(f"{status_emoji} `{p['id']}` — {p['prompt'][:60]} ({created})")
        embed = discord.Embed(title="\U0001F4C2 Your Projects", description="\n".join(lines), color=BOT_COLOR)
        embed.set_footer(text=f"{len(projects)} project(s) total")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="siteinfo", description="Show details about a specific project")
    @discord.app_commands.describe(project_id="The project ID to inspect")
    async def siteinfo_cmd(self, ctx: commands.Context, *, project_id: str) -> None:
        project = await site_store.get_project(project_id)
        if not project:
            await ctx.send(embed=discord.Embed(description=f"\u274C Project `{project_id}` not found.", color=COLOR_ERR))
            return
        if project["owner_id"] != ctx.author.id:
            await ctx.send(embed=discord.Embed(description="\u274C You can only view your own projects.", color=COLOR_ERR))
            return
        embed = discord.Embed(title=f"\U0001F4CB Project {project['id']}", description=project["prompt"][:200], color=BOT_COLOR)
        embed.add_field(name="Status", value=project["build_status"], inline=True)
        embed.add_field(name="Files", value=str(len(project["files"])), inline=True)
        embed.add_field(name="Edits", value=str(len(project["edit_log"])), inline=True)
        embed.add_field(name="Created", value=project["created_at"][:19].replace("T", " "), inline=True)
        embed.add_field(name="Modified", value=project["last_modified"][:19].replace("T", " "), inline=True)
        if project.get("preview_url"):
            embed.add_field(name="Preview", value=project["preview_url"], inline=False)
        embed.add_field(name="File List", value=", ".join(project["files"].keys()) or "None", inline=False)
        await ctx.send(embed=embed)

    @site_cmd.error
    async def site_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=discord.Embed(description="\u274C Please provide a description.\n\n**Example:** `/site Create a modern portfolio website`", color=COLOR_ERR))
        else:
            await ctx.send(embed=discord.Embed(description=f"\u274C Error: {error}", color=COLOR_ERR))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SiteCog(bot))
