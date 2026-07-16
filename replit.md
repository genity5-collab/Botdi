# Nexus

A multi-purpose Discord bot with slash commands, AI chat, moderation, tickets,
and live Roblox knowledge — powered by Gemini (vision-capable).

## Features

- **AI chat** — @mention, DM, or say `nexus …`. Plain-text replies chunked at
  Discord's 2000-char cap (no embeds). Understands attached images and GIFs.
- **Per-user memory** — up to 100 exchanges kept per user, older turns auto-summarized.
- **/teach** — server admins add persistent facts Nexus uses for that guild.
- **/roblox** — live lookup (games, users, trending) via the public Roblox API.
- **Moderation & tickets** — strikes, mute/kick/ban, DM support tickets.

## Run

- `python -m bot.main` — start the bot (needs env vars below)
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — typecheck all workspace packages
- `pnpm run build` — typecheck + build

## Required env

- `DISCORD_TOKEN`, `GEMINI_API_KEY`, `ADMIN_CHANNEL_ID`, `LOG_CHANNEL_ID`, `SUPPORT_LINK`
- Optional: `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, `DATABASE_URL`

## Stack

- discord.py 2.7, google-genai 2.11 (Gemini vision)
- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5, DB: PostgreSQL + Drizzle, Validation: Zod
- Build: esbuild

## Slash commands

- **AI:** `/ask` `/forget` `/teach` (admin) `/untutor` (admin) `/roblox`
- **General:** `/ping` `/uptime` `/userinfo` `/serverinfo` `/help`
- **Moderation:** `/strike` `/strikes` `/mute` `/unmute` `/warn` `/kick` `/ban` `/purge` `/slowmode` `/lock` `/unlock`
- **Fun:** `/roll` `/flip` `/8ball` `/poll` `/avatar` `/botinfo`

## Notes

- The `/teach` store is per-guild; facts are injected into the AI system prompt
  for messages in that guild.
- Roblox knowledge is always fetched live — the bot does not memorize a game
  catalog. Ask about a specific game, user, or "what's trending on Roblox".
