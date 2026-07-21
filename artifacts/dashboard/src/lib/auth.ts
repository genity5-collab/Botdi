export interface DiscordUser {
  id: string;
  username: string;
  global_name: string;
  avatar: string;
  discriminator: string;
}

export interface AuthState {
  token: string;
  user: DiscordUser;
  expires_at: number;
}

export interface ApiKeyConfig {
  mode: 'default' | 'custom';
  customKeys: {
    gemini?: string;
    groq?: string;
    openrouter?: string;
    cerebras?: string;
    huggingface?: string;
  };
  bonusMessages: number;
}

const STORAGE_KEY = 'vyrion_auth';
const API_KEY_STORAGE = 'vyrion_api_keys';
const BONUS_MESSAGES_KEY = 'vyrion_bonus_messages';
const SCOPES = 'identify';
const REDIRECT_URI = typeof window !== 'undefined' ? `${window.location.origin}${window.location.pathname}` : '';

export function getDiscordClientId(): string {
  return import.meta.env.VITE_DISCORD_CLIENT_ID || localStorage.getItem('vyrion_discord_client_id') || '';
}

export function setDiscordClientId(id: string): void {
  localStorage.setItem('vyrion_discord_client_id', id);
}

export function getAuth(): AuthState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data: AuthState = JSON.parse(raw);
    if (Date.now() > data.expires_at) {
      clearAuth();
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

export function setAuth(auth: AuthState): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
}

export function clearAuth(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function getLoginUrl(clientId: string): string {
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: REDIRECT_URI,
    response_type: 'token',
    scope: SCOPES,
  });
  return `https://discord.com/api/oauth2/authorize?${params.toString()}`;
}

export async function fetchDiscordUser(token: string): Promise<DiscordUser | null> {
  try {
    const res = await fetch('https://discord.com/api/users/@me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return {
      id: data.id,
      username: data.username,
      global_name: data.global_name || data.username,
      avatar: data.avatar
        ? `https://cdn.discordapp.com/avatars/${data.id}/${data.avatar}.png?size=128`
        : `https://cdn.discordapp.com/embed/avatars/${parseInt(data.discriminator || '0') % 5}.png`,
      discriminator: data.discriminator || '0',
    };
  } catch {
    return null;
  }
}

export function parseTokenFromHash(): { token: string; expiresIn: number } | null {
  const hash = window.location.hash.slice(1);
  if (!hash) return null;
  const params = new URLSearchParams(hash);
  const token = params.get('access_token');
  const expiresIn = parseInt(params.get('expires_in') || '0', 10);
  if (!token || !expiresIn) return null;
  window.history.replaceState(null, '', window.location.pathname);
  return { token, expiresIn };
}

// ── API Key Management ───────────────────────────────────────────────────────

export function getApiKeyConfig(): ApiKeyConfig {
  try {
    const raw = localStorage.getItem(API_KEY_STORAGE);
    if (!raw) return { mode: 'default', customKeys: {}, bonusMessages: 0 };
    return JSON.parse(raw);
  } catch {
    return { mode: 'default', customKeys: {}, bonusMessages: 0 };
  }
}

export function setApiKeyConfig(config: ApiKeyConfig): void {
  localStorage.setItem(API_KEY_STORAGE, JSON.stringify(config));
}

export function setCustomApiKeys(keys: Partial<ApiKeyConfig['customKeys']>): void {
  const config = getApiKeyConfig();
  config.customKeys = { ...config.customKeys, ...keys };
  config.mode = 'custom';
  // Award 5 bonus messages only when switching TO custom keys
  if (config.bonusMessages === 0) {
    config.bonusMessages = 5;
  }
  setApiKeyConfig(config);
}

export function switchToDefaultKeys(): void {
  const config = getApiKeyConfig();
  config.mode = 'default';
  // Switching back removes bonus messages
  config.bonusMessages = 0;
  setApiKeyConfig(config);
}

export function getBonusMessages(): number {
  const config = getApiKeyConfig();
  return config.bonusMessages || 0;
}

export function useBonusMessage(): boolean {
  const config = getApiKeyConfig();
  if (config.bonusMessages > 0) {
    config.bonusMessages -= 1;
    setApiKeyConfig(config);
    return true;
  }
  return false;
}
