"""
Support Cog
───────────
• DM → persistent category menu (Exploiter / Bug / Strike Report / Other)
• Forwards messages to ADMIN_CHANNEL_ID with a ticket ID
• !reply <ticket_id> <msg>  — staff replies to user via DM
• !close <ticket_id>        — closes ticket and notifies user
"""

from __future__ import annotations

import discord
from discord.ext import commands

from config import ADMIN_CHANNEL_ID, SUPPORT_LINK, BOT_COLOR
from data_store import (
    create_ticket,
    get_ticket,
    close_ticket,
    get_user_open_ticket,
)
from utils import log_action

CATEGORIES = ["Exploiter", "Bug", "Strike Report", "Other"]


# ── Persistent support menu ────────────────────────────────────────────────────

class SupportView(discord.ui.View):
    """Sent in DMs to let users choose a ticket category."""

    def __init__(self) -> None:
        super().__init__(timeout=None)   # Persistent across restarts

    async def _handle_category(
        self, interaction: discord.Interaction, category: str
    ) -> None:
        user = interaction.user

        # Check for existing open ticket
        existing = await get_user_open_ticket(user.id)
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket (`#{existing}`). "
                "Please continue describing your issue in this DM channel.",
                ephemeral=True,
            )
            return

        tid = await create_ticket(user.id, category)

        # Acknowledge to user
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"🎫 Ticket #{tid} — {category}",
                description=(
                    "Your ticket has been opened. **Please describe your issue** "
                    "in your next message and staff will respond shortly.\n\n"
                    f"To appeal a moderation action, use: [Appeal Form]({SUPPORT_LINK})"
                ),
                color=BOT_COLOR,
            )
        )

        # Notify admin channel
        bot_instance = interaction.client
        admin_channel = bot_instance.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(
                title=f"📥 New Support Ticket #{tid}",
                description=(
                    f"**Category:** {category}\n"
                    f"**User:** {user.mention} (`{user.id}`)\n"
                    f"**Tag:** `{user}`\n\n"
                    f"Use `!reply {tid} <message>` to respond.\n"
                    f"Use `!close {tid}` to close the ticket."
                ),
                color=BOT_COLOR,
            )
            await admin_channel.send(embed=embed)

        await log_action(
            bot_instance,
            "🎫 Ticket Opened",
            f"**Ticket:** #{tid}\n**Category:** {category}\n**User:** {user.mention} (`{user.id}`)",
            color=0x2ECC71,
        )

    @discord.ui.button(label="⚔️ Exploiter", style=discord.ButtonStyle.danger,  custom_id="support:exploiter")
    async def exploiter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_category(interaction, "Exploiter")

    @discord.ui.button(label="🐛 Bug Report", style=discord.ButtonStyle.primary, custom_id="support:bug")
    async def bug(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_category(interaction, "Bug")

    @discord.ui.button(label="⚠️ Strike Report", style=discord.ButtonStyle.secondary, custom_id="support:strike")
    async def strike_report(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_category(interaction, "Strike Report")

    @discord.ui.button(label="❓ Other", style=discord.ButtonStyle.secondary, custom_id="support:other")
    async def other(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_category(interaction, "Other")


# ── Cog ───────────────────────────────────────────────────────────────────────

class Support(commands.Cog, name="Support"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── DM listener — only forwards messages for active tickets ───────────────
    # The support menu is NOT auto-sent on every DM; use !ticket to open one.

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Only handle DMs from real users
        if message.author.bot or message.guild is not None:
            return
        # Ignore bot commands — let the command handler deal with them
        if message.content.startswith("!"):
            return

        user = message.author
        existing_tid = await get_user_open_ticket(user.id)

        # No open ticket → let the AI cog handle the conversation, do nothing here
        if existing_tid is None:
            return

        # Active ticket → forward message to admin channel
        admin_channel = self.bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel is None:
            await message.channel.send("⚠️ Support channel not found. Please try again later.")
            return

        content = message.content or "(no text)"
        embed = discord.Embed(
            title=f"💬 Ticket #{existing_tid} — New Message",
            description=content[:2000],
            color=0x95A5A6,
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.set_footer(text=f"User ID: {user.id} | !reply {existing_tid} <msg> | !close {existing_tid}")

        if message.attachments:
            attach_links = "\n".join(a.url for a in message.attachments)
            embed.add_field(name="📎 Attachments", value=attach_links[:1024], inline=False)

        await admin_channel.send(embed=embed)
        await message.add_reaction("✅")

    # ── !ticket — open a support ticket from anywhere ─────────────────────────

    @commands.command(name="ticket")
    async def ticket_cmd(self, ctx: commands.Context) -> None:
        """Open a support ticket. Works in DMs or in the server."""
        existing = await get_user_open_ticket(ctx.author.id)
        if existing:
            await ctx.send(
                embed=discord.Embed(
                    title="🎫 Ticket Already Open",
                    description=(
                        f"You already have an open ticket (`#{existing}`).\n"
                        "Continue describing your issue and staff will reply soon."
                    ),
                    color=BOT_COLOR,
                ),
                delete_after=15,
            )
            return

        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                embed=discord.Embed(
                    title="🎫 Support Center",
                    description="Select the category that best describes your issue.",
                    color=BOT_COLOR,
                ),
                view=SupportView(),
            )
            if ctx.guild:
                await ctx.send(
                    embed=discord.Embed(
                        description="📬 Check your DMs — I've sent you the support menu!",
                        color=BOT_COLOR,
                    ),
                    delete_after=10,
                )
        except discord.Forbidden:
            await ctx.send(
                embed=discord.Embed(
                    description="❌ I couldn't DM you. Please enable DMs from server members.",
                    color=0xED4245,
                ),
                delete_after=12,
            )

    # ── !reply ────────────────────────────────────────────────────────────────

    @commands.command(name="reply")
    @commands.has_permissions(moderate_members=True)
    async def reply_cmd(self, ctx: commands.Context, ticket_id: str, *, msg: str) -> None:
        """!reply <ticket_id> <message> — Send a DM reply to the ticket user."""
        ticket = await get_ticket(ticket_id.upper())
        if ticket is None:
            await ctx.send(f"❌ Ticket `{ticket_id.upper()}` not found.", delete_after=10)
            return
        if ticket["status"] == "closed":
            await ctx.send(f"❌ Ticket `{ticket_id.upper()}` is already closed.", delete_after=10)
            return

        try:
            user = await self.bot.fetch_user(ticket["user_id"])
            dm = await user.create_dm()
            embed = discord.Embed(
                title=f"📩 Reply to Ticket #{ticket_id.upper()}",
                description=msg,
                color=BOT_COLOR,
            )
            embed.set_footer(text=f"Staff reply • Category: {ticket['category']}")
            await dm.send(embed=embed)
        except discord.HTTPException as e:
            await ctx.send(f"❌ Could not DM user: {e}", delete_after=10)
            return

        await log_action(
            self.bot,
            "📩 Staff Reply Sent",
            f"**Ticket:** #{ticket_id.upper()}\n"
            f"**Staff:** {ctx.author.mention}\n"
            f"**Message:** {msg[:500]}",
            color=0x3498DB,
        )
        await ctx.send(f"✅ Reply sent to ticket `#{ticket_id.upper()}`.", delete_after=10)

    # ── !close ────────────────────────────────────────────────────────────────

    @commands.command(name="close")
    @commands.has_permissions(moderate_members=True)
    async def close_cmd(self, ctx: commands.Context, ticket_id: str) -> None:
        """!close <ticket_id> — Close the ticket and notify the user."""
        success = await close_ticket(ticket_id.upper())
        if not success:
            await ctx.send(f"❌ Ticket `{ticket_id.upper()}` not found or already closed.", delete_after=10)
            return

        ticket = await get_ticket(ticket_id.upper())
        try:
            user = await self.bot.fetch_user(ticket["user_id"])
            dm = await user.create_dm()
            embed = discord.Embed(
                title=f"🔒 Ticket #{ticket_id.upper()} Closed",
                description=(
                    "Your support ticket has been resolved and closed.\n"
                    f"If you need further help, feel free to open a new ticket.\n"
                    f"[Appeal Form]({SUPPORT_LINK})"
                ),
                color=0x95A5A6,
            )
            await dm.send(embed=embed)
        except discord.HTTPException:
            pass

        await log_action(
            self.bot,
            "🔒 Ticket Closed",
            f"**Ticket:** #{ticket_id.upper()}\n**Staff:** {ctx.author.mention}",
            color=0x95A5A6,
        )
        await ctx.send(f"✅ Ticket `#{ticket_id.upper()}` has been closed.", delete_after=15)

    # ── Error handlers ────────────────────────────────────────────────────────

    @reply_cmd.error
    @close_cmd.error
    async def support_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`.", delete_after=10)
        else:
            await ctx.send(f"❌ Error: {error}", delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Support(bot))
