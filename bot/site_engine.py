"""
App Engineering engine — AI generation, provider fallback, build, debug, screenshot, ZIP.

Pipeline: Prompt -> Plan -> Generate -> Build -> Test -> Debug -> Screenshot -> Preview -> Deliver

Security:
  - API keys are server-side env vars only, never sent to Discord or embedded in projects.
  - Generated code is validated, not executed on the main bot host.
  - Sandboxed file paths per project.
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
from typing import Any

import aiohttp

from config import (
    SITE_PROVIDER_CHAIN,
    SITE_MAX_DEBUG_RETRIES,
    SITE_PREVIEW_BASE_URL,
    SITE_FREE_MONTHLY_LIMIT,
)
import site_store

log = logging.getLogger(__name__)

_ENGINEER_SYSTEM = """\
You are Botdi App Engineering, an AI that generates complete, working website projects.

You output ONLY a single JSON object — no markdown, no explanation, no code fences.
The JSON must have this exact structure:

{
  "files": {
    "index.html": "<!DOCTYPE html>...",
    "style.css": "body { ... }",
    "script.js": "..."
  },
  "summary": "One-line description of what was built",
  "dependencies": [],
  "external_requests": [],
  "permissions": "Sandbox only"
}

Rules:
1. Generate complete, self-contained HTML/CSS/JS files. No placeholders.
2. All CSS goes in style.css, all JS in script.js, HTML in index.html.
3. Do NOT include any API keys, secrets, tokens, or environment variables in the files.
4. Do NOT reference external build tools or npm packages — pure HTML/CSS/JS only.
5. Make the site responsive and modern-looking.
6. If editing an existing project, modify the provided files and return ALL files.
7. Output ONLY the JSON object. No text before or after.
"""

_EDIT_SYSTEM = """\
You are Botdi App Engineering, editing an existing website project.

You will receive the current project files and an edit request.
Output ONLY a single JSON object with the same structure as the generate step.
Return ALL files (modified and unmodified). No markdown, no explanation.

Rules:
1. Modify only what the user asked for. Keep working parts intact.
2. Do NOT include any API keys, secrets, or tokens in the files.
3. Keep all files self-contained HTML/CSS/JS.
4. Output ONLY the JSON object.
"""

_DEBUG_SYSTEM = """\
You are Botdi App Engineering, fixing a build/runtime error in a website project.

You will receive the current files and an error message.
Fix the error and output ONLY a JSON object with the same structure (all files).
Do NOT add comments about what you changed — just return the fixed files.
Output ONLY the JSON object.
"""


async def _call_provider(
    provider: dict[str, str],
    messages: list[dict],
    timeout: float = 60.0,
) -> str | None:
    api_key = provider.get("api_key", "")
    if not api_key:
        return None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if provider["name"] == "openrouter":
        headers["HTTP-Referer"] = "https://botdi.app"
        headers["X-Title"] = "Botdi App Engineering"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                provider["url"],
                headers=headers,
                json={
                    "model": provider["model"],
                    "messages": messages,
                    "max_tokens": 8000,
                    "temperature": 0.4,
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("[site:%s] HTTP %s: %s", provider["name"], resp.status, body[:200])
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except asyncio.TimeoutError:
        log.warning("[site:%s] timed out", provider["name"])
    except Exception as exc:
        log.warning("[site:%s] %s", provider["name"], exc)
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


async def _generate_with_fallback(
    system: str, user_prompt: str, user_gemini_key: str | None = None
) -> str | None:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]
    if user_gemini_key:
        result = await _call_user_gemini(user_gemini_key, messages)
        if result:
            return result
    for provider in SITE_PROVIDER_CHAIN:
        result = await _call_provider(provider, messages)
        if result:
            return result
    return None


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
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            await page.set_content(full_html, wait_until="networkidle")
            await page.wait_for_timeout(1500)
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
            "Generated by Botdi App Engineering\n"
            "This project contains HTML, CSS, and JavaScript files.\n"
            "Open index.html in your browser to view the site.\n",
        )
    buf.seek(0)
    return buf


def _preview_url(project_id: str) -> str:
    return f"{SITE_PREVIEW_BASE_URL}/{project_id}"


def _analyze_project(files: dict[str, str]) -> dict[str, Any]:
    file_types = set()
    external_requests: list[str] = []
    dependencies = 0
    for fname, content in files.items():
        ext = fname.rsplit(".", 1)[-1] if "." in fname else "unknown"
        file_types.add(ext)
        if ext == "html":
            for m in re.finditer(r'(?:src|href)\s*=\s*["\']https?://([^"\']+)', content):
                domain = m.group(1).split("/")[0]
                if domain not in external_requests:
                    external_requests.append(domain)
        if ext == "js":
            dependencies += content.count("import ") + content.count("require(")
    contains = ", ".join(sorted(f.upper() for f in file_types if f in ("html", "css", "js")))
    return {
        "contains": contains or "HTML/CSS/JS",
        "dependencies": dependencies,
        "external_requests": external_requests if external_requests else None,
        "api_usage": "None",
        "permissions": "Sandbox only",
    }


async def generate_project(
    prompt: str, user_id: int, user_gemini_key: str | None = None
) -> dict[str, Any] | None:
    project = await site_store.create_project(user_id, prompt)
    pid = project["id"]
    ai_prompt = f"Build a website with this description:\n\n{prompt}\n\nGenerate all files now."
    raw = await _generate_with_fallback(_ENGINEER_SYSTEM, ai_prompt, user_gemini_key)
    if not raw:
        await site_store.update_project(pid, {"build_status": "failed"})
        await site_store.add_edit_log_entry(pid, "Generation failed — no AI provider available", [], "failed")
        return None
    parsed = _extract_json(raw)
    if not parsed or "files" not in parsed:
        await site_store.update_project(pid, {"build_status": "failed"})
        await site_store.add_edit_log_entry(pid, "Generation failed — invalid AI response", [], "failed")
        return None
    files = _strip_secrets(parsed["files"])
    ok, msg = _validate_project(files)
    if not ok:
        files, debug_ok, debug_msg = await _auto_debug(pid, files, msg, user_gemini_key)
        if not debug_ok:
            await site_store.update_project(pid, {"build_status": "failed"})
            await site_store.add_edit_log_entry(pid, f"Build failed: {debug_msg}", list(files.keys()), "failed", "failed")
            return {"project_id": pid, "files": files, "build_status": "failed", "error": debug_msg}
    await site_store.set_project_files(pid, files)
    await site_store.update_project(pid, {"build_status": "success", "preview_url": _preview_url(pid)})
    await site_store.add_edit_log_entry(pid, "Created project files", list(files.keys()), "success")
    await site_store.save_checkpoint(pid, "Initial build")
    screenshot = await _capture_screenshot(files, pid)
    screenshot_path = None
    if screenshot:
        sdir = site_store.DATA_DIR / "screenshots"
        sdir.mkdir(exist_ok=True)
        spath = sdir / f"{pid}.png"
        spath.write_bytes(screenshot)
        screenshot_path = str(spath)
        await site_store.update_project(pid, {"screenshot_path": screenshot_path})
    return {
        "project_id": pid,
        "files": files,
        "build_status": "success",
        "preview_url": _preview_url(pid),
        "screenshot": screenshot,
        "screenshot_path": screenshot_path,
        "summary": parsed.get("summary", ""),
        "info": _analyze_project(files),
    }


async def edit_project(
    project_id: str, edit_prompt: str, user_gemini_key: str | None = None
) -> dict[str, Any] | None:
    project = await site_store.get_project(project_id)
    if not project:
        return None
    current_files = project["files"]
    ai_prompt = (
        f"Current project files:\n{json.dumps(current_files, indent=2)}\n\n"
        f"Edit request: {edit_prompt}\n\n"
        "Return ALL files with the requested changes."
    )
    raw = await _generate_with_fallback(_EDIT_SYSTEM, ai_prompt, user_gemini_key)
    if not raw:
        return None
    parsed = _extract_json(raw)
    if not parsed or "files" not in parsed:
        return None
    files = _strip_secrets(parsed["files"])
    ok, msg = _validate_project(files)
    if not ok:
        files, debug_ok, debug_msg = await _auto_debug(project_id, files, msg, user_gemini_key)
        if not debug_ok:
            await site_store.update_project(project_id, {"build_status": "failed"})
            await site_store.add_edit_log_entry(project_id, f"Edit failed: {debug_msg}", list(files.keys()), "failed", "failed")
            return {"project_id": project_id, "files": files, "build_status": "failed", "error": debug_msg}
    await site_store.set_project_files(project_id, files)
    await site_store.update_project(project_id, {"build_status": "success", "preview_url": _preview_url(project_id)})
    await site_store.add_edit_log_entry(project_id, f"Edited: {edit_prompt[:100]}", list(files.keys()), "success")
    await site_store.save_checkpoint(project_id, f"Edit: {edit_prompt[:50]}")
    screenshot = await _capture_screenshot(files, project_id)
    screenshot_path = project.get("screenshot_path")
    if screenshot:
        sdir = site_store.DATA_DIR / "screenshots"
        sdir.mkdir(exist_ok=True)
        spath = sdir / f"{project_id}.png"
        spath.write_bytes(screenshot)
        screenshot_path = str(spath)
        await site_store.update_project(project_id, {"screenshot_path": screenshot_path})
    return {
        "project_id": project_id,
        "files": files,
        "build_status": "success",
        "preview_url": _preview_url(project_id),
        "screenshot": screenshot,
        "screenshot_path": screenshot_path,
        "info": _analyze_project(files),
    }


async def _auto_debug(
    project_id: str,
    files: dict[str, str],
    error: str,
    user_gemini_key: str | None = None,
) -> tuple[dict[str, str], bool, str]:
    for attempt in range(SITE_MAX_DEBUG_RETRIES):
        log.info("[site:%s] Debug attempt %d/%d", project_id, attempt + 1, SITE_MAX_DEBUG_RETRIES)
        await site_store.add_edit_log_entry(
            project_id, f"Auto-debug attempt {attempt + 1}: {error[:100]}",
            list(files.keys()), "debugging", "in_progress",
        )
        debug_prompt = (
            f"Current files:\n{json.dumps(files, indent=2)}\n\n"
            f"Error: {error}\n\nFix the error and return all files."
        )
        raw = await _generate_with_fallback(_DEBUG_SYSTEM, debug_prompt, user_gemini_key)
        if not raw:
            return files, False, "Debug AI unavailable"
        parsed = _extract_json(raw)
        if not parsed or "files" not in parsed:
            return files, False, "Debug AI returned invalid response"
        files = _strip_secrets(parsed["files"])
        ok, msg = _validate_project(files)
        if ok:
            await site_store.add_edit_log_entry(
                project_id, f"Fixed on attempt {attempt + 1}",
                list(files.keys()), "success", "fixed",
            )
            return files, True, "Fixed"
        error = msg
    return files, False, f"Could not fix after {SITE_MAX_DEBUG_RETRIES} attempts"
