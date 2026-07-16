import { Router } from "express";
import { botStatusDb, type BotStatusRow } from "../db.js";

const router = Router();

router.get<"/">("/", async (_req, res) => {
  let data: BotStatusRow | null = null;
  try {
    data = await botStatusDb.getBotStatus();
  } catch {
    data = null;
  }

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
  return res.json(data);
});

export default router;
