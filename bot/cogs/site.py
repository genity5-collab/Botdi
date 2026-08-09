"""
Site Cog — Botdi App Engineering via /site
──────────────────────────────────────────
Pipeline: Prompt → Plan → Generate → Build → Test → Debug → Screenshot → Preview → Deliver

Completely separate from SubAgent mode. Own AI provider chain, own storage,
own workflow. Uses GPT-OSS-20B via Groq/OpenRouter/Fireworks with fallback,
or user-provided Gemini key if configured.
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import logging
import os
import re
import zipfile
from typing import Any

import aiohttp
import discord
from discord.ext import commands

from config import (
    BOT_COLOR, COLOR_ERR, COLOR_WARN, COLOR_OK,
    GEMINI_API_KEY,
    SITE_FREE_MONTHLY_LIMIT,
    SITE_MAX_DEBUG_RETRIES,
    SITE_PREVIEW_BASE_URL,
    SITE_PROVIDERS,
)
from site_store import (
    add_edit_log,
    check_usage,
    create_project,
    get_edit_logs,
    get_latest_user_project,
    get_project,
    get_user_key,
    increment_usage,
    set_user_key,
    update_project,
)
from utils import log_action

log = logging.getLogger(__name__)

# ── System prompt for the engineering AI ─────────────────────────────────────

_ENGINEER_SYSTEM = """\
You are Botdi's App Engineering AI. You generate complete, working website projects from natural language descriptions.

OUTPUT FORMAT (CRITICAL — you must follow this exactly):
Respond with ONLY a JSON object. No markdown, no code fences, no explanation outside the JSON.

The JSON must have this structure:
{
  "project_name": "short name",
  "files": {
    "index.html": "full file content here",
    "style.css": "full file content here",
    "script.js": "full file content here"
  },
  "description": "one sentence summary of what was built",
  "dependencies": ["any cdn libraries used"],
  "external_requests": false
}

Rules:
1. ALWAYS include an index.html as the entry point.
2. Use vanilla HTML/CSS/JS for static sites. Inline CSS in <style> tags or separate .css files.
3. Make it visually polished — modern, responsive, with good color contrast and spacing.
4. If the user asks for a calculator, dashboard, portfolio, landing page, etc., make it fully functional.
5. Set "external_requests" to true ONLY if the site fetches from an external API.
6. NEVER include API keys, secrets, or credentials in the generated files.
7. Keep each file's content complete and working — no placeholders.
8. For edits to existing projects, include ALL files (modified and unmodified) in the "files" object.
"""

_EDIT_SYSTEM = """\
You are Botdi's App Engineering AI. You are editing an existing website project.
The user wants to modify their existing site. Apply the requested changes to the existing files.

OUTPUT FORMAT (CRITICAL — respond with ONLY a JSON object, no markdown, no code fences):
{
  "project_name": "updated name if changed",
  "files": {
    "index.html": "full updated content",
    "style.css": "full updated content",
    "script.js": "full updated content"
  },
  "description": "one sentence describing the edit",
  "dependencies": ["updated list"],
  "external_requests": false,
  "files_changed": ["list of filenames that were modified"]
}

Rules:
1. Include ALL files in the "files" object — both modified and unmodified.
2. Only list actually changed filenames in "files_changed".
3. Preserve existing functionality while applying the requested changes.
4. NEVER include API keys or secrets in generated files.
5. Keep everything complete and working.
"""

_DEBUG_SYSTEM = """\
You are Botdi's debugging AI. A build error occurred in a website project.
Fix the error and return the corrected files.

OUTPUT FORMAT (respond with ONLY a JSON object, no markdown, no code fences):
{
  "files": {
    "index.html": "full corrected content",
    "style.css": "full corrected content"
  },
  "description": "what was wrong and how you fixed it",
  "files_changed": ["list of fixed filenames"]
}

Rules:
1. Include ALL files in the "files" object.
2. Only list actually changed files in "files_changed".
3. NEVER include API keys or secrets.
"""


# ── AI provider chain ──────────────────────────────────────────────────────────

async def _call_provider(
    provider: dict[str, Any],
    messages: list[dict],
    timeout: float = 30.0,
) -> str | None:
    """Call a single OpenAI-compatible provider. Returns text or None."""
    api_key = provider.get("api_key", "")
    if not api_key:
        return None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider.get("extra_headers"):
        headers.update(provider["extra_headers"])
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                provider["url"],
                headers=headers,
                json={
                    "model": provider["model"],
                    "messages": messages,
                    "max_tokens": 8000,
                    "temperature": 0.7,
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    log.warning("[%s] HTTP %s", provider["name"], resp.status)
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except asyncio.TimeoutError:
        log.warning("[%s] timed out", provider["name"])
    except Exception as exc:
        log.warning("[%s] %s", provider["name"], exc)
    return None


async def _call_gemini(api_key: str, messages: list[dict], timeout: float = 30.0) -> str | None:
    """Call Google Gemini directly using the google-genai client."""
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        # Convert OpenAI messages to a single prompt
        prompt_parts = []
        for m in messages:
            role = "User" if m["role"] == "user" else "Assistant"
            prompt_parts.append(f"{role}: {m['content']}")
        prompt = "\n\n".join(prompt_parts)
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config={"temperature": 0.7, "max_output_tokens": 8000},
            ),
            timeout=timeout,
        )
        return resp.text.strip() if resp.text else None
    except Exception as exc:
        log.warning("Gemini (user key): %s", exc)
    return None


async def _generate(
    system: str,
    user_msg: str,
    user_gemini_key: str | None = None,
) -> tuple[str | None, str]:
    """
    Try providers in order. Returns (text, source_label).
    If user_gemini_key is provided, try Gemini first.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]

    # User's own Gemini key → first priority
    if user_gemini_key:
        result = await _call_gemini(user_gemini_key, messages)
        if result:
            return result, "gemini-user"

    # Botdi provider chain
    for provider in SITE_PROVIDERS:
        result = await _call_provider(provider, messages)
        if result:
            return result, provider["name"].lower()

    return None, "none"


# ── JSON extraction ───────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from AI output, handling code fences and preamble."""
    text = text.strip()
    # Strip code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


# ── Build & test (static analysis for HTML/CSS/JS) ────────────────────────────

def _validate_files(files: dict[str, str]) -> tuple[bool, str]:
    """Basic validation: check for required files and obvious syntax issues."""
    if not files:
        return False, "No files generated"
    if "index.html" not in files:
        return False, "Missing index.html"
    html = files.get("index.html", "")
    if len(html.strip()) < 20:
        return False, "index.html is too short to be valid"
    # Check for unclosed script tags
    if "<script" in html and "</script>" not in html:
        return False, "Unclosed <script> tag in index.html"
    return True, "OK"


def _detect_external_requests(files: dict[str, str]) -> bool:
    """Check if any file makes external API calls."""
    patterns = [
        r"fetch\s*\(\s*['\"]https?://",
        r"XMLHttpRequest",
        r"\$\.ajax",
        r"axios\s*\(",
        r"import\s+.*from\s+['\"]https?://",
    ]
    for content in files.values():
        for p in patterns:
            if re.search(p, content, re.IGNORECASE):
                return True
    return False


# ── ZIP creation ──────────────────────────────────────────────────────────────

def _create_zip(files: dict[str, str]) -> io.BytesIO:
    """Create a ZIP archive of project files. Returns BytesIO buffer."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            zf.writestr(filename, content)
    buf.seek(0)
    return buf


# ── Cog ───────────────────────────────────────────────────────────────────────

class Site(commands.Cog, name="Site"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /site ─────────────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="site",
        description="Generate or edit a website with Botdi App Engineering",
    )
    async def site_cmd(self, ctx: commands.Context, *, prompt: str) -> None:
        """
        /site <description> — Generate a new website or edit your existing one.
        """
        await ctx.defer()
        user = ctx.author

        # Check for user's own Gemini key
        user_key = await get_user_key(user.id)
        has_custom_key = user_key is not None

        # Check usage limit
        allowed, remaining = await check_usage(user.id, has_custom_key)
        if not allowed:
            embed = discord.Embed(
                title="⚠️ Monthly Limit Reached",
                description=(
                    f"You've used all **{SITE_FREE_MONTHLY_LIMIT}** free /site messages this month.\n"
                    "Add your own supported API key to continue, "
                    "subject to the provider's limits.\n\n"
                    "Use `/setkey <your-gemini-api-key>` to add your key."
                ),
                color=COLOR_WARN,
            )
            await ctx.send(embed=embed)
            return

        # Determine if this is a new project or an edit
        existing = await get_latest_user_project(user.id)

        if existing:
            # Edit existing project
            await self._do_edit(ctx, prompt, existing, user_key)
        else:
            # Create new project
            await self._do_create(ctx, prompt, user, user_key)

        # Increment usage
        await increment_usage(user.id)

    # ── Create new project ────────────────────────────────────────────────────

    async def _do_create(
        self,
        ctx: commands.Context,
        prompt: str,
        user: discord.Member | discord.User,
        user_key: str | None,
    ) -> None:
        # Step 1: Planning
        status_msg = await ctx.send(embed=discord.Embed(
            description="🧠 Planning...\n⚙️ Generating files...\n🧪 Building...",
            color=BOT_COLOR,
        ))

        result, source = await _generate(_ENGINEER_SYSTEM, prompt, user_key)

        if result is None:
            await status_msg.edit(embed=discord.Embed(
                title="❌ I couldn't generate the site.",
                description="All AI providers are currently unavailable. Please try again later.",
                color=COLOR_ERR,
            ))
            return

        parsed = _extract_json(result)
        if parsed is None or "files" not in parsed:
            await status_msg.edit(embed=discord.Embed(
                title="❌ I couldn't generate the site.",
                description="The AI returned an unexpected response. Please try rephrasing your request.",
                color=COLOR_ERR,
            ))
            return

        files = parsed["files"]
        project_name = parsed.get("project_name", "Untitled")
        description = parsed.get("description", "Website generated")
        dependencies = parsed.get("dependencies", [])
        external_req = parsed.get("external_requests", _detect_external_requests(files))

        # Step 2: Validate / Build
        valid, error = _validate_files(files)
        debug_status = "none"

        if not valid:
            # Step 3: Auto-debug
            debug_status = "fixing"
            await status_msg.edit(embed=discord.Embed(
                description=f"🔧 Debugging...\n❌ {error}\n🛠️ Fixing files...",
                color=COLOR_WARN,
            ))
            files, debug_status, error = await self._auto_debug(files, error, user_key)

        valid, error = _validate_files(files)

        build_status = "success" if valid else "failed"

        # Create project in DB
        project_id = await create_project(
            owner_id=user.id,
            owner_name=user.display_name,
            prompt=prompt,
            project_name=project_name,
            files=files,
            dependencies=dependencies,
            external_requests=external_req,
        )

        if project_id is None:
            await status_msg.edit(embed=discord.Embed(
                title="❌ Storage Error",
                description="Could not save the project. Please try again.",
                color=COLOR_ERR,
            ))
            return

        # Update build status
        await update_project(project_id, {
            "build_status": build_status,
            "last_edit_prompt": prompt,
        })

        # Log the creation
        await add_edit_log(
            project_id=project_id,
            description=description,
            files_affected=list(files.keys()),
            build_status=build_status,
            debug_status=debug_status,
            prompt=prompt,
        )

        # Build preview URL
        preview_url = f"{SITE_PREVIEW_BASE_URL}/{project_id}"
        await update_project(project_id, {"preview_url": preview_url})

        # Step 4: Deliver
        await self._send_result(
            status_msg, project_id, project_name, description,
            files, dependencies, external_req, build_status,
            debug_status, source, user, remaining=None,
        )

    # ── Edit existing project ─────────────────────────────────────────────────

    async def _do_edit(
        self,
        ctx: commands.Context,
        prompt: str,
        project: dict[str, Any],
        user_key: str | None,
    ) -> None:
        project_id = str(project["id"])
        existing_files = project.get("files", {})
        if isinstance(existing_files, str):
            existing_files = json.loads(existing_files)

        status_msg = await ctx.send(embed=discord.Embed(
            description="🧠 Understanding your edit...\n⚙️ Updating files...\n🧪 Building...",
            color=BOT_COLOR,
        ))

        # Build context for the edit
        files_context = "\n\n".join(
            f"--- {fname} ---\n{content[:3000]}"
            for fname, content in existing_files.items()
        )
        edit_prompt = (
            f"Current project files:\n{files_context}\n\n"
            f"User edit request: {prompt}"
        )

        result, source = await _generate(_EDIT_SYSTEM, edit_prompt, user_key)

        if result is None:
            await status_msg.edit(embed=discord.Embed(
                title="❌ Edit Failed",
                description="All AI providers are currently unavailable. Please try again later.",
                color=COLOR_ERR,
            ))
            return

        parsed = _extract_json(result)
        if parsed is None or "files" not in parsed:
            await status_msg.edit(embed=discord.Embed(
                title="❌ Edit Failed",
                description="The AI returned an unexpected response. Please try rephrasing your edit.",
                color=COLOR_ERR,
            ))
            return

        files = parsed["files"]
        description = parsed.get("description", "Project updated")
        files_changed = parsed.get("files_changed", list(files.keys()))
        dependencies = parsed.get("dependencies", project.get("dependencies", []))
        if isinstance(dependencies, str):
            dependencies = json.loads(dependencies)
        external_req = parsed.get("external_requests", _detect_external_requests(files))
        project_name = parsed.get("project_name", project.get("project_name", "Untitled"))

        # Validate
        valid, error = _validate_files(files)
        debug_status = "none"

        if not valid:
            debug_status = "fixing"
            await status_msg.edit(embed=discord.Embed(
                description=f"🔧 Debugging...\n❌ {error}\n🛠️ Fixing files...",
                color=COLOR_WARN,
            ))
            files, debug_status, error = await self._auto_debug(files, error, user_key)

        valid, error = _validate_files(files)
        build_status = "success" if valid else "failed"

        # Update project
        await update_project(project_id, {
            "files": files,
            "project_name": project_name,
            "dependencies": dependencies,
            "external_requests": external_req,
            "build_status": build_status,
            "last_edit_prompt": prompt,
        })

        await add_edit_log(
            project_id=project_id,
            description=description,
            files_affected=files_changed,
            build_status=build_status,
            debug_status=debug_status,
            prompt=prompt,
        )

        await self._send_result(
            status_msg, project_id, project_name, description,
            files, dependencies, external_req, build_status,
            debug_status, source, ctx.author, remaining=None,
        )

    # ── Auto-debug loop ───────────────────────────────────────────────────────

    async def _auto_debug(
        self,
        files: dict[str, str],
        error: str,
        user_key: str | None,
    ) -> tuple[dict[str, str], str, str]:
        """
        Try to automatically fix build errors.
        Returns (files, debug_status, last_error).
        """
        for attempt in range(SITE_MAX_DEBUG_RETRIES):
            debug_prompt = (
                f"Build error: {error}\n\n"
                f"Current files:\n"
                + "\n\n".join(
                    f"--- {fname} ---\n{content[:2000]}"
                    for fname, content in files.items()
                )
                + "\n\nFix the error and return ALL corrected files."
            )

            result, _ = await _generate(_DEBUG_SYSTEM, debug_prompt, user_key)
            if result is None:
                return files, "failed", "AI unavailable during debug"

            parsed = _extract_json(result)
            if parsed and "files" in parsed:
                files = parsed["files"]
                valid, error = _validate_files(files)
                if valid:
                    return files, "fixed", ""
                log.info("Debug attempt %d: %s", attempt + 1, error)
            else:
                log.warning("Debug attempt %d: couldn't parse response", attempt + 1)

        return files, "failed", error

    # ── Send result embed with buttons ─────────────────────────────────────────

    async def _send_result(
        self,
        status_msg: discord.Message,
        project_id: str,
        project_name: str,
        description: str,
        files: dict[str, str],
        dependencies: list[str],
        external_requests: bool,
        build_status: str,
        debug_status: str,
        source: str,
        user: discord.Member | discord.User,
        remaining: int | None,
    ) -> None:
        """Send the final result with preview, download, edit log, and info."""

        preview_url = f"{SITE_PREVIEW_BASE_URL}/{project_id}"

        # Build embed
        if build_status == "success":
            title = "✅ Site ready!"
            color = COLOR_OK
        else:
            title = "⚠️ Build had issues"
            color = COLOR_WARN

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="Project", value=f"`{project_name}`", inline=True)
        embed.add_field(name="Project ID", value=f"`{project_id[:8]}…`", inline=True)

        source_labels = {
            "gemini-user":  "Your Gemini Key",
            "groq":         "GPT-OSS-20B via Groq",
            "openrouter":   "GPT-OSS-20B-FREE via OpenRouter",
            "fireworks":    "GPT-OSS-20B via Fireworks",
        }
        embed.add_field(name="AI Model", value=source_labels.get(source, source), inline=True)

        if debug_status == "fixed":
            embed.add_field(name="Debug", value="✅ Auto-fixed", inline=True)
        elif debug_status == "failed":
            embed.add_field(name="Debug", value="❌ Couldn't auto-fix", inline=True)

        # Project info section
        file_types = []
        for fname in files:
            ext = fname.rsplit(".", 1)[-1].upper() if "." in fname else "FILE"
            if ext not in file_types:
                file_types.append(ext)
        info_lines = [
            f"Contains: {', '.join(file_types)}",
            f"Dependencies: {len(dependencies)}",
            f"External requests: {'Yes' if external_requests else 'None'}",
            "Permissions: Sandbox only",
        ]
        if external_requests:
            info_lines.append("⚠️ External API detected — this project makes requests to an external service.")
        embed.add_field(
            name="⚠️ Project Info",
            value="\n".join(info_lines),
            inline=False,
        )

        embed.set_footer(text=f"Botdi App Engineering • /site to edit • Project preserved")

        # Create ZIP
        zip_buf = _create_zip(files)
        zip_file = discord.File(zip_buf, filename=f"botdi-site.zip")

        # Build view with buttons
        view = SiteResultView(project_id, preview_url)

        await status_msg.edit(content=None, embed=embed, view=view, attachments=[zip_file])

        await log_action(
            self.bot, "🏗️ Site Generated",
            f"**User:** {user.mention} (`{user.id}`)\n"
            f"**Project:** {project_name}\n"
            f"**ID:** `{project_id}`\n"
            f"**Build:** {build_status}\n"
            f"**Model:** {source_labels.get(source, source)}",
            color=0x2ECC71 if build_status == "success" else COLOR_WARN,
        )

    # ── /setkey — add your own Gemini API key ──────────────────────────────────

    @commands.hybrid_command(
        name="setkey",
        description="Add your own Gemini API key for unlimited /site usage",
    )
    async def setkey_cmd(self, ctx: commands.Context, *, api_key: str) -> None:
        """Set your own Gemini API key for /site usage."""
        # Delete the message containing the key for privacy
        if ctx.message:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

        success = await set_user_key(ctx.author.id, api_key)
        if success:
            await ctx.send(
                embed=discord.Embed(
                    title="✅ API Key Saved",
                    description=(
                        "Your Gemini API key has been securely stored.\n"
                        "You now have unlimited /site usage (subject to Google's limits).\n"
                        "Your key is never shared or included in generated projects."
                    ),
                    color=COLOR_OK,
                ),
                delete_after=15,
            )
        else:
            await ctx.send(
                embed=discord.Embed(
                    title="❌ Failed to Save Key",
                    description="Could not store your API key. Please try again.",
                    color=COLOR_ERR,
                ),
                delete_after=10,
            )

    # ── /delkey — remove your API key ─────────────────────────────────────────

    @commands.hybrid_command(
        name="delkey",
        description="Remove your stored Gemini API key",
    )
    async def delkey_cmd(self, ctx: commands.Context) -> None:
        from site_store import remove_user_key
        success = await remove_user_key(ctx.author.id)
        if success:
            await ctx.send(
                embed=discord.Embed(
                    description="✅ Your API key has been removed. You're back to free /site limits.",
                    color=COLOR_OK,
                ),
                delete_after=10,
            )
        else:
            await ctx.send(
                embed=discord.Embed(
                    description="❌ No key found or could not remove.",
                    color=COLOR_ERR,
                ),
                delete_after=10,
            )

    # ── /mysites — list your projects ─────────────────────────────────────────

    @commands.hybrid_command(
        name="mysites",
        description="List your generated sites",
    )
    async def mysites_cmd(self, ctx: commands.Context) -> None:
        from site_store import get_user_projects
        projects = await get_user_projects(ctx.author.id)
        if not projects:
            await ctx.send(
                embed=discord.Embed(
                    description="You haven't created any sites yet. Use `/site <description>` to get started!",
                    color=BOT_COLOR,
                ),
                delete_after=15,
            )
            return

        embed = discord.Embed(
            title="🏗️ Your Sites",
            color=BOT_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        for p in projects[:10]:
            pid = str(p["id"])[:8]
            name = p.get("project_name", "Untitled")
            status = p.get("build_status", "?")
            icon = "✅" if status == "success" else "⚠️"
            embed.add_field(
                name=f"{icon} {name}",
                value=f"ID: `{pid}…` | Status: {status}",
                inline=False,
            )
        embed.set_footer(text=f"Showing {min(len(projects), 10)} of {len(projects)} projects")
        await ctx.send(embed=embed)

    # ── /editlog — view edit history ───────────────────────────────────────────

    @commands.hybrid_command(
        name="editlog",
        description="View the edit history of your latest site",
    )
    async def editlog_cmd(self, ctx: commands.Context) -> None:
        project = await get_latest_user_project(ctx.author.id)
        if not project:
            await ctx.send("You don't have any projects yet.", delete_after=10)
            return

        logs = await get_edit_logs(str(project["id"]))
        if not logs:
            await ctx.send("No edit history found.", delete_after=10)
            return

        embed = discord.Embed(
            title="📝 Edit Log",
            description=f"Project: **{project.get('project_name', 'Untitled')}**",
            color=BOT_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        for entry in logs[-15:]:
            ts = entry.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts_str = dt.strftime("%H:%M")
                except Exception:
                    ts_str = "??:??"
            else:
                ts_str = "??:??"
            desc = entry.get("description", "")
            debug = entry.get("debug_status", "none")
            debug_icon = ""
            if debug == "fixed":
                debug_icon = " 🔧"
            elif debug == "failed":
                debug_icon = " ❌"
            embed.add_field(
                name=f"{ts_str} — {desc}{debug_icon}",
                value=f"Files: {', '.join(entry.get('files_affected', [])[:5])}",
                inline=False,
            )
        embed.set_footer(text="Botdi App Engineering • Edit History")
        await ctx.send(embed=embed)

    # ── Error handler ──────────────────────────────────────────────────────────

    @site_cmd.error
    async def site_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❌ Usage: `/site <description>`\n"
                "Example: `/site Create a modern portfolio website`",
                delete_after=15,
            )
        else:
            await ctx.send(f"❌ Error: {error}", delete_after=10)


# ── Result view with buttons ───────────────────────────────────────────────────

class SiteResultView(discord.ui.View):
    """Buttons for the site result message: Open Preview, Download, Edit Log."""

    def __init__(self, project_id: str, preview_url: str) -> None:
        super().__init__(timeout=300)  # 5 min timeout
        self.project_id = project_id
        self.preview_url = preview_url
        self.add_item(discord.ui.Button(
            label="🌐 Open Preview",
            style=discord.ButtonStyle.link,
            url=preview_url,
            row=0,
        ))
        self.add_item(discord.ui.Button(
            label="📝 Edit Log",
            style=discord.ButtonStyle.secondary,
            custom_id=f"site:editlog:{project_id}",
            row=0,
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Site(bot))
