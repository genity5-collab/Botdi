export interface BotStatusRow {
  online: boolean;
  bot_name: string;
  bot_id: string;
  guild_count: number;
  uptime_seconds: number;
  started_at: string;
  last_updated: string;
}

export const botStatusDb = {
  async getBotStatus(): Promise<BotStatusRow | null> {
    return null;
  },
};
