import { Router } from "express";
import fs from "fs";
import path from "path";

const router = Router();

// process.cwd() is artifacts/api-server when run by pnpm --filter
// so ../../bot/data resolves to <workspace-root>/bot/data
const BOT_DATA_DIR = path.resolve(process.cwd(), "../../bot/data");

function readJson(file: string): unknown {
  try {
    return JSON.parse(fs.readFileSync(path.join(BOT_DATA_DIR, file), "utf8"));
  } catch {
    return null;
  }
}

// GET /api/bot/status
router.get("/bot/status", (_req, res) => {
  const data = readJson("status.json");
  if (!data) {
    return res.json({
      online: false,
      bot_name: "",
      bot_id: "",
      guild_count: 0,
      uptime_seconds: 0,
      started_at: "",
      last_updated: new Date().toISOString(),
    });
  }
  res.json(data);
});

// GET /api/bot/logs?limit=100
router.get("/bot/logs", (req, res) => {
  const limit = Math.min(parseInt((req.query.limit as string) ?? "100", 10) || 100, 500);
  const data = readJson("recent_logs.json") as { entries?: unknown[] } | null;
  const entries = data?.entries ?? [];
  res.json({ entries: (entries as unknown[]).slice(-limit) });
});

// GET /api/bot/strikes
router.get("/bot/strikes", (_req, res) => {
  const data = readJson("strikes.json") as Record<string, number> | null;
  res.json({ strikes: data ?? {} });
});

// GET /api/bot/tickets
router.get("/bot/tickets", (_req, res) => {
  const data = readJson("tickets.json") as Record<string, unknown> | null;
  res.json({ tickets: data ?? {} });
});

export default router;
