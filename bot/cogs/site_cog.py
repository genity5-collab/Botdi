"""
Site Cog — /site slash command for App Engineering.
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
    def __init__(self, project_id: str, files: dict[str, str], preview_url: str) -> None:
        super().__init__(timeout=300)
        self.project_id = project_id
        self.files = files
        self.preview_url = preview_url

    @discord.ui.button(label="Open Preview", style=discord.ButtonStyle.link, emoji="🌐")
    async def open_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.url = self.preview_url

    @discord.ui.button(label="Download Project", style=discord.ButtonStyle.secondary, emoji="📦")
    async def download_project(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        project = await site_store.get_project(self.project_id)
        if not project or project["owner_id"] != interaction.user.id:
            await interaction.response.send_message("You can only download your own projects.", ephemeral=True)
            return
        await interaction.response.send_message(file=discord.File(site_engine._create_zip(self.files), filename="botdi-site.zip"), ephemeral=True)

    @discord.ui.button(label="Edit Log", style=discord.ButtonStyle.secondary, emoji="📝")
    async def show_edit_log(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        project = await site_store.get_project(self.project_id)
        if not project or project["owner_id"] != interaction.user.id:
            await interaction.response.send_message("You can only view your own project logs.", ephemeral=True)
            return
        entries = project.get("edit_log", [])
        lines = [f"`{entry['timestamp'][:16].replace('T', ' ')}` — {entry['description']}" for entry in entries[-15:]]
        await interaction.response.send_message(embed=discord.Embed(title="📝 Edit Log", description="\n".join(lines) or "No edits yet.", color=COLOR_INFO), ephemeral=True)


class SiteCog(commands.Cog, name="AppEngineering"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="site", description="Build a website from a description — AI generates, builds, and previews it")
    @discord.app_commands.describe(description="What do you want to build?", project_id="Edit an existing project by ID (optional)")
    async def site_cmd(self, ctx: commands.Context, *, description: str, project_id: str | None = None) -> None:
        await ctx.defer()
        user = ctx.author
        is_owner = await _is_bot_owner(self.bot, user.id)
        user_gemini_key = await site_store.get_user_gemini_key(user.id)
        if not is_owner:
            allowed, _ = await site_store.check_site_usage(user.id)
            if not allowed:
                await ctx.send(embed=discord.Embed(title="⚠️ App creation paused", description=f"Your **{SITE_FREE_MONTHLY_LIMIT}** monthly app credits are used.\n\nNew apps and edits are paused until the monthly credits restock.", color=COLOR_WARN))
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
            if latest and latest["build_status"] == "success" and any(k in description.lower() for k in ("make it", "add", "fix", "change", "update", "remove", "dark mode", "mobile friendly", "blue", "navbar", "login", "animation", "button")):
                target_project_id = latest["id"]
                is_edit = True
        if not is_owner:
            consumed, remaining = await site_store.consume_site_message(user.id)
            if not consumed:
                await ctx.send(embed=discord.Embed(title="⚠️ App creation paused", description="Your monthly app credits were used by another request. New apps and edits are paused until the monthly restock.", color=COLOR_WARN))
                return
        else:
            remaining = None
        progress = discord.Embed(title="✏️ Editing Project" if is_edit else "🏗️ App Engineering", description="🧠 Planning...\n⚙️ Generating files...\n🧪 Building...\n🔧 Testing...\n📸 Capturing preview...", color=COLOR_INFO)
        progress.set_footer(text=f"Requested by {user.display_name}")
        progress_msg = await ctx.send(embed=progress)
        try:
            result = await site_engine.edit_project(target_project_id, description, user_gemini_key) if is_edit and target_project_id else await site_engine.generate_project(description, user.id, user_gemini_key)
        except Exception as exc:
            log.error("[site] Pipeline error: %s", exc, exc_info=True)
            await progress_msg.edit(embed=discord.Embed(title="❌ I couldn't generate the site.", description="An unexpected error occurred. The project has been preserved.", color=COLOR_ERR))
            return
        if result is None:
            await progress_msg.edit(embed=discord.Embed(title="❌ I couldn't generate the site.", description="All AI providers are currently unavailable. Please try again in a moment.", color=COLOR_ERR))
            return
        if result["build_status"] == "failed":
            await progress_msg.edit(embed=discord.Embed(title="❌ Build failed", description=f"The project was preserved.\n\n**Error:** `{result.get('error', 'Unknown error')[:300]}`", color=COLOR_ERR))
            return
        files = result["files"]
        pid = result["project_id"]
        project = await site_store.get_project(pid)
        entries = project.get("edit_log", []) if project else []
        info = result.get("info", {})
        embed = discord.Embed(title="✅ Site ready!", description=f"**Project ID:** `{pid}`\n" + (f"**Summary:** {result.get('summary')}" if result.get('summary') else ""), color=COLOR_OK, timestamp=discord.utils.utcnow())
        if result.get("screenshot"):
            embed.set_image(url="attachment://preview.png")
        embed.add_field(name="📝 Edit Log", value="```\n" + "\n".join(f"{e['timestamp'][11:16]} — {e['description']}" for e in entries[-6:])[:1000] + "\n```", inline=False)
        embed.add_field(name="⚠️ Project Info", value="\n".join((f"**Contains:** {info.get('contains', 'HTML/CSS/JS')}", f"**Dependencies:** {info.get('dependencies', 0)}", f"**External requests:** {info.get('external_requests') or 'None'}", f"**Permissions:** {info.get('permissions', 'Sandbox only')}")), inline=False)
        footer = "Botdi App Engineering"
        if remaining is not None:
            footer += f" • {remaining}/{SITE_FREE_MONTHLY_LIMIT} credits left this month"
        embed.set_footer(text=footer)
        view = SiteView(pid, files, result["preview_url"])
        for child in view.children:
            if isinstance(child, discord.ui.Button) and child.label == "Open Preview":
                child.url = result["preview_url"]
        if result.get("screenshot"):
            await progress_msg.edit(embed=embed, view=view, attachments=[discord.File(io.BytesIO(result["screenshot"]), filename="preview.png")])
        else:
            await progress_msg.edit(embed=embed, view=view)
        await log_action(self.bot, "🏗️ Site Generated", f"**User:** {user.mention} (`{user.id}`)\n**Project:** `{pid}`\n**Prompt:** {description[:200]}", color=COLOR_OK)

    @commands.hybrid_command(name="setkey", description="Set your own Gemini API key for /site")
    @discord.app_commands.describe(api_key="Your Gemini API key")
    async def setkey_cmd(self, ctx: commands.Context, *, api_key: str) -> None:
        if ctx.guild is not None:
            await ctx.send(embed=discord.Embed(description="❌ Please use `/setkey` in a DM to Botdi.", color=COLOR_ERR), delete_after=10)
            return
        if not api_key.startswith("AIza"):
            await ctx.send(embed=discord.Embed(description="❌ That doesn't look like a valid Gemini API key.", color=COLOR_ERR))
            return
        await site_store.set_user_gemini_key(ctx.author.id, api_key)
        await ctx.send(embed=discord.Embed(title="✅ API Key Saved", description="Your key is stored securely and used only for your requests.", color=COLOR_OK))

    @commands.hybrid_command(name="removekey", description="Remove your stored Gemini API key")
    async def removekey_cmd(self, ctx: commands.Context) -> None:
        if ctx.guild is not None:
            await ctx.send("❌ Please use `/removekey` in a DM to Botdi.", delete_after=10)
            return
        removed = await site_store.remove_user_gemini_key(ctx.author.id)
        await ctx.send(embed=discord.Embed(description="✅ Your Gemini API key was removed." if removed else "ℹ️ You didn't have a stored API key.", color=COLOR_OK if removed else COLOR_INFO))

    @commands.hybrid_command(name="myprojects", description="List your App Engineering projects")
    async def myprojects_cmd(self, ctx: commands.Context) -> None:
        projects = await site_store.get_user_projects(ctx.author.id)
        if not projects:
            await ctx.send(embed=discord.Embed(description="You don't have any projects yet. Use `/site` to build one!", color=COLOR_INFO))
            return
        lines = [f"{'✅' if p['build_status'] == 'success' else '❌'} `{p['id']}` — {p['prompt'][:60]} ({p['created_at'][:10]})" for p in projects[:10]]
        await ctx.send(embed=discord.Embed(title="📂 Your Projects", description="\n".join(lines), color=BOT_COLOR).set_footer(text=f"{len(projects)} project(s) total"))

    @commands.hybrid_command(name="siteinfo", description="Show details about a specific project")
    @discord.app_commands.describe(project_id="The project ID to inspect")
    async def siteinfo_cmd(self, ctx: commands.Context, *, project_id: str) -> None:
        project = await site_store.get_project(project_id)
        if not project or project["owner_id"] != ctx.author.id:
            await ctx.send(embed=discord.Embed(description="❌ Project not found or not yours.", color=COLOR_ERR))
            return
        embed = discord.Embed(title=f"📋 Project {project['id']}", description=project["prompt"][:200], color=BOT_COLOR)
        embed.add_field(name="Status", value=project["build_status"], inline=True)
        embed.add_field(name="Files", value=str(len(project["files"])), inline=True)
        embed.add_field(name="Edits", value=str(len(project["edit_log"])), inline=True)
        await ctx.send(embed=embed)

    @site_cmd.error
    async def site_error(self, ctx: commands.Context, error: Exception) -> None:
        await ctx.send(embed=discord.Embed(description=f"❌ Error: {error}", color=COLOR_ERR))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SiteCog(bot))
