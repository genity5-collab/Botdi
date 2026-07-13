import React, { useEffect, useRef, useState } from 'react';
import {
  useHealthCheck,
  getHealthCheckQueryKey,
  useGetBotStatus,
  getGetBotStatusQueryKey,
  useGetBotLogs,
  getGetBotLogsQueryKey,
  useGetBotStrikes,
  getGetBotStrikesQueryKey,
  useGetBotTickets,
  getGetBotTicketsQueryKey,
  healthCheck,
} from '@workspace/api-client-react';

// ── Helpers ───────────────────────────────────────────────────────────────────

const formatUptime = (seconds: number) => {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${h}h ${m}m ${s}s`;
};

const getBadgeClasses = (level: string) => {
  switch (level.toUpperCase()) {
    case 'INFO':    return 'bg-[#5865F2]/10 text-[#5865F2] border-[#5865F2]/20';
    case 'WARNING': return 'bg-[#F0B132]/10 text-[#F0B132] border-[#F0B132]/20';
    case 'ERROR':   return 'bg-[#ED4245]/10 text-[#ED4245] border-[#ED4245]/20';
    case 'DEBUG':   return 'bg-muted/50 text-muted-foreground border-border/50';
    default:        return 'bg-muted text-muted-foreground border-border';
  }
};

// ── Command data ───────────────────────────────────────────────────────────────

const COMMANDS = [
  {
    category: '🤖 AI',
    color: '#5865F2',
    items: [
      { usage: '@Bot <question>', desc: 'Ask the AI anything (60 s cooldown per user)' },
      { usage: '@Bot I\'m being bullied by @user', desc: 'Report bullying — AI investigates recent messages and applies 30-min timeout if confirmed' },
    ],
  },
  {
    category: '⚠️ Moderation',
    color: '#ED4245',
    items: [
      { usage: '!strike <user> [reason]', desc: '1 strike = 24 h timeout · 3 strikes = auto-ban · sends appeal DM', perm: 'Moderate Members' },
      { usage: '!strikes <user>', desc: 'View a user\'s current strike count', perm: 'Moderate Members' },
      { usage: '!mute <user> [minutes] [reason]', desc: 'Timeout a user for N minutes (default 10)', perm: 'Moderate Members' },
      { usage: '!unmute <user>', desc: 'Remove an active timeout', perm: 'Moderate Members' },
      { usage: '!warn <user> [reason]', desc: 'Send a formal warning DM — no strike applied', perm: 'Moderate Members' },
      { usage: '!kick <user> [reason]', desc: 'Kick a member and send appeal DM', perm: 'Kick Members' },
      { usage: '!ban <user> [reason]', desc: 'Ban a user and send appeal DM', perm: 'Ban Members' },
      { usage: '!purge <1–100>', desc: 'Bulk-delete up to 100 recent messages', perm: 'Manage Messages' },
      { usage: '!slowmode <seconds>', desc: 'Set channel slowmode delay (0 = off, max 21600)', perm: 'Manage Channels' },
      { usage: '!lock [#channel]', desc: 'Lock a channel — members cannot send messages', perm: 'Manage Channels' },
      { usage: '!unlock [#channel]', desc: 'Unlock a previously locked channel', perm: 'Manage Channels' },
    ],
  },
  {
    category: '🎫 Support',
    color: '#23A55A',
    items: [
      { usage: 'DM the bot', desc: 'Opens persistent ticket menu: Exploiter · Bug · Strike Report · Other' },
      { usage: '!reply <ticket_id> <msg>', desc: 'Send a DM reply to the ticket owner (staff)', perm: 'Moderate Members' },
      { usage: '!close <ticket_id>', desc: 'Close a ticket and notify the user (staff)', perm: 'Moderate Members' },
    ],
  },
  {
    category: '📢 Admin',
    color: '#9B59B6',
    items: [
      { usage: '!embed <#channel> "Title" <desc>', desc: 'Post a branded embed to any channel', perm: 'Administrator' },
    ],
  },
  {
    category: 'ℹ️ General',
    color: '#95A5A6',
    items: [
      { usage: '!ping', desc: 'Check WebSocket and round-trip API latency' },
      { usage: '!uptime', desc: 'Display how long the bot has been running' },
      { usage: '!userinfo [user]', desc: 'Show detailed info about a user (default: yourself)' },
      { usage: '!serverinfo', desc: 'Show server statistics and configuration' },
      { usage: '!help', desc: 'Show the full command reference embed in Discord' },
    ],
  },
];

// ── Sub-components ─────────────────────────────────────────────────────────────

const StatusDot = ({ isOnline }: { isOnline?: boolean }) => (
  <div className="relative flex items-center justify-center w-3 h-3">
    {isOnline && <div className="absolute inset-0 bg-[#23A55A] rounded-full animate-ping opacity-75" />}
    <div className={`relative w-3 h-3 rounded-full ${isOnline ? 'bg-[#23A55A]' : 'bg-[#ED4245]'}`} />
  </div>
);

// ── Main dashboard ─────────────────────────────────────────────────────────────

export default function Dashboard() {
  // ── Queries ─────────────────────────────────────────────────────────────────
  const { data: health } = useHealthCheck({
    query: { refetchInterval: 10000, queryKey: getHealthCheckQueryKey() },
  });
  const { data: status } = useGetBotStatus({
    query: { refetchInterval: 5000, queryKey: getGetBotStatusQueryKey() },
  });
  const { data: logs } = useGetBotLogs(
    { limit: 100 },
    { query: { refetchInterval: 3000, queryKey: getGetBotLogsQueryKey({ limit: 100 }) } },
  );
  const { data: strikes } = useGetBotStrikes({
    query: { refetchInterval: 5000, queryKey: getGetBotStrikesQueryKey() },
  });
  const { data: tickets } = useGetBotTickets({
    query: { refetchInterval: 5000, queryKey: getGetBotTicketsQueryKey() },
  });

  // ── Derived stats ────────────────────────────────────────────────────────────
  const strikesMap = strikes?.strikes || {};
  let totalStrikes = 0, oneStrike = 0, twoStrikes = 0, threeStrikes = 0;
  Object.values(strikesMap).forEach((count) => {
    totalStrikes++;
    if (count === 1) oneStrike++;
    else if (count === 2) twoStrikes++;
    else if (count >= 3) threeStrikes++;
  });

  const ticketsMap = tickets?.tickets || {};
  let totalOpen = 0, totalClosed = 0;
  Object.values(ticketsMap).forEach((t) => {
    if (t.status === 'open') totalOpen++;
    else totalClosed++;
  });
  const totalTickets = Object.keys(ticketsMap).length;

  // ── Log viewer ───────────────────────────────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isAutoScroll, setIsAutoScroll] = useState(true);
  const [clearedAt, setClearedAt] = useState<number | null>(null);

  const visibleLogs = (logs?.entries || []).filter((log) =>
    clearedAt ? new Date(log.ts).getTime() > clearedAt : true,
  );

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const t = e.currentTarget;
    setIsAutoScroll(t.scrollHeight - t.scrollTop - t.clientHeight < 20);
  };

  useEffect(() => {
    if (isAutoScroll && scrollRef.current)
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [visibleLogs, isAutoScroll]);

  // ── Connection test ──────────────────────────────────────────────────────────
  const [testResult, setTestResult] = useState<{
    status: 'idle' | 'testing' | 'pass' | 'fail';
    latency?: number;
  }>({ status: 'idle' });

  const handleTestConnection = async () => {
    setTestResult({ status: 'testing' });
    const start = Date.now();
    try {
      await healthCheck();
      setTestResult({ status: 'pass', latency: Date.now() - start });
    } catch {
      setTestResult({ status: 'fail' });
    }
  };

  // ── Bottom tab state ─────────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<'logs' | 'commands'>('logs');
  const [cmdSearch, setCmdSearch] = useState('');

  const filteredCmds = COMMANDS.map((cat) => ({
    ...cat,
    items: cat.items.filter(
      (item) =>
        !cmdSearch ||
        item.usage.toLowerCase().includes(cmdSearch.toLowerCase()) ||
        item.desc.toLowerCase().includes(cmdSearch.toLowerCase()),
    ),
  })).filter((cat) => cat.items.length > 0);

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col min-h-[100dvh] md:h-[100dvh] md:overflow-hidden bg-background text-foreground p-3 md:p-4 gap-3 md:gap-4 selection:bg-[#5865F2]/30">

      {/* ── HEADER ── */}
      <header className="flex-none flex flex-wrap items-center justify-between bg-card border border-card-border p-4 rounded-lg shadow-sm gap-4">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-3 bg-secondary/50 px-3 py-1.5 rounded-md border border-border">
            <StatusDot isOnline={status?.online} />
            <span className={`text-sm font-bold tracking-widest uppercase ${status?.online ? 'text-[#23A55A]' : 'text-[#ED4245]'}`}>
              {status?.online ? 'System Online' : 'System Offline'}
            </span>
          </div>
          <h1 className="text-xl font-bold tracking-tight">{status?.bot_name || 'Bot_Instance'}</h1>
          <div className="h-4 w-px bg-border hidden sm:block" />
          <div className="text-sm text-muted-foreground font-mono hidden sm:block">
            ID: {status?.bot_id || '---'}
          </div>
        </div>
        <div className="flex items-center gap-8 text-sm">
          <div className="flex flex-col items-end">
            <span className="text-muted-foreground uppercase text-[10px] tracking-widest font-bold">API Status</span>
            <span className={`font-bold tracking-wider ${health ? 'text-[#23A55A]' : 'text-muted-foreground animate-pulse'}`}>
              {health ? 'OPERATIONAL' : 'WAITING'}
            </span>
          </div>
          <div className="flex flex-col items-end">
            <span className="text-muted-foreground uppercase text-[10px] tracking-widest font-bold">Last Updated</span>
            <span className="font-mono">
              {status?.last_updated ? new Date(status.last_updated).toLocaleTimeString() : '---'}
            </span>
          </div>
        </div>
      </header>

      {/* ── STATS GRID ── */}
      <div className="flex-none grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">

        {/* System */}
        <div className="bg-card border border-card-border rounded-lg p-4 flex flex-col justify-between shadow-sm min-h-[100px]">
          <div className="text-xs font-bold tracking-widest uppercase text-muted-foreground mb-4">System Resources</div>
          <div className="flex justify-between items-end">
            <div>
              <div className="text-[10px] text-muted-foreground/70 uppercase tracking-widest font-bold mb-1">Guilds</div>
              <div className="text-3xl font-bold leading-none">{status?.guild_count ?? '---'}</div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-muted-foreground/70 uppercase tracking-widest font-bold mb-1">Uptime</div>
              <div className="text-xl font-bold font-mono leading-none text-[#5865F2]">
                {status ? formatUptime(status.uptime_seconds) : '---'}
              </div>
            </div>
          </div>
        </div>

        {/* Strikes */}
        <div className="bg-card border border-card-border rounded-lg p-4 flex flex-col justify-between shadow-sm min-h-[100px]">
          <div className="text-xs font-bold tracking-widest uppercase text-muted-foreground mb-4">Moderation Strikes</div>
          <div className="flex justify-between items-end gap-2">
            <div>
              <div className="text-[10px] text-muted-foreground/70 uppercase tracking-widest font-bold mb-1">Total Users</div>
              <div className="text-3xl font-bold leading-none">{totalStrikes}</div>
            </div>
            <div className="flex gap-1.5">
              <div className="flex flex-col items-center bg-secondary/50 px-2 py-1 rounded border border-border">
                <span className="text-[10px] text-muted-foreground font-bold">1x</span>
                <span className="text-sm font-bold">{oneStrike}</span>
              </div>
              <div className="flex flex-col items-center bg-[#F0B132]/10 px-2 py-1 rounded border border-[#F0B132]/20">
                <span className="text-[10px] text-[#F0B132] font-bold">2x</span>
                <span className="text-sm font-bold text-[#F0B132]">{twoStrikes}</span>
              </div>
              <div className="flex flex-col items-center bg-[#ED4245]/10 px-2 py-1 rounded border border-[#ED4245]/20">
                <span className="text-[10px] text-[#ED4245] font-bold">3+</span>
                <span className="text-sm font-bold text-[#ED4245]">{threeStrikes}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Tickets */}
        <div className="bg-card border border-card-border rounded-lg p-4 flex flex-col justify-between shadow-sm min-h-[100px]">
          <div className="text-xs font-bold tracking-widest uppercase text-muted-foreground mb-4">Support Tickets</div>
          <div className="flex justify-between items-end gap-4">
            <div>
              <div className="text-[10px] text-muted-foreground/70 uppercase tracking-widest font-bold mb-1">Total</div>
              <div className="text-3xl font-bold leading-none">{totalTickets}</div>
            </div>
            <div className="flex gap-4">
              <div className="flex flex-col items-end">
                <span className="text-[10px] text-muted-foreground font-bold uppercase tracking-widest">Open</span>
                <span className="text-xl font-bold text-[#F0B132] leading-none mt-1">{totalOpen}</span>
              </div>
              <div className="flex flex-col items-end">
                <span className="text-[10px] text-muted-foreground font-bold uppercase tracking-widest">Closed</span>
                <span className="text-xl font-bold text-[#23A55A] leading-none mt-1">{totalClosed}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Diagnostics */}
        <div className="bg-card border border-card-border rounded-lg p-4 flex flex-col justify-between shadow-sm min-h-[100px]">
          <div className="text-xs font-bold tracking-widest uppercase text-muted-foreground mb-4">Diagnostics</div>
          <div className="flex items-end justify-between">
            <div className="flex flex-col">
              <span className="text-[10px] text-muted-foreground/70 font-bold uppercase tracking-widest mb-1">API Latency</span>
              {testResult.status === 'idle' && <span className="text-xl font-bold font-mono text-muted-foreground leading-none">---</span>}
              {testResult.status === 'testing' && <span className="text-xl font-bold font-mono text-[#F0B132] animate-pulse leading-none">Wait</span>}
              {testResult.status === 'pass' && <span className="text-xl font-bold font-mono text-[#23A55A] leading-none">{testResult.latency}ms</span>}
              {testResult.status === 'fail' && <span className="text-xl font-bold font-mono text-[#ED4245] leading-none">FAIL</span>}
            </div>
            <button
              onClick={handleTestConnection}
              disabled={testResult.status === 'testing'}
              className="bg-primary hover:bg-primary/90 text-primary-foreground px-4 py-2 rounded text-xs font-bold uppercase tracking-widest transition-colors disabled:opacity-50 cursor-pointer shadow-[0_0_15px_rgba(88,101,242,0.3)] hover:shadow-[0_0_20px_rgba(88,101,242,0.5)]"
            >
              Ping API
            </button>
          </div>
        </div>
      </div>

      {/* ── BOTTOM PANEL (tabbed) ── */}
      <div className="flex flex-col h-[520px] md:flex-1 md:min-h-0 bg-card border border-card-border rounded-lg overflow-hidden shadow-sm">

        {/* Tab bar */}
        <div className="flex-none flex items-center border-b border-card-border bg-secondary/30">
          {/* Tabs */}
          <div className="flex">
            {[
              { id: 'logs', label: 'Live Logs', dot: <span className="w-1.5 h-1.5 bg-[#F0B132] rounded-full animate-pulse shadow-[0_0_6px_rgba(240,177,50,0.8)]" /> },
              { id: 'commands', label: 'Commands', dot: null },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as 'logs' | 'commands')}
                className={`flex items-center gap-2 px-4 py-2.5 text-xs font-bold uppercase tracking-widest border-b-2 transition-colors ${
                  activeTab === tab.id
                    ? 'border-[#5865F2] text-[#5865F2]'
                    : 'border-transparent text-muted-foreground hover:text-foreground'
                }`}
              >
                {tab.dot}
                {tab.label}
                {tab.id === 'logs' && (
                  <span className="text-[10px] font-mono text-muted-foreground/60 normal-case tracking-normal">
                    {visibleLogs.length}
                  </span>
                )}
                {tab.id === 'commands' && (
                  <span className="text-[10px] font-mono text-muted-foreground/60 normal-case tracking-normal">
                    {COMMANDS.reduce((n, c) => n + c.items.length, 0)}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab-specific controls */}
          <div className="ml-auto flex items-center gap-1.5 px-2 md:px-4">
            {activeTab === 'logs' && (
              <>
                <button
                  onClick={() => setIsAutoScroll(!isAutoScroll)}
                  title={`Auto-scroll ${isAutoScroll ? 'ON' : 'OFF'}`}
                  className={`text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest border transition-colors whitespace-nowrap ${
                    isAutoScroll
                      ? 'bg-[#5865F2]/10 text-[#5865F2] border-[#5865F2]/30'
                      : 'bg-transparent text-muted-foreground border-border'
                  }`}
                >
                  <span className="hidden sm:inline">Auto-Scroll </span>{isAutoScroll ? 'ON' : 'OFF'}
                </button>
                <button
                  onClick={() => setClearedAt(Date.now())}
                  className="text-[10px] px-2 py-1 rounded font-bold uppercase tracking-widest text-muted-foreground border border-border hover:bg-secondary transition-colors"
                >
                  Clear
                </button>
              </>
            )}
            {activeTab === 'commands' && (
              <input
                type="search"
                placeholder="Search…"
                value={cmdSearch}
                onChange={(e) => setCmdSearch(e.target.value)}
                className="bg-secondary/50 border border-border rounded px-2 py-1 text-xs font-mono placeholder-muted-foreground/50 focus:outline-none focus:border-[#5865F2]/50 w-28 sm:w-48 transition-colors"
              />
            )}
          </div>
        </div>

        {/* ── Log viewer ── */}
        {activeTab === 'logs' && (
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="flex-1 overflow-y-auto p-4 font-mono text-sm leading-relaxed bg-[#09090b]/50"
          >
            {visibleLogs.length === 0 ? (
              <div className="flex items-center justify-center h-full text-muted-foreground italic opacity-50">
                Waiting for incoming logs...
              </div>
            ) : (
              <div className="flex flex-col">
                {visibleLogs.map((log, i) => (
                  <div
                    key={i}
                    className="flex gap-4 hover:bg-secondary/40 py-1 px-2 -mx-2 rounded transition-colors border-b border-border/20 last:border-0"
                  >
                    <div className="text-muted-foreground/50 shrink-0 w-[110px] tabular-nums select-none pt-0.5">
                      {log.ts.slice(11, 19)}
                    </div>
                    <div className="shrink-0 w-20 pt-0.5">
                      <span className={`px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded border flex justify-center w-full ${getBadgeClasses(log.level)}`}>
                        {log.level}
                      </span>
                    </div>
                    <div className="text-foreground/90 break-words whitespace-pre-wrap flex-1">{log.msg}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Commands reference ── */}
        {activeTab === 'commands' && (
          <div className="flex-1 overflow-y-auto p-4 bg-[#09090b]/50">
            {filteredCmds.length === 0 ? (
              <div className="flex items-center justify-center h-full text-muted-foreground italic opacity-50">
                No commands match "{cmdSearch}"
              </div>
            ) : (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                {filteredCmds.map((cat) => (
                  <div key={cat.category} className="bg-card/50 border border-card-border rounded-lg overflow-hidden">
                    {/* Category header */}
                    <div
                      className="px-4 py-2 flex items-center gap-2 border-b border-card-border"
                      style={{ borderLeftColor: cat.color, borderLeftWidth: 3 }}
                    >
                      <span className="text-xs font-bold uppercase tracking-widest" style={{ color: cat.color }}>
                        {cat.category}
                      </span>
                      <span className="text-[10px] text-muted-foreground/50 font-mono ml-auto">
                        {cat.items.length} command{cat.items.length !== 1 ? 's' : ''}
                      </span>
                    </div>
                    {/* Command rows */}
                    <div className="divide-y divide-border/30">
                      {cat.items.map((item) => (
                        <div key={item.usage} className="px-4 py-3 hover:bg-secondary/20 transition-colors group">
                          <div className="flex items-start justify-between gap-3 mb-1">
                            <code className="text-xs font-mono bg-secondary/60 text-[#5865F2] px-2 py-0.5 rounded border border-[#5865F2]/10 break-all leading-relaxed">
                              {item.usage}
                            </code>
                            {item.perm && (
                              <span className="shrink-0 text-[10px] font-bold uppercase tracking-wider text-muted-foreground/60 border border-border/40 rounded px-1.5 py-0.5 whitespace-nowrap">
                                {item.perm}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground leading-relaxed">{item.desc}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
