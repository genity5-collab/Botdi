"""
Site Cog — /site slash command for App Engineering.

Features:
  - /site <description> — Generate a new website from natural language
  - /site <description> — Edit existing project (auto-detected or via project_id)
  - /setkey — Set your own Gemini API key
  - /removekey — Remove stored key
  - /myprojects — List your projects
  - /deleteproject — Delete a project by ID

Security:
  - Scam/phishing/malware prompts are blocked
  - 5 monthly free credits for non-owners; owner has unlimited
  - When credits hit 0, all user's sites go offline until reset
  - Bot owner always has infinite credits
  - API keys never exposed in Discord, files, or screenshots
"""
from __future__ import annotations
import io
import logging
import discord
from discord.ext import commands
from config import BOT_COLOR, COLOR_ERR, COLOR_WARN, COLOR_OK, COLOR_INFO, SITE_FREE_MONTHLY_LIMIT
import site_store
import site_engine
from utils import log_action

log = logging.getLogger(__name__)


async def _is_bot_owner(bot: commands.Bot, user_id: int) -> bool:
    try:
        info = await bot.application_info()
        return info.owner.id == user_id
    except discord.HTTPException:
        return False


class SiteView(discord.ui.View):
    def __init__(self, project_id: str, files: dict[str, str], preview_url: str | None, owner_id: int, is_owner: bool) -> None:
        super().__init__(timeout=300)
        self.project_id = project_id
        self.files = files
        self.preview_url = preview_url
        self.owner_id = owner_id
        self.is_owner = is_owner

    @discord.ui.button(label="Open Preview", style=discord.ButtonStyle.link, emoji="🌐")
    async def open_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.url = self.preview_url

    @discord.ui.button(label="Download Project", style=discord.ButtonStyle.secondary, emoji="📦")
    async def download_project(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        project = await site_store.get_project(self.project_id)
        if not project or project["owner_id"] != interaction.user.id:
            await interaction.response.send_message("You can only download your own projects.", ephemeral=True)
            return
        await interaction.response.send_message(
            file=discord.File(site_engine._create_zip(self.files), filename="botdi-site.zip"),
            ephemeral=True,
        )

    @discord.ui.button(label="Edit Log", style=discord.ButtonStyle.secondary, emoji="📝")
    async def show_edit_log(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        project = await site_store.get_project(self.project_id)
        if not project or project["owner_id"] != interaction.user.id:
            await interaction.response.send_message("You can only view your own project logs.", ephemeral=True)
            return
        entries = project.get("edit_log", [])
        lines = [f"`{entry['timestamp'][:16].replace('T', ' ')}` — {entry['description']}" for entry in entries[-15:]]
        await interaction.response.send_message(
            embed=discord.Embed(title="📝 Edit Log", description="\n".join(lines) or "No edits yet.", color=COLOR_INFO),
            ephemeral=True,
        )


class SiteCog(commands.Cog, name="AppEngineering"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="site", description="Build a website from a description — AI generates, builds, and previews it")
    @discord.app_commands.describe(
        description="What do you want to build?",
        project_id="Edit an existing project by ID (optional)",
    )
    async def site_cmd(self, ctx: commands.Context, *, description: str, project_id: str | None = None) -> None:
        await ctx.defer()
        user = ctx.author
        is_owner = await _is_bot_owner(self.bot, user.id)
        user_gemini_key = await site_store.get_user_gemini_key(user.id)

        # ── Check usage credits ────────────────────────────────────────────────
        if not is_owner:
            allowed, remaining_check = await site_store.check_site_usage(user.id)
            if not allowed:
                await ctx.send(embed=discord.Embed(
                    title="⚠️ App creation paused",
                    description=(
                        f"Your **{SITE_FREE_MONTHLY_LIMIT}** monthly app credits are used up.\n\n"
                        f"🚫 All your sites are now **offline** until credits reset next month.\n\n"
                        f"Add your own API key with `/setkey` to continue building."
                    ),
                    color=COLOR_WARN,
                ))
                return

        # ── Determine if this is a new project or an edit ─────────────────────
        is_edit = False
        target_project_id = project_id
        if project_id:
            project = await site_store.get_project(project_id)
            if not project:
                await ctx.send(embed=discord.Embed(
                    description=f"❌ Project `{project_id}` not found.",
                    color=COLOR_ERR,
                ))
                return
            if project["owner_id"] != user.id:
                await ctx.send(embed=discord.Embed(
                    description="❌ You can only edit your own projects.",
                    color=COLOR_ERR,
                ))
                return
            is_edit = True
        else:
            latest = await site_store.get_user_latest_project(user.id)
            if latest and latest["build_status"] == "success" and any(
                k in description.lower() for k in (
                    "make it", "add", "fix", "change", "update", "remove",
                    "dark mode", "mobile friendly", "blue", "navbar",
                    "login", "animation", "button", "color", "font",
                )
            ):
                target_project_id = latest["id"]
                is_edit = True

        # ── Consume a credit (non-owners only) ────────────────────────────────
        if not is_owner:
            consumed, remaining = await site_store.consume_site_message(user.id)
            if not consumed:
                await ctx.send(embed=discord.Embed(
                    title="⚠️ App creation paused",
                    description=(
                        "Your monthly app credits were used by another request.\n"
                        "🚫 All your sites are now **offline** until credits reset.\n\n"
                        "Add your own API key with `/setkey` to continue."
                    ),
                    color=COLOR_WARN,
                ))
                return
        else:
            remaining = None

        # ── Progress message ──────────────────────────────────────────────────
        progress = discord.Embed(
            title="✏️ Editing Project" if is_edit else "🏗️ App Engineering",
            description="🧠 Planning...\n⚙️ Generating files...\n🧪 Building...\n🔧 Testing...\n📸 Capturing preview...",
            color=COLOR_INFO,
        )
        progress.set_footer(text=f"Requested by {user.display_name}")
        progress_msg = await ctx.send(embed=progress)

        # ── Run the pipeline ──────────────────────────────────────────────────
        try:
            if is_edit and target_project_id:
                result = await site_engine.edit_project(target_project_id, description, user_gemini_key)
            else:
                result = await site_engine.generate_project(description, user.id, user_gemini_key)
        except Exception as exc:
            log.error("[site] Pipeline error: %s", exc, exc_info=True)
            await progress_msg.edit(embed=discord.Embed(
                title="❌ I couldn't generate the site.",
                description="An unexpected error occurred. The project has been preserved.",
                color=COLOR_ERR,
            ))
            return

        if result is None:
            await progress_msg.edit(embed=discord.Embed(
                title="❌ I couldn't generate the site.",
                description="All AI providers are currently unavailable. Please try again in a moment.",
                color=COLOR_ERR,
            ))
            return

        # ── Handle blocked (scam/phishing/malware) ─────────────────────────────
        if result.get("build_status") == "blocked":
            await progress_msg.edit(embed=discord.Embed(
                title="🚫 Request blocked",
                description=f"This request was blocked by the safety filter.\n\n**Reason:** `{result.get('error', 'Violates safety policy')}`\n\nBotdi does not generate phishing pages, scams, malware, or other malicious content.",
                color=COLOR_ERR,
            ))
            return

        if result["build_status"] == "failed":
            await progress_msg.edit(embed=discord.Embed(
                title="❌ Build failed",
                description=f"The project was preserved.\n\n**Error:** `{result.get('error', 'Unknown error')[:300]}`",
                color=COLOR_ERR,
            ))
            return

        # ── Success! Build the response ───────────────────────────────────────
        files = result["files"]
        pid = result["project_id"]
        project = await site_store.get_project(pid) if pid else None
        entries = project.get("edit_log", []) if project else []
        info = result.get("info", {})

        embed = discord.Embed(
            title="✅ Site ready!",
            description=f"**Project ID:** `{pid}`\n" + (f"**Summary:** {result.get('summary')}" if result.get("summary") else ""),
            color=COLOR_OK,
            timestamp=discord.utils.utcnow(),
        )
        if result.get("screenshot"):
            embed.set_image(url="attachment://preview.png")

        # Edit log (last 6 entries)
        log_text = "\n".join(f"{e['timestamp'][11:16]} — {e['description']}" for e in entries[-6:])[:1000]
        embed.add_field(name="📝 Edit Log", value=f"```\n{log_text}\n```", inline=False)

        # Project info
        embed.add_field(
            name="⚠️ Project Info",
            value="\n".join((
                f"**Contains:** {info.get('contains', 'HTML/CSS/JS')}",
                f"**Dependencies:** {info.get('dependencies', 0)}",
                f"**External requests:** {info.get('external_requests') or 'None'}",
                f"**Permissions:** {info.get('permissions', 'Sandbox only')}",
            )),
            inline=False,
        )

        footer = "Botdi App Engineering"
        if remaining is not None:
            footer += f" • {remaining}/{SITE_FREE_MONTHLY_LIMIT} credits left this month"
            if remaining == 0:
                footer += " • ⚠️ Sites offline until reset"
        embed.set_footer(text=footer)

        # View with buttons
        view = SiteView(pid, files, result.get("preview_url"), user.id, is_owner)
        for child in view.children:
            if isinstance(child, discord.ui.Button) and child.label == "Open Preview":
                child.url = result.get("preview_url") or "https://botdi.app"

        if result.get("screenshot"):
            await progress_msg.edit(
                embed=embed,
                view=view,
                attachments=[discord.File(io.BytesIO(result["screenshot"]), filename="preview.png")],
            )
        else:
            await progress_msg.edit(embed=embed, view=view)

        await log_action(
            self.bot, "🏗️ Site Generated",
            f"**User:** {user.mention} (`{user.id}`)\n**Project:** `{pid}`\n**Prompt:** {description[:200]}",
            color=COLOR_OK,
        )

    @commands.hybrid_command(name="setkey", description="Set your own Gemini API key for /site")
    @discord.app_commands.describe(api_key="Your Gemini API key")
    async def setkey_cmd(self, ctx: commands.Context, *, api_key: str) -> None:
        if ctx.guild is not None:
            await ctx.send(embed=discord.Embed(
                description="❌ Please use `/setkey` in a DM to Botdi.",
                color=COLOR_ERR,
            ), delete_after=10)
            return
        if not api_key.startswith("AIza"):
            await ctx.send(embed=discord.Embed(
                description="❌ That doesn't look like a valid Gemini API key.",
                color=COLOR_ERR,
            ))
            return
        await site_store.set_user_gemini_key(ctx.author.id, api_key)
        await ctx.send(embed=discord.Embed(
            title="✅ API Key Saved",
            description="Your key is stored securely and used only for your /site requests.",
            color=COLOR_OK,
        ))

    @commands.hybrid_command(name="removekey", description="Remove your stored Gemini API key")
    async def removekey_cmd(self, ctx: commands.Context) -> None:
        if ctx.guild is not None:
            await ctx.send("❌ Please use `/removekey` in a DM to Botdi.", delete_after=10)
            return
        removed = await site_store.remove_user_gemini_key(ctx.author.id)
        await ctx.send(embed=discord.Embed(
            description="✅ Your Gemini API key was removed." if removed else "ℹ️ You didn't have a stored API key.",
            color=COLOR_OK if removed else COLOR_INFO,
        ))

    @commands.hybrid_command(name="myprojects", description="List your App Engineering projects")
    async def myprojects_cmd(self, ctx: commands.Context) -> None:
        projects = await site_store.get_user_projects(ctx.author.id)
        if not projects:
            await ctx.send(embed=discord.Embed(
                description="You don't have any projects yet. Use `/site` to build one!",
                color=COLOR_INFO,
            ))
            return
        is_owner = await _is_bot_owner(self.bot, ctx.author.id)
        _, remaining = await site_store.check_site_usage(ctx.author.id) if not is_owner else (True, "∞")
        lines = []
        for p in projects[-10:]:
            status_emoji = "✅" if p["build_status"] == "success" else "❌" if p["build_status"] == "failed" else "🚫" if p["build_status"] == "blocked" else "⏳"
            lines.append(f"{status_emoji} `{p['id']}` — {p.get('prompt', 'Untitled')[:50]}")
        embed = discord.Embed(
            title="📋 Your Projects",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"{'∞' if is_owner else f'{remaining}/{SITE_FREE_MONTHLY_LIMIT}'} credits {'(owner)' if is_owner else 'this month'}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="deleteproject", description="Delete one of your projects by ID")
    @discord.app_commands.describe(project_id="The project ID to delete")
    async def deleteproject_cmd(self, ctx: commands.Context, project_id: str) -> None:
        project = await site_store.get_project(project_id)
        if not project:
            await ctx.send(embed=discord.Embed(
                description=f"❌ Project `{project_id}` not found.",
                color=COLOR_ERR,
            ))
            return
        if project["owner_id"] != ctx.author.id:
            await ctx.send(embed=discord.Embed(
                description="❌ You can only delete your own projects.",
                color=COLOR_ERR,
            ))
            return
        await site_store.delete_project(project_id)
        await ctx.send(embed=discord.Embed(
            description=f"🗑️ Project `{project_id}` deleted.",
            color=COLOR_OK,
        ))

    @commands.hybrid_command(name="sitecredits", description="Check your remaining App Engineering credits")
    async def sitecredits_cmd(self, ctx: commands.Context) -> None:
        is_owner = await _is_bot_owner(self.bot, ctx.author.id)
        if is_owner:
            await ctx.send(embed=discord.Embed(
                title="🆓 App Engineering Credits",
                description="You are the bot owner — **unlimited** credits.",
                color=COLOR_OK,
            ))
            return
        _, remaining = await site_store.check_site_usage(ctx.author.id)
        has_key = await site_store.get_user_gemini_key(ctx.author.id) is not None
        desc = f"**{remaining}/{SITE_FREE_MONTHLY_LIMIT}** credits remaining this month.\n"
        if remaining == 0:
            desc += "\n🚫 All your sites are **offline** until credits reset next month."
        if has_key:
            desc += "\n\n✅ You have a Gemini API key — unlimited generations (subject to Google's limits)."
        await ctx.send(embed=discord.Embed(
            title="🆓 App Engineering Credits",
            description=desc,
            color=COLOR_OK if remaining > 0 else COLOR_WARN,
        ))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SiteCog(bot))
