# Vyrion Discord Bot

AI-powered Discord bot with Roblox integration, moderation, subagent, and safety features.

## Key Features

- **AI Chat**: Multi-provider fallback (Gemini, Groq, OpenRouter, HuggingFace, Cerebras)
- **Strict Word Limits**: 40 words normal, 100 words code, 25 words for detected poems
- **Anti-Poem Protection**: System prompt + output detection prevents long creative writing
- **Anti-Copy**: AI cannot echo user input verbatim
- **Profanity Filter**: Output profanity censored with *** tags
- **Ping Prevention**: All @everyone, @here, user/role/channel pings stripped from AI output
- **Prompt Injection Defense**: AI ignores embedded instructions from non-owner users
- **Roblox Integration**: Live game/user/trending lookups
- **Moderation**: Strikes, mutes, bans, auto-mod, warnings
- **Subagent**: Bot-owner-only AI that performs Discord actions via natural language
- **Support Tickets**: DM-based ticket system with categories
- **PII/TOS Protection**: Filters PII and TOS-violating content
