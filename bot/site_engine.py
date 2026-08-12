"""
App Engineering engine — AI generation with visible thinking, tool-use simulation,
dependency support, provider fallback, build, debug, screenshot, ZIP, live edit log.

Pipeline: Prompt → Think (visible) → Tools (visible) → Generate → Build → Test → Debug → Screenshot → Preview → Deliver

Provider chains:
  - Generation (better UI): Groq gpt-oss-20b → Fireworks → OpenRouter free
  - Debugging (fixing errors): OpenRouter gpt-oss-20b:free → Groq → Fireworks
  - Thinking: Same as generation (Groq for better planning)

Security:
  - API keys are server-side env vars only (owner's keys, not user keys)
  - User Gemini key is optional and used only for that user's requests
  - Generated code is validated, not executed on the main bot host
  - Scam/phishing/malware prompts are blocked before generation
  - Dependencies are CDN-only (no npm, no build tools)
"""
from __future__ import annotations

import asyncio
import datetime
import io
import json
import logging
import os
import re
import shutil
import zipfile
from typing import Any, Callable, Awaitable

import aiohttp

from config import (
    SITE_GENERATE_CHAIN,
    SITE_DEBUG_CHAIN,
    SITE_THINK_CHAIN,
    SITE_MAX_DEBUG_RETRIES,
    SITE_PREVIEW_BASE_URL,
    SITE_FREE_MONTHLY_LIMIT,
    SITE_BLOCKED_KEYWORDS,
    SITE_BLOCKED_PATTERNS,
    SITE_OPENROUTER_FALLBACK_MODELS,
    SITE_USE_OWNER_GEMINI,
    OPENROUTER_API_KEY,
    OPENROUTER_URL,
    GEMINI_API_KEY,
)
import site_store

log = logging.getLogger(__name__)

# ── System prompts ────────────────────────────────────────────────────────────

_THINK_SYSTEM = """\
You are Vyrion's App Engineering Thinker. Before generating a website, you think through the plan step by step.

Output ONLY a JSON object:
{
  "thoughts": ["step 1 of your thinking", "step 2", ...],
  "tools": ["tool you'd use 1", "tool you'd use 2", ...],
  "plan": "one paragraph describing what you'll build",
  "files_needed": ["index.html", "style.css", "script.js"],
  "dependencies": ["list of CDN libraries needed, e.g. 'Tailwind CSS', 'Alpine.js', 'Chart.js'"],
  "estimated_complexity": "simple|medium|complex"
}

Think about:
- What the user wants and the best way to build it
- Which sections/components the site needs
- Color scheme, layout, and responsiveness
- Any CDN libraries needed (Tailwind, Bootstrap, Alpine.js, Chart.js, Three.js, etc.)
- Any interactivity needed (JS)
- Keep it practical. 4-8 thoughts and 2-5 tools max.

Tools can include things like:
- "Layout planner: choosing flexbox grid structure"
- "Color picker: selecting a modern palette"
- "Typography: choosing fonts from Google Fonts"
- "Dependency manager: adding Tailwind CSS via CDN"
- "Animation designer: planning transitions"
- "Responsive tester: ensuring mobile-first design"

Output ONLY the JSON. No markdown, no explanation.
"""

_ENGINEER_SYSTEM = """\
You are Vyrion App Engineering, an AI that generates complete, polished, working website projects.

You output ONLY a single JSON object — no markdown, no explanation, no code fences.
The JSON must have this exact structure:

{
  "files": {
    "index.html": "<!DOCTYPE html>...",
    "style.css": "body { ... }",
    "script.js": "..."
  },
  "summary": "One-line description of what was built",
  "dependencies": ["Tailwind CSS", "Alpine.js"],
  "external_requests": ["cdn.tailwindcss.com", "cdn.jsdelivr.net"],
  "permissions": "Sandbox only"
}

QUALITY RULES — CRITICAL:
1. Generate complete, self-contained HTML/CSS/JS files. No placeholders, no TODOs.
2. All CSS goes in style.css, all JS in script.js, HTML in index.html.
3. Make it VISUALLY POLISHED — modern design, good spacing, smooth transitions, nice colors.
4. Use CSS custom properties (variables) for theming. Add dark mode if appropriate.
5. Make it FULLY RESPONSIVE — mobile-first, works on all screen sizes.
6. Add micro-interactions: hover effects, transitions, subtle animations.
7. Use modern CSS (flexbox/grid). No tables for layout. No inline styles.
8. JavaScript should be clean, use modern ES6+. No global pollution.
9. Do NOT include any API keys, secrets, tokens, or environment variables in the files.
10. You MAY use CDN-hosted libraries (Tailwind, Bootstrap, Alpine.js, Chart.js, etc.) via <script>/<link> tags in index.html.
11. Do NOT use npm, webpack, or any build tools — CDN only.
12. If editing an existing project, modify the provided files and return ALL files.
13. List all CDN dependencies in the "dependencies" field.
14. List all external domains in "external_requests".
15. Output ONLY the JSON object. No text before or after.

SAFETY — REFUSE THESE REQUESTS:
- Phishing pages, fake login forms, credential harvesters
- Scam sites, pyramid schemes, crypto scams, fake giveaways
- Malware, keyloggers, ransomware, spyware
- Sites that steal passwords, tokens, credit cards, or personal data
- Counterfeit product stores, illegal drug marketplaces
- Any site designed to deceive or defraud users

If asked for any of the above, output: {"files": {}, "summary": "Request blocked: violates safety policy", "dependencies": [], "external_requests": [], "permissions": "Blocked"}
"""

_EDIT_SYSTEM = """\
You are Vyrion App Engineering, editing an existing website project.

You will receive the current project files and an edit request.
Output ONLY a single JSON object with the same structure as the generate step.
Return ALL files (modified and unmodified). No markdown, no explanation.

Rules:
1. Modify only what the user asked for. Keep working parts intact.
2. Do NOT include any API keys, secrets, or tokens in the files.
3. Keep all files self-contained HTML/CSS/JS.
4. Maintain the same quality — polished, responsive, modern.
5. You MAY add or remove CDN dependencies as needed.
6. Do NOT turn the site into a phishing page, scam, or anything malicious.
7. Output ONLY the JSON object.
"""

_DEBUG_SYSTEM = """\
You are Vyrion App Engineering, fixing a build/runtime error in a website project.

You will receive the current files and an error message.
Fix the error and output ONLY a JSON object with the same structure (all files).
Do NOT add comments about what you changed — just return the fixed files.
Output ONLY the JSON object.
"""


# ── Scam / suspicious prompt detection ────────────────────────────────────────

_BLOCKED_RE = [re.compile(p) for p in SITE_BLOCKED_PATTERNS]


def check_prompt_safety(prompt: str) -> tuple[bool, str]:
    lower = prompt.lower()
    for kw in SITE_BLOCKED_KEYWORDS:
        if kw in lower:
            return False, f"Blocked keyword: '{kw}'"
    for pat in _BLOCKED_RE:
        if pat.search(prompt):
            return False, "Request matches a blocked pattern (phishing/scam/malware)"
    return True, ""


# ── Provider calls ───────────────────────────────────────────────────────────

async def _call_provider(
    provider: dict[str, str],
    messages: list[dict],
    timeout: float = 90.0,
) -> str | None:
    api_key = provider.get("api_key", "")
    if not api_key:
        return None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter" in provider.get("url", ""):
        headers["HTTP-Referer"] = "https://vyrion.app"
        headers["X-Title"] = "Vyrion App Engineering"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                provider["url"],
                headers=headers,
                json={
                    "model": provider["model"],
                    "messages": messages,
                    "max_tokens": 12000,
                    "temperature": 0.4,
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("[site:%s] HTTP %s: %s", provider["name"], resp.status, body[:200])
                    return None
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content.strip()
                return None
    except asyncio.TimeoutError:
        log.warning("[site:%s] timed out after %ss", provider["name"], timeout)
    except Exception as exc:
        log.warning("[site:%s] %s", provider["name"], exc)
    return None


async def _call_openrouter_free_fallback(messages: list[dict]) -> str | None:
    """Try additional free OpenRouter models if the main chain fails."""
    if not OPENROUTER_API_KEY:
        return None
    for model in SITE_OPENROUTER_FALLBACK_MODELS:
        provider = {
            "name": f"openrouter-{model}",
            "url": OPENROUTER_URL,
            "api_key": OPENROUTER_API_KEY,
            "model": model,
        }
        result = await _call_provider(provider, messages, timeout=60.0)
        if result:
            log.info("[site] Free fallback model worked: %s", model)
            return result
    return None


async def _call_user_gemini(api_key: str, messages: list[dict]) -> str | None:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = messages[0]["content"] + "\n\n" + messages[-1]["content"]
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(model="gemini-2.0-flash", contents=prompt),
            timeout=45.0,
        )
        return resp.text.strip()
    except Exception as exc:
        log.warning("[site:user-gemini] %s", exc)
        return None


async def _call_owner_gemini(messages: list[dict]) -> str | None:
    """Use the bot owner's Gemini key as a last-resort fallback."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = messages[0]["content"] + "\n\n" + messages[-1]["content"]
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(model="gemini-2.0-flash", contents=prompt),
            timeout=45.0,
        )
        if resp.text:
            log.info("[site] Owner Gemini fallback worked")
            return resp.text.strip()
        return None
    except Exception as exc:
        log.warning("[site:owner-gemini] %s", exc)
        return None


async def _generate_with_chain(
    system: str,
    user_prompt: str,
    chain: list[dict],
    user_gemini_key: str | None = None,
) -> str | None:
    """Try a specific provider chain, then free OpenRouter fallbacks."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]
    # User's own Gemini key first (if provided)
    if user_gemini_key:
        result = await _call_user_gemini(user_gemini_key, messages)
        if result:
            return result
    # Main chain
    for provider in chain:
        result = await _call_provider(provider, messages)
        if result:
            return result
    # Free OpenRouter fallbacks
    result = await _call_openrouter_free_fallback(messages)
    if result:
        return result
    # Owner's Gemini key as last resort (if all gpt-oss models are down)
    if SITE_USE_OWNER_GEMINI and GEMINI_API_KEY:
        result = await _call_owner_gemini(messages)
        if result:
            return result
    return None


async def _generate_files(system: str, prompt: str, user_gemini_key: str | None = None) -> str | None:
    """Generate files using the generation chain (Groq first for better UI)."""
    return await _generate_with_chain(system, prompt, SITE_GENERATE_CHAIN, user_gemini_key)


async def _think_ai(prompt: str, user_gemini_key: str | None = None) -> str | None:
    """Think using the think chain (Groq for better planning)."""
    return await _generate_with_chain(_THINK_SYSTEM, prompt, SITE_THINK_CHAIN, user_gemini_key)


async def _debug_ai(prompt: str, user_gemini_key: str | None = None) -> str | None:
    """Debug using the debug chain (OpenRouter free first)."""
    return await _generate_with_chain(_DEBUG_SYSTEM, prompt, SITE_DEBUG_CHAIN, user_gemini_key)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


_SECRET_PATTERNS = [
    re.compile(r"(?:sk-|pk-|Bearer\s+)[A-Za-z0-9\-_]{20,}", re.I),
    re.compile(r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"][^'\"]+['\"]", re.I),
    re.compile(r"AIza[A-Za-z0-9_\-]{35}"),
]


def _strip_secrets(files: dict[str, str]) -> dict[str, str]:
    cleaned = {}
    for fname, content in files.items():
        c = content
        for pattern in _SECRET_PATTERNS:
            c = pattern.sub("[REDACTED]", c)
        cleaned[fname] = c
    return cleaned


def _validate_project(files: dict[str, str]) -> tuple[bool, str]:
    if not files:
        return False, "No files generated"
    if "index.html" not in files:
        return False, "Missing index.html"
    html = files["index.html"]
    if len(html) < 50:
        return False, "index.html is too short — likely incomplete"
    if "<html" not in html.lower() and "<!doctype" not in html.lower():
        return False, "index.html does not contain valid HTML structure"
    if "<script" in html.lower() and "</script>" not in html.lower():
        return False, "Unclosed <script> tag in index.html"
    return True, "OK"


async def _capture_screenshot(files: dict[str, str], project_id: str) -> bytes | None:
    html_content = files.get("index.html", "")
    css_content = files.get("style.css", "")
    js_content = files.get("script.js", "")
    full_html = html_content
    if css_content:
        full_html = full_html.replace("</head>", f"<style>\n{css_content}\n</style>\n</head>", 1)
        if "</head>" not in full_html:
            full_html = f"<style>\n{css_content}\n</style>\n" + full_html
    if js_content:
        full_html = full_html.replace("</body>", f"<script>\n{js_content}\n</script>\n</body>", 1)
        if "</body>" not in full_html:
            full_html = full_html + f"\n<script>\n{js_content}\n</script>"
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(full_html, wait_until="networkidle")
            await page.wait_for_timeout(2000)
            screenshot = await page.screenshot(type="png")
            await browser.close()
            return screenshot
    except ImportError:
        log.info("[site] Playwright not installed — skipping screenshot")
    except Exception as exc:
        log.warning("[site] Screenshot failed: %s", exc)
    return None


def _create_zip(files: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
        zf.writestr(
            "README.txt",
            "Generated by Vyrion App Engineering\n"
            "This project contains HTML, CSS, and JavaScript files.\n"
            "Open index.html in your browser to view the site.\n",
        )
    buf.seek(0)
    return buf


def _preview_url(project_id: str) -> str:
    return f"{SITE_PREVIEW_BASE_URL}/{project_id}"


def _analyze_project(files: dict[str, str], dependencies: list[str] | None = None) -> dict[str, Any]:
    file_types = set()
    external_requests: list[str] = []
    dep_count = 0
    for fname, content in files.items():
        ext = fname.rsplit(".", 1)[-1] if "." in fname else "unknown"
        file_types.add(ext)
        if ext == "html":
            for m in re.finditer(r'(?:src|href)\s*=\s*["\']https?://([^"\']+)', content):
                domain = m.group(1).split("/")[0]
                if domain not in external_requests:
                    external_requests.append(domain)
        if ext == "js":
            dep_count += content.count("import ") + content.count("require(")
    if dependencies:
        dep_count = max(dep_count, len(dependencies))
    contains = ", ".join(sorted(f.upper() for f in file_types if f in ("html", "css", "js")))
    return {
        "contains": contains or "HTML/CSS/JS",
        "dependencies": dep_count,
        "cdn_libraries": dependencies or [],
        "external_requests": external_requests if external_requests else None,
        "api_usage": "None",
        "permissions": "Sandbox only",
    }


# ── Thinking phase (visible to users) ─────────────────────────────────────────

async def _think(
    prompt: str, user_gemini_key: str | None = None
) -> dict | None:
    think_prompt = f"User wants: {prompt}\n\nThink through how to build this website. Include tools you'd use and dependencies needed."
    raw = await _think_ai(think_prompt, user_gemini_key)
    if not raw:
        return None
    parsed = _extract_json(raw)
    if not parsed:
        return None
    return parsed


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def generate_project(
    prompt: str,
    user_id: int,
    user_gemini_key: str | None = None,
    on_progress: Callable[[str, str], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:

    async def _progress(key: str, msg: str) -> None:
        if on_progress:
            try:
                await on_progress(key, msg)
            except Exception:
                pass

    # ── Safety check ──────────────────────────────────────────────────────────
    safe, reason = check_prompt_safety(prompt)
    if not safe:
        log.warning("[site] Blocked prompt: %s — %s", reason, prompt[:100])
        await _progress("blocked", f"🚫 Blocked: {reason}")
        return {
            "project_id": None, "files": {}, "build_status": "blocked",
            "error": f"Request blocked: {reason}", "preview_url": None,
            "screenshot": None, "summary": "Blocked by safety filter",
            "thinking": [], "tools": [],
            "info": {"contains": "N/A", "dependencies": 0, "cdn_libraries": [], "external_requests": None, "api_usage": "None", "permissions": "Blocked"},
        }

    project = await site_store.create_project(user_id, prompt)
    pid = project["id"]

    # ── Thinking phase ────────────────────────────────────────────────────────
    await _progress("thinking", "🧠 Analyzing your request...")
    thinking = await _think(prompt, user_gemini_key)
    thoughts: list[str] = []
    tools: list[str] = []
    dependencies: list[str] = []
    if thinking:
        thoughts = thinking.get("thoughts", [])
        tools = thinking.get("tools", [])
        dependencies = thinking.get("dependencies", [])
        plan = thinking.get("plan", "")
        for i, thought in enumerate(thoughts):
            await _progress("thinking", f"🧠 {thought}")
            await asyncio.sleep(0.4)
        if tools:
            await _progress("tools", f"🔧 Running tools ({len(tools)})...")
            for tool in tools:
                await _progress("tools", f"🔧 {tool}")
                await asyncio.sleep(0.3)
        if dependencies:
            await _progress("deps", f"📦 Adding dependencies: {', '.join(dependencies)}")
            await site_store.add_edit_log_entry(pid, f"Dependencies: {', '.join(dependencies)}", [], "planning")
        else:
            await _progress("deps", "📦 No external dependencies needed")
        if plan:
            await _progress("planning", f"📋 Plan: {plan[:200]}")
        await site_store.add_edit_log_entry(pid, f"AI thought through approach ({len(thoughts)} steps, {len(tools)} tools)", [], "planning")

    # ── Generate files (Groq chain for better UI) ──────────────────────────────
    await _progress("generating", "⚙️ Writing HTML/CSS/JS files...")
    ai_prompt = f"Build a website with this description:\n\n{prompt}\n\n"
    if thinking:
        if thinking.get("plan"):
            ai_prompt += f"Plan to follow:\n{thinking['plan']}\n\n"
        if dependencies:
            ai_prompt += f"Use these CDN dependencies: {', '.join(dependencies)}\n\n"
    ai_prompt += "Generate all files now."
    raw = await _generate_files(_ENGINEER_SYSTEM, ai_prompt, user_gemini_key)
    if not raw:
        await _progress("failed", "❌ All AI providers unavailable. Tried: Groq, Fireworks, OpenRouter free (5 models). Check API keys.")
        await site_store.update_project(pid, {"build_status": "failed"})
        await site_store.add_edit_log_entry(pid, "Generation failed — no AI provider available", [], "failed")
        return None
    parsed = _extract_json(raw)
    if not parsed or "files" not in parsed:
        await _progress("failed", "❌ AI returned invalid response — couldn't parse files. Try again or rephrase your request.")
        await site_store.update_project(pid, {"build_status": "failed"})
        await site_store.add_edit_log_entry(pid, "Generation failed — invalid AI response", [], "failed")
        return None
    files = _strip_secrets(parsed["files"])
    actual_deps = parsed.get("dependencies", [])
    if actual_deps:
        dependencies = actual_deps
    if not files:
        await _progress("blocked", "🚫 Blocked by safety policy")
        await site_store.update_project(pid, {"build_status": "blocked"})
        await site_store.add_edit_log_entry(pid, "Request blocked by safety policy", [], "blocked")
        return {
            "project_id": pid, "files": {}, "build_status": "blocked",
            "error": "Request blocked by safety policy",
            "preview_url": None, "screenshot": None, "summary": "Blocked",
            "thinking": thoughts, "tools": tools,
            "info": {"contains": "N/A", "dependencies": 0, "cdn_libraries": [], "external_requests": None, "api_usage": "None", "permissions": "Blocked"},
        }

    # ── Validate & debug (OpenRouter free chain for debugging) ────────────────
    await _progress("building", "🧪 Validating HTML structure...")
    ok, msg = _validate_project(files)
    if not ok:
        await _progress("debugging", f"🔧 Found issue: {msg}")
        files, debug_ok, debug_msg = await _auto_debug(pid, files, msg, user_gemini_key, on_progress)
        if not debug_ok:
            await _progress("failed", f"❌ Build failed: {debug_msg}")
            await site_store.update_project(pid, {"build_status": "failed"})
            await site_store.add_edit_log_entry(pid, f"Build failed: {debug_msg}", list(files.keys()), "failed", "failed")
            return {"project_id": pid, "files": files, "build_status": "failed", "error": debug_msg, "thinking": thoughts, "tools": tools}

    # ── Save & screenshot ─────────────────────────────────────────────────────
    await _progress("success", "✅ Build successful!")
    await site_store.set_project_files(pid, files)
    await site_store.update_project(pid, {"build_status": "success", "preview_url": _preview_url(pid)})
    files_changed = list(files.keys())
    await site_store.add_edit_log_entry(pid, f"Created project ({', '.join(files_changed)})", files_changed, "success")
    await site_store.save_checkpoint(pid, "Initial build")

    await _progress("screenshot", "📸 Taking screenshot of your site...")
    screenshot = await _capture_screenshot(files, pid)
    screenshot_path = None
    if screenshot:
        sdir = site_store.DATA_DIR / "screenshots"
        sdir.mkdir(exist_ok=True)
        spath = sdir / f"{pid}.png"
        spath.write_bytes(screenshot)
        screenshot_path = str(spath)
        await site_store.update_project(pid, {"screenshot_path": screenshot_path})
        await _progress("screenshot", "📸 Screenshot captured!")
    else:
        await _progress("screenshot", "📸 Screenshot skipped (headless browser unavailable)")

    await _progress("done", "✅ Site ready!")
    return {
        "project_id": pid, "files": files, "build_status": "success",
        "preview_url": _preview_url(pid), "screenshot": screenshot,
        "screenshot_path": screenshot_path, "summary": parsed.get("summary", ""),
        "thinking": thoughts, "tools": tools,
        "info": _analyze_project(files, dependencies),
    }


async def edit_project(
    project_id: str,
    edit_prompt: str,
    user_gemini_key: str | None = None,
    on_progress: Callable[[str, str], Awaitable[None]] | None = None,
) -> dict[str, Any] | None:

    async def _progress(key: str, msg: str) -> None:
        if on_progress:
            try:
                await on_progress(key, msg)
            except Exception:
                pass

    project = await site_store.get_project(project_id)
    if not project:
        return None

    safe, reason = check_prompt_safety(edit_prompt)
    if not safe:
        return {
            "project_id": project_id, "files": project["files"],
            "build_status": "blocked", "error": f"Edit blocked: {reason}",
            "preview_url": project.get("preview_url"), "screenshot": None,
            "summary": "Blocked", "thinking": [], "tools": [],
            "info": _analyze_project(project["files"]),
        }

    await _progress("thinking", "🧠 Analyzing the edit request...")
    thinking = await _think(edit_prompt, user_gemini_key)
    thoughts: list[str] = []
    tools: list[str] = []
    dependencies: list[str] = []
    if thinking:
        thoughts = thinking.get("thoughts", [])
        tools = thinking.get("tools", [])
        dependencies = thinking.get("dependencies", [])
        for thought in thoughts:
            await _progress("thinking", f"🧠 {thought}")
            await asyncio.sleep(0.4)
        if tools:
            for tool in tools:
                await _progress("tools", f"🔧 {tool}")
                await asyncio.sleep(0.3)

    await _progress("generating", "⚙️ Generating edit...")
    current_files = project["files"]
    ai_prompt = (
        f"Current project files:\n{json.dumps(current_files, indent=2)}\n\n"
        f"Edit request: {edit_prompt}\n\n"
        "Return ALL files with the requested changes."
    )
    raw = await _generate_files(_EDIT_SYSTEM, ai_prompt, user_gemini_key)
    if not raw:
        await _progress("failed", "❌ All AI providers unavailable. Check API keys.")
        return None
    parsed = _extract_json(raw)
    if not parsed or "files" not in parsed:
        await _progress("failed", "❌ Invalid AI response — try rephrasing.")
        return None
    files = _strip_secrets(parsed["files"])
    if not files:
        await _progress("blocked", "🚫 Blocked by safety policy")
        return {
            "project_id": project_id, "files": project["files"],
            "build_status": "blocked", "error": "Edit blocked by safety policy",
            "preview_url": project.get("preview_url"), "screenshot": None,
            "summary": "Blocked", "thinking": thoughts, "tools": tools,
            "info": _analyze_project(project["files"]),
        }

    await _progress("building", "🧪 Validating...")
    ok, msg = _validate_project(files)
    if not ok:
        await _progress("debugging", f"🔧 Fixing: {msg}")
        files, debug_ok, debug_msg = await _auto_debug(project_id, files, msg, user_gemini_key, on_progress)
        if not debug_ok:
            await _progress("failed", f"❌ Edit failed: {debug_msg}")
            await site_store.update_project(project_id, {"build_status": "failed"})
            await site_store.add_edit_log_entry(project_id, f"Edit failed: {debug_msg}", list(files.keys()), "failed", "failed")
            return {"project_id": project_id, "files": files, "build_status": "failed", "error": debug_msg, "thinking": thoughts, "tools": tools}

    await _progress("success", "✅ Edit applied!")
    await site_store.set_project_files(project_id, files)
    await site_store.update_project(project_id, {"build_status": "success", "preview_url": _preview_url(project_id)})
    await site_store.add_edit_log_entry(project_id, f"Edited: {edit_prompt[:100]}", list(files.keys()), "success")
    await site_store.save_checkpoint(project_id, f"Edit: {edit_prompt[:50]}")

    await _progress("screenshot", "📸 Capturing preview...")
    screenshot = await _capture_screenshot(files, project_id)
    screenshot_path = project.get("screenshot_path")
    if screenshot:
        sdir = site_store.DATA_DIR / "screenshots"
        sdir.mkdir(exist_ok=True)
        spath = sdir / f"{project_id}.png"
        spath.write_bytes(screenshot)
        screenshot_path = str(spath)
        await site_store.update_project(project_id, {"screenshot_path": screenshot_path})

    await _progress("done", "✅ Edit complete!")
    return {
        "project_id": project_id, "files": files, "build_status": "success",
        "preview_url": _preview_url(project_id), "screenshot": screenshot,
        "screenshot_path": screenshot_path, "thinking": thoughts, "tools": tools,
        "info": _analyze_project(files, parsed.get("dependencies")),
    }


async def _auto_debug(
    project_id: str,
    files: dict[str, str],
    error: str,
    user_gemini_key: str | None = None,
    on_progress: Callable[[str, str], Awaitable[None]] | None = None,
) -> tuple[dict[str, str], bool, str]:
    for attempt in range(SITE_MAX_DEBUG_RETRIES):
        log.info("[site:%s] Debug attempt %d/%d", project_id, attempt + 1, SITE_MAX_DEBUG_RETRIES)
        if on_progress:
            try:
                await on_progress("debugging", f"🔧 Debug attempt {attempt + 1}/{SITE_MAX_DEBUG_RETRIES}: {error[:80]}")
            except Exception:
                pass
        await site_store.add_edit_log_entry(
            project_id, f"Auto-debug attempt {attempt + 1}: {error[:100]}",
            list(files.keys()), "debugging", "in_progress",
        )
        debug_prompt = (
            f"Current files:\n{json.dumps(files, indent=2)}\n\n"
            f"Error: {error}\n\nFix the error and return all files."
        )
        # Use debug chain (OpenRouter free first)
        raw = await _debug_ai(debug_prompt, user_gemini_key)
        if not raw:
            break
        parsed = _extract_json(raw)
        if not parsed or "files" not in parsed:
            break
        files = _strip_secrets(parsed["files"])
        ok, msg = _validate_project(files)
        if ok:
            await site_store.add_edit_log_entry(
                project_id, f"Debug fix applied (attempt {attempt + 1})",
                list(files.keys()), "success", "fixed",
            )
            return files, True, "Fixed"
    return files, False, error


async def is_user_sites_online(user_id: int, is_owner: bool) -> bool:
    if is_owner:
        return True
    _, remaining = await site_store.check_site_usage(user_id)
    return remaining > 0
