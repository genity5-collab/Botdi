"""
Site Cog — /site slash command for App Engineering.

Pipeline: Think → Tools → Generate → Build → Debug → Screenshot → Preview → Deliver
Shows AI thinking, tools being run, dependencies, and live edit log to the user.

Commands:
  /site <description>       — Build or edit a website
  /myprojects               — List your projects
  /sitecredits              — Check remaining credits
  /deleteproject <id>       — Delete a project
  /setkey <api_key>          — Set your own Gemini key (DM only)
  /removekey                — Remove stored key (DM only)
"""
from __future__ import annotations
import io
import logging
import discord
from discord.ext import commands
from config import COLOR_ERR, COLOR_WARN, COLOR_OK, COLOR_INFO, SITE_FREE_MONTHLY_LIMIT
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
    def __init__(self, project_id: str, files: dict[str, str], preview_url: str | None, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.project_id = project_id
        self.files = files
        self.preview_url = preview_url
        self.owner_id = owner_id

    @discord.ui.button(label="Open in Studio", style=discord.ButtonStyle.link, emoji="🏗️")
    async def open_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.url = self.preview_url

    @discord.ui.button(label="Download", style=discord.ButtonStyle.secondary, emoji="📦")
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
        if not entries:
            await interaction.response.send_message("No edits yet.", ephemeral=True)
            return
        lines = []
        for entry in entries[-20:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            status_emoji = {"success": "✅", "failed": "❌", "debugging": "🔧", "blocked": "🚫", "planning": "🧠", "pending": "⏳"}.get(entry.get("build_status", ""), "•")
            debug_info = f" [{entry['debug_status']}]" if entry.get("debug_status") else ""
            files_info = f" ({', '.join(entry.get('files', [])[:3])})" if entry.get("files") else ""
            lines.append(f"`{ts}` {status_emoji} {entry['description']}{debug_info}{files_info}")
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[-1900:]
        await interaction.response.send_message(
            content=f"📝 **Edit Log**\n```\n{text}\n```",
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

        if not is_owner:
            await ctx.send(embed=discord.Embed(
                title="🔒 Owner Only",
                description="App Engineering is restricted to the bot owner. Use `/help` to see what you can do.",
                color=COLOR_ERR,
            ), delete_after=15)
            return

        is_edit = False
        target_project_id = project_id
        if project_id:
            project = await site_store.get_project(project_id)
            if not project:
                await ctx.send(embed=discord.Embed(description=f"❌ Project `{project_id}` not found.", color=COLOR_ERR))
                return
            if project["owner_id"] != user.id:
                await ctx.send(embed=discord.Embed(description="❌ You can only edit your own projects.", color=COLOR_ERR))
                return
            is_edit = True
        else:
            latest = await site_store.get_user_latest_project(user.id)
            if latest and latest["build_status"] == "success" and any(
                k in description.lower() for k in (
                    "make it", "add", "fix", "change", "update", "remove",
                    "dark mode", "mobile friendly", "blue", "navbar",
                    "login", "animation", "button", "color", "font", "theme",
                )
            ):
                target_project_id = latest["id"]
                is_edit = True

        # ── Detect project size for display ────────────────────────────────
        project_credits = site_store.detect_project_size(description)
        is_big = project_credits > 1
        remaining = None  # Owner = unlimited

        # ── Live progress message ───────────────────────────────────────────────
        progress_embed = discord.Embed(
            title="✏️ Editing Project" if is_edit else "🏗️ App Engineering",
            description="🧠 Starting...",
            color=COLOR_INFO,
        )
        progress_embed.set_footer(text=f"Requested by {user.display_name}")
        progress_msg = await ctx.send(embed=progress_embed)

        # ── Live progress callback ─────────────────────────────────────────────
        current_thoughts: list[str] = []
        current_tools: list[str] = []
        edit_log_lines: list[str] = []

        async def on_progress(key: str, msg: str) -> None:
            try:
                # Track entries
                if key == "thinking":
                    clean = msg.replace("🧠 ", "")
                    if clean not in current_thoughts:
                        current_thoughts.append(clean)
                elif key == "tools":
                    clean = msg.replace("🔧 ", "").replace("Running tools", "")
                    if clean.strip() and clean.strip() not in current_tools:
                        current_tools.append(clean.strip())
                else:
                    edit_log_lines.append(msg)
                    clean_log = msg.lstrip("✅❌🚫🔧📸⚙️🧪📋📦✏️🏗️ ")
                    if clean_log:
                        edit_log_lines.append(f"  → {clean_log}")

                # Build the live description
                sections = []

                # Current step
                sections.append(msg)

                # Thoughts section (show last 4)
                if current_thoughts:
                    visible = current_thoughts[-4:]
                    thoughts_text = "\n".join(f"💭 {t}" for t in visible)
                    sections.append(f"\n**🧠 AI Thinking:**\n{thoughts_text}")

                # Tools section
                if current_tools:
                    tools_text = "\n".join(f"🔧 {t}" for t in current_tools[-4:])
                    sections.append(f"\n**🔧 Running Tools:**\n{tools_text}")

                # Edit log section (last 6)
                if edit_log_lines:
                    log_text = "\n".join(edit_log_lines[-6:])
                    sections.append(f"\n**📝 Live Edit Log:**\n```\n{log_text[:800]}\n```")

                desc = "\n".join(sections)[:4000]

                color = COLOR_INFO
                if key in ("failed", "blocked"):
                    color = COLOR_ERR
                elif key in ("success", "done", "screenshot"):
                    color = COLOR_OK

                updated = discord.Embed(
                    title="✏️ Editing Project" if is_edit else "🏗️ App Engineering",
                    description=desc,
                    color=color,
                )
                updated.set_footer(text=f"Requested by {user.display_name} • {len(current_thoughts)} thoughts • {len(current_tools)} tools")
                await progress_msg.edit(embed=updated)
            except Exception as exc:
                log.debug("[site] Progress update failed: %s", exc)

        # ── Run pipeline ──────────────────────────────────────────────────────
        try:
            if is_edit and target_project_id:
                result = await site_engine.edit_project(target_project_id, description, user_gemini_key, on_progress)
            else:
                result = await site_engine.generate_project(description, user.id, user_gemini_key, on_progress)
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

        if result.get("build_status") == "blocked":
            await progress_msg.edit(embed=discord.Embed(
                title="🚫 Request blocked",
                description=f"This request was blocked by the safety filter.\n\n**Reason:** `{result.get('error', 'Violates safety policy')}`\n\nVyrion does not generate phishing pages, scams, malware, or other malicious content.",
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

        # ── Success! Build final response ──────────────────────────────────────
        files = result["files"]
        pid = result["project_id"]
        project = await site_store.get_project(pid) if pid else None
        entries = project.get("edit_log", []) if project else []
        info = result.get("info", {})
        thinking = result.get("thinking", [])
        tools_used = result.get("tools", [])

        log_text = "\n".join(f"{e['timestamp'][11:16]} — {e['description']}" for e in entries[-8:])[:1000]

        embed = discord.Embed(
            title="✅ Site ready!",
            description=(
                f"**Project ID:** `{pid}`\n"
                + (f"**Summary:** {result.get('summary')}\n" if result.get("summary") else "")
                + f"**Studio:** {result.get('preview_url', 'N/A')}"
            ),
            color=COLOR_OK,
            timestamp=discord.utils.utcnow(),
        )
        if result.get("screenshot"):
            embed.set_image(url="attachment://preview.png")

        # Show AI thinking
        if thinking:
            thinking_text = "\n".join(f"💭 {t}" for t in thinking[:6])
            embed.add_field(name="🧠 AI Thought Process", value=thinking_text[:1000], inline=False)

        # Show tools used
        if tools_used:
            tools_text = "\n".join(f"🔧 {t}" for t in tools_used[:5])
            embed.add_field(name="🔧 Tools Used", value=tools_text[:1000], inline=False)

        # Show dependencies
        cdn_libs = info.get("cdn_libraries", [])
        if cdn_libs:
            embed.add_field(name="📦 Dependencies", value="\n".join(f"• {d}" for d in cdn_libs[:8]), inline=False)

        embed.add_field(name="📝 Edit Log", value=f"```\n{log_text}\n```", inline=False)

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

        footer = f"Vyrion App Engineering • {'Big project' if is_big else 'Standard'} ({project_credits} credits)"
        embed.set_footer(text=footer)

        view = SiteView(pid, files, result.get("preview_url"), user.id)
        for child in view.children:
            if isinstance(child, discord.ui.Button) and child.label == "Open in Studio":
                child.url = result.get("preview_url") or "https://vyrion.app"

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

    @commands.hybrid_command(name="myprojects", description="List your App Engineering projects with status and credits")
    async def myprojects_cmd(self, ctx: commands.Context) -> None:
        projects = await site_store.get_user_projects(ctx.author.id)
        if not projects:
            await ctx.send(embed=discord.Embed(
                description="You don't have any projects yet. Use `/site` to build one!",
                color=COLOR_INFO,
            ))
            return
        lines = []
        for p in projects[-10:]:
            status_emoji = {
                "success": "✅", "failed": "❌", "blocked": "🚫",
                "pending": "⏳", "planning": "🧠",
            }.get(p["build_status"], "•")
            lines.append(f"{status_emoji} `{p['id']}` — {p.get('prompt', 'Untitled')[:60]}")
        embed = discord.Embed(
            title="📋 Your Projects",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        embed.set_footer(text="Vyrion App Engineering — Owner only")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="deleteproject", description="Delete one of your projects by ID")
    @discord.app_commands.describe(project_id="The project ID to delete")
    async def deleteproject_cmd(self, ctx: commands.Context, project_id: str) -> None:
        project = await site_store.get_project(project_id)
        if not project:
            await ctx.send(embed=discord.Embed(description=f"❌ Project `{project_id}` not found.", color=COLOR_ERR))
            return
        if project["owner_id"] != ctx.author.id:
            await ctx.send(embed=discord.Embed(description="❌ You can only delete your own projects.", color=COLOR_ERR))
            return
        await site_store.delete_project(project_id)
        await ctx.send(embed=discord.Embed(description=f"🗑️ Project `{project_id}` deleted.", color=COLOR_OK))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SiteCog(bot))
