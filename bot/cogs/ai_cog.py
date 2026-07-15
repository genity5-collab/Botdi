IMPORTANT: This file is very long (750+ lines). Here's what you do:

1. Keep your existing ai_cog.py
2. Only replace these specific sections:

--- SECTION A (around line 451): Profanity message ---
REPLACE THIS:
            embed = discord.Embed(
                title="⚠️ Watch Your Language",
                description="Please keep it respectful. A strike has been issued.",
                color=COLOR_ERR,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="Botdi Moderation")
            await message.reply(embed=embed, delete_after=15)

WITH THIS:
            await message.reply(
                "⚠️ **Watch Your Language**\nPlease keep it respectful. A strike has been issued.",
                delete_after=15
            )

--- SECTION B (around line 478): DM quota message ---
REPLACE THIS:
                embed = discord.Embed(
                    title="💬 Daily Limit Reached",
                    description=(
                        f"You've used all **{DM_DAILY_LIMIT}** of your daily DM messages.\n"
                        "Your quota resets at **midnight UTC**. See you then! 🌙\n\n"
                        "*Tip: you can mention me in the server anytime — no limits there!*"
                    ),
                    color=COLOR_WARN,
                    timestamp=discord.utils.utcnow(),
                )
                embed.set_footer(text="Botdi AI • Daily limit")
                await message.reply(embed=embed)

WITH THIS:
                await message.reply(
                    f"💬 **Daily Limit Reached**\n"
                    f"You've used all **{DM_DAILY_LIMIT}** of your daily DM messages.\n"
                    f"Your quota resets at **midnight UTC**. See you then! 🌙\n\n"
                    f"*Tip: you can mention me in the server anytime — no limits there!*"
                )

--- SECTION C (around line 613): MAIN AI RESPONSE (MOST IMPORTANT!) ---
REPLACE THIS:
        embed = discord.Embed(
            description=reply_text,
            color=BOT_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.set_footer(text=footer)
        await message.reply(embed=embed)

WITH THIS:
        # Send plain text response (allows longer messages)
        mention = f"**{user.display_name}:** " if not isinstance(message.channel, discord.DMChannel) else ""
        footer_text = f"\n\n*{footer}*"
        await message.reply(f"{mention}{reply_text}{footer_text}")

That's it! The rest stays the same.
