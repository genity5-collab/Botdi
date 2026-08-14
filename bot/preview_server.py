from __future__ import annotations

import logging
from pathlib import Path
from aiohttp import web
import site_store

log = logging.getLogger(__name__)

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


@web.middleware
async def _404_middleware(request: web.Request, handler) -> web.Response:
    try:
        return await handler(request)
    except web.HTTPNotFound:
        if request.path.startswith("/api/"):
            return web.json_response({"error": "Not Found"}, status=404)
        return web.Response(
            text="<!DOCTYPE html><html><head><title>404 Not Found</title></head><body><h1>404 Not Found</h1></body></html>",
            status=404,
            content_type="text/html",
        )


async def _studio_handler(request: web.Request) -> web.Response:
    studio_file = Path(__file__).parent / "studio.html"
    if not studio_file.is_file():
        return web.Response(
            text="<h1>Studio page not found</h1>",
            status=404,
            content_type="text/html",
        )
    try:
        content = studio_file.read_text(encoding="utf-8")
        return web.Response(text=content, content_type="text/html")
    except Exception as e:
        log.error("Failed to read studio.html: %s", e)
        return web.Response(
            text="<h1>Error reading studio page</h1>",
            status=500,
            content_type="text/html",
        )


async def _api_projects_handler(request: web.Request) -> web.Response:
    if hasattr(site_store, "_lock"):
        async with site_store._lock:
            projects_dict = dict(getattr(site_store, "_projects", {}))
    else:
        projects_dict = dict(getattr(site_store, "_projects", {}))

    projects_list = []
    for pid, p in projects_dict.items():
        prompt = p.get("prompt", "") or ""
        projects_list.append({
            "id": p.get("id", pid),
            "prompt": prompt[:100],
            "build_status": p.get("build_status", "pending"),
            "created_at": p.get("created_at", ""),
            "owner_id": str(p.get("owner_id", "")),
        })

    return web.json_response(projects_list)


async def _api_project_detail_handler(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    project = await site_store.get_project(project_id)
    if not project:
        return web.json_response({"error": "Project not found"}, status=404)

    details = {
        "id": project.get("id", project_id),
        "owner_id": str(project.get("owner_id", "")),
        "prompt": project.get("prompt", ""),
        "files": project.get("files", {}),
        "build_status": project.get("build_status", "pending"),
        "created_at": project.get("created_at", ""),
        "last_modified": project.get("last_modified", ""),
    }
    return web.json_response(details)


async def _preview_handler(request: web.Request) -> web.Response:
    project_id = request.match_info["project_id"]
    project = await site_store.get_project(project_id)
    if not project:
        return web.Response(text="<h1>Project not found</h1>", status=404, content_type="text/html")

    # Sites offline when 0 credits (non-owners)
    owner_id = project.get("owner_id", 0)
    if owner_id and hasattr(site_store, "check_site_usage"):
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


def create_preview_app() -> web.Application:
    app = web.Application(middlewares=[_404_middleware])
    app.router.add_get("/", _studio_handler)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/api/projects", _api_projects_handler)
    app.router.add_get("/api/project/{project_id}", _api_project_detail_handler)
    app.router.add_get("/view/{project_id}", _preview_handler)
    app.router.add_get("/{project_id}", _preview_handler)
    return app
