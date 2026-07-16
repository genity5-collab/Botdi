"""
Roblox live-lookup helper (public API, no key required).
Used as a tool by the AI cog and by the /roblox slash command.
"""
from __future__ import annotations

import asyncio
import aiohttp

_TIMEOUT = aiohttp.ClientTimeout(total=8)


async def _get_json(session: aiohttp.ClientSession, url: str, **kwargs) -> dict | list | None:
    try:
        async with session.get(url, timeout=_TIMEOUT, **kwargs) as r:
            if r.status != 200:
                return None
            return await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


async def search_games(query: str, limit: int = 5) -> list[dict]:
    """Return live Roblox games matching `query` with player counts."""
    async with aiohttp.ClientSession() as s:
        data = await _get_json(
            s,
            "https://games.roblox.com/v1/games/list",
            params={"model.keyword": query, "model.maxRows": limit},
        )
        if not data or "games" not in data:
            return []
        out = []
        for g in data["games"][:limit]:
            out.append({
                "name": g.get("name"),
                "creator": (g.get("creatorName") or ""),
                "playing": g.get("playerCount", 0),
                "total_up_votes": g.get("totalUpVotes", 0),
                "total_down_votes": g.get("totalDownVotes", 0),
                "place_id": g.get("placeId"),
                "url": f"https://www.roblox.com/games/{g.get('placeId')}" if g.get("placeId") else None,
            })
        return out


async def lookup_user(username: str) -> dict | None:
    """Resolve a Roblox username → profile info + avatar headshot."""
    async with aiohttp.ClientSession() as s:
        data = await _get_json(
            s,
            "https://users.roblox.com/v1/users/search",
            params={"keyword": username, "limit": 1},
        )
        if not data or not data.get("data"):
            return None
        u = data["data"][0]
        uid = u.get("id")
        profile = await _get_json(s, f"https://users.roblox.com/v1/users/{uid}") or {}
        thumb = await _get_json(
            s,
            "https://thumbnails.roblox.com/v1/users/avatar-headshot",
            params={"userIds": uid, "size": "150x150", "format": "Png"},
        )
        avatar_url = None
        if thumb and thumb.get("data"):
            avatar_url = thumb["data"][0].get("imageUrl")
        return {
            "id": uid,
            "name": profile.get("name") or u.get("name"),
            "display_name": profile.get("displayName") or u.get("displayName"),
            "description": (profile.get("description") or "").strip(),
            "created": profile.get("created"),
            "is_banned": profile.get("isBanned"),
            "avatar_url": avatar_url,
            "profile_url": f"https://www.roblox.com/users/{uid}/profile",
        }


async def trending_games(limit: int = 5) -> list[dict]:
    """Return top games by concurrent players right now."""
    async with aiohttp.ClientSession() as s:
        data = await _get_json(
            s,
            "https://games.roblox.com/v1/games/list",
            params={"model.sortToken": "TopEarning", "model.maxRows": limit},
        )
        if not data or "games" not in data:
            return []
        return [{
            "name": g.get("name"),
            "playing": g.get("playerCount", 0),
            "place_id": g.get("placeId"),
            "url": f"https://www.roblox.com/games/{g.get('placeId')}",
        } for g in data["games"][:limit]]


def format_games(games: list[dict]) -> str:
    if not games:
        return "No matching Roblox games found."
    lines = []
    for g in games:
        line = f"• **{g['name']}**"
        if g.get("creator"):
            line += f" by {g['creator']}"
        line += f" — {g.get('playing', 0):,} playing"
        if g.get("url"):
            line += f" — <{g['url']}>"
        lines.append(line)
    return "\n".join(lines)


def format_user(u: dict) -> str:
    lines = [f"**{u['display_name']}** (@{u['name']}) — ID `{u['id']}`"]
    if u.get("description"):
        desc = u["description"]
        if len(desc) > 200:
            desc = desc[:200] + "…"
        lines.append(f"> {desc}")
    if u.get("created"):
        lines.append(f"Joined: {u['created'][:10]}")
    if u.get("profile_url"):
        lines.append(f"<{u['profile_url']}>")
    return "\n".join(lines)
