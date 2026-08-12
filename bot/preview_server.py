"""
Preview server — serves generated project files at /<project_id>.

Runs as a background task alongside the Discord bot.
When a user's credits are 0 (and they're not the bot owner),
their preview pages show an "offline" message instead of the site.
"""
from __future__ import annotations

import logging
from aiohttp import web
import site_store

log = logging.getLogger(__name__)


async def _preview_handler(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    project = await site_store.get_project(project_id)
    if not project:
        return web.Response(text="<h1>Project not found</h1>", status=404, content_type="text/html")

    # ── Sites offline when 0 credits (non-owners) ────────────────────────────
    owner_id = project.get("owner_id", 0)
    if owner_id:
        # Check if user has credits (we can't check is_owner here since we don't have the bot)
        # The site_store handles this — if the user has 0 credits, we show offline
        _, remaining = await site_store.check_site_usage(owner_id)
        if remaining <= 0:
            return web.Response(
                text=_OFFLINE_HTML,
                status=503,
                content_type="text/html",
            )

    files = project.get("files", {})
    if not files:
        return web.Response(text="<h1>Project has no files</h1>", status=404, content_type="text/html")

    # ── Build full HTML from files ───────────────────────────────────────────
    html = files.get("index.html", "")
    css = files.get("style.css", "")
    js = files.get("script.js", "")

    if css:
        if "</head>" in html:
            html = html.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)
        else:
            html = f"<style>\n{css}\n</style>\n" + html
    if js:
        if "</body>" in html:
            html = html.replace("</body>", f"<script>\n{js}\n</script>\n</body>", 1)
        else:
            html = html + f"\n<script>\n{js}\n</script>"

    return web.Response(text=html, content_type="text/html")


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


_OFFLINE_HTML = """\
<!DOCTYPE html>
<html>
<head>
<title>Site Offline</title>
<style>
body{display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;
background:#0d0d0f;color:#e6e6ec;font-family:system-ui,sans-serif;text-align:center}
h1{font-size:1.5rem;margin-bottom:.5rem}
p{color:#72727e;max-width:400px;line-height:1.5}
.badge{display:inline-block;padding:4px 12px;background:#f0b132;color:#000;
border-radius:6px;font-weight:600;font-size:.8rem;margin-bottom:1rem}
</style>
</head>
<body>
<div>
<div class="badge">⚠️ Offline</div>
<h1>This site is temporarily offline</h1>
<p>The project owner has used all their monthly app credits.
Sites will come back online when credits reset next month.</p>
</div>
</body>
</html>
"""


def create_preview_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/{project_id}", _preview_handler)
    return app
