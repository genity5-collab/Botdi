import React, { useState } from 'react';
import { getApiKeyConfig, setCustomApiKeys, switchToDefaultKeys, type ApiKeyConfig } from '@/lib/auth';

const PROVIDERS = [
  { key: 'gemini', label: 'Google Gemini', placeholder: 'AIza...', color: '#4285F4' },
  { key: 'groq', label: 'Groq', placeholder: 'gsk_...', color: '#F55036' },
  { key: 'openrouter', label: 'OpenRouter', placeholder: 'sk-or-...', color: '#6469FF' },
  { key: 'cerebras', label: 'Cerebras', placeholder: 'csk-...', color: '#E8553F' },
  { key: 'huggingface', label: 'HuggingFace', placeholder: 'hf_...', color: '#FFD21E' },
] as const;

export default function ApiKeySettings() {
  const [config, setConfig] = useState<ApiKeyConfig>(getApiKeyConfig());
  const [keys, setKeys] = useState(config.customKeys);
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setCustomApiKeys(keys);
    setConfig(getApiKeyConfig());
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleSwitchDefault = () => {
    switchToDefaultKeys();
    setConfig(getApiKeyConfig());
    setKeys({});
  };

  return (
    <div className="bg-card border border-card-border rounded-lg p-4 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-bold tracking-widest uppercase text-muted-foreground">API Keys</h2>
          <p className="text-xs text-muted-foreground/60 mt-1">
            Use default keys or bring your own. Using your own grants 5 bonus messages.
          </p>
        </div>
        <div className={`px-3 py-1.5 rounded-md text-xs font-bold uppercase tracking-widest border ${
          config.mode === 'custom'
            ? 'bg-[#23A55A]/10 text-[#23A55A] border-[#23A55A]/20'
            : 'bg-secondary/50 text-muted-foreground border-border'
        }`}>
          {config.mode === 'custom' ? 'Custom Keys' : 'Default Keys'}
        </div>
      </div>

      {/* Bonus messages indicator */}
      {config.bonusMessages > 0 && (
        <div className="mb-4 px-3 py-2 bg-[#F0B132]/10 border border-[#F0B132]/20 rounded-md flex items-center gap-2">
          <span className="text-sm">🎁</span>
          <span className="text-xs text-[#F0B132] font-bold">
            {config.bonusMessages} bonus messages available
          </span>
        </div>
      )}

      {/* Provider key inputs */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        {PROVIDERS.map((provider) => (
          <div key={provider.key} className="space-y-1">
            <label className="text-xs font-bold uppercase tracking-widest text-muted-foreground flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: provider.color }} />
              {provider.label}
            </label>
            <div className="flex gap-1">
              <input
                type={showKeys[provider.key] ? 'text' : 'password'}
                value={keys[provider.key as keyof typeof keys] || ''}
                onChange={(e) => setKeys({ ...keys, [provider.key]: e.target.value })}
                placeholder={provider.placeholder}
                className="flex-1 bg-secondary/50 border border-border rounded-md px-2.5 py-1.5 text-xs font-mono placeholder-muted-foreground/40 focus:outline-none focus:border-[#5865F2]/50 transition-colors min-w-0"
              />
              <button
                onClick={() => setShowKeys({ ...showKeys, [provider.key]: !showKeys[provider.key] })}
                className="px-2 py-1.5 bg-secondary/50 border border-border rounded-md text-xs text-muted-foreground hover:text-foreground transition-colors flex-none"
                title={showKeys[provider.key] ? 'Hide' : 'Show'}
              >
                {showKeys[provider.key] ? '🙈' : '👁'}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Action buttons */}
      <div className="flex flex-col sm:flex-row gap-2">
        <button
          onClick={handleSave}
          className="flex-1 bg-[#5865F2] hover:bg-[#5865F2]/90 text-white px-4 py-2 rounded-md text-xs font-bold uppercase tracking-widest transition-all cursor-pointer shadow-[0_0_15px_rgba(88,101,242,0.3)] hover:shadow-[0_0_20px_rgba(88,101,242,0.5)]"
        >
          {saved ? '✓ Saved!' : 'Save & Use My Keys'}
        </button>
        {config.mode === 'custom' && (
          <button
            onClick={handleSwitchDefault}
            className="flex-1 sm:flex-none bg-secondary/50 hover:bg-secondary border border-border text-foreground px-4 py-2 rounded-md text-xs font-bold uppercase tracking-widest transition-colors cursor-pointer"
          >
            Switch to Default
          </button>
        )}
      </div>

      <p className="text-[11px] text-muted-foreground/50 mt-3">
        Keys are stored in your browser's local storage only. They are never sent to any server.
        Switching back to default keys removes any remaining bonus messages.
      </p>
    </div>
  );
}
