"""
Support Cog — slash command ticketing with live status tracking.

Commands:
  /ticket                     — Open a support ticket (DM menu)
  /reply <ticket_id> <msg>    — Staff reply to ticket user (DM)
  /close <ticket_id>          — Close a ticket
  /tickets                    — List open tickets (staff)

Ticket flow:
  1. User runs /ticket → gets DM with category buttons
  2. User picks category → ticket created, admin channel notified
  3. User sends messages in DM → forwarded to admin channel
  4. Staff uses /reply → DM sent to user
  5. Staff uses /close → ticket closed, user notified

Live status: each ticket tracks "waiting for user" vs "waiting for staff"
"""
from __future__ import annotations

import discord
from discord.ext import commands

from config import ADMIN_CHANNEL_ID, SUPPORT_LINK, BOT_COLOR, COLOR_OK, COLOR_ERR, COLOR_INFO
from data_store import (
    create_ticket,
    get_ticket,
    close_ticket,
    get_user_open_ticket,
)
from utils import log_action

CATEGORIES = ["Exploiter", "Bug", "Strike Report", "Other"]


class SupportView(discord.ui.View):
    """Sent in DMs to let users choose a ticket category."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def _handle_category(
        self, interaction: discord.Interaction, category: str
    ) -> None:
        user = interaction.user

        existing = await get_user_open_ticket(user.id)
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket (`#{existing}`). "
                "Continue describing your issue in this DM channel.",
                ephemeral=True,
            )
            return

        tid = await create_ticket(user.id, category)

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"🎫 Ticket #{tid} — {category}",
                description=(
                    "Your ticket has been opened!\n\n"
                    "**Next step:** Describe your issue in your next message. "
                    "Staff will respond as soon as possible.\n\n"
                    f"To appeal a moderation action: [Appeal Form]({SUPPORT_LINK})\n\n"
                    f"Your ticket ID is **#{tid}** — keep it for reference."
                ),
                color=COLOR_OK,
            )
        )

        bot_instance = interaction.client
        admin_channel = bot_instance.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(
                title=f"📥 New Ticket #{tid}",
                description=(
                    f"**Category:** {category}\n"
                    f"**User:** {user.mention} (`{user.id}`)\n"
                    f"**Username:** `{user}`\n\n"
                    f"**Status:** 🟡 Waiting for user message\n\n"
                    f"Reply: `/reply {tid} <message>`\n"
                    f"Close: `/close {tid}`"
                ),
                color=COLOR_INFO,
                timestamp=discord.utils.utcnow(),
            )
            await admin_channel.send(embed=embed)

        await log_action(
            bot_instance,
            "🎫 Ticket Opened",
            f"**Ticket:** #{tid}\n**Category:** {category}\n**User:** {user.mention} (`{user.id}`)",
            color=0x2ECC71,
        )

    @discord.ui.button(label="⚔️ Exploiter", style=discord.ButtonStyle.danger, custom_id="support:exploiter")
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


class Support(commands.Cog, name="Support"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is not None:
            return
        if message.content.startswith("!") or message.content.startswith("/"):
            return

        user = message.author
        existing_tid = await get_user_open_ticket(user.id)
        if existing_tid is None:
            return

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
        embed.set_footer(text=f"User ID: {user.id} | /reply {existing_tid} <msg> | /close {existing_tid}")
        embed.add_field(name="Status", value="🟢 Waiting for staff reply", inline=False)

        if message.attachments:
            attach_links = "\n".join(a.url for a in message.attachments)
            embed.add_field(name="📎 Attachments", value=attach_links[:1024], inline=False)

        await admin_channel.send(embed=embed)
        await message.add_reaction("✅")

    @commands.hybrid_command(name="ticket", description="Open a support ticket")
    async def ticket_cmd(self, ctx: commands.Context) -> None:
        existing = await get_user_open_ticket(ctx.author.id)
        if existing:
            await ctx.send(
                embed=discord.Embed(
                    title="🎫 Ticket Already Open",
                    description=(
                        f"You already have an open ticket (`#{existing}`).\n"
                        "Continue describing your issue and staff will reply soon."
                    ),
                    color=COLOR_INFO,
                ),
                delete_after=15,
            )
            return

        try:
            dm = await ctx.author.create_dm()
            await dm.send(
                embed=discord.Embed(
                    title="🎫 Support Center",
                    description=(
                        "Select the category that best describes your issue.\n\n"
                        "• **Exploiter** — Report someone exploiting/abusing\n"
                        "• **Bug Report** — Report a Botdi bug or glitch\n"
                        "• **Strike Report** — Appeal or report a strike issue\n"
                        "• **Other** — Anything else"
                    ),
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
                    description="❌ I couldn't DM you. Please enable DMs from server members in your privacy settings.",
                    color=COLOR_ERR,
                ),
                delete_after=12,
            )

    @commands.hybrid_command(name="reply", description="Reply to a support ticket (staff only)")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(ticket_id="Ticket ID", message="Your reply to the user")
    async def reply_cmd(self, ctx: commands.Context, ticket_id: str, *, message: str) -> None:
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
                description=message[:2000],
                color=BOT_COLOR,
            )
            embed.set_footer(text=f"Staff reply • Category: {ticket['category']} • Reply in this DM to continue")
            await dm.send(embed=embed)
        except discord.HTTPException:
            await ctx.send("❌ Could not DM user — they may have DMs disabled.", delete_after=10)
            return

        # Update admin channel with staff reply
        admin_channel = self.bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(
                title=f"💬 Ticket #{ticket_id.upper()} — Staff Reply",
                description=message[:2000],
                color=COLOR_OK,
            )
            embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
            embed.set_footer(text=f"Staff: {ctx.author} | Status: 🟡 Waiting for user")
            await admin_channel.send(embed=embed)

        await log_action(
            self.bot, "📩 Staff Reply Sent",
            f"**Ticket:** #{ticket_id.upper()}\n**Staff:** {ctx.author.mention}\n**Message:** {message[:500]}",
            color=0x3498DB,
        )
        await ctx.send(f"✅ Reply sent to ticket `#{ticket_id.upper()}`.", delete_after=10)

    @commands.hybrid_command(name="close", description="Close a support ticket (staff only)")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(ticket_id="Ticket ID to close")
    async def close_cmd(self, ctx: commands.Context, ticket_id: str) -> None:
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
                    "Your support ticket has been closed by staff.\n\n"
                    "If you need further help, open a new ticket with `/ticket`.\n\n"
                    f"We'd love your feedback! How was your support experience?"
                ),
                color=COLOR_INFO,
            )
            await dm.send(embed=embed)
        except discord.HTTPException:
            pass

        await log_action(
            self.bot, "🔒 Ticket Closed",
            f"**Ticket:** #{ticket_id.upper()}\n**Staff:** {ctx.author.mention}",
            color=0x95A5A6,
        )
        await ctx.send(f"✅ Ticket `#{ticket_id.upper()}` has been closed.", delete_after=15)

    @commands.hybrid_command(name="tickets", description="List all open tickets (staff only)")
    @commands.has_permissions(moderate_members=True)
    async def tickets_cmd(self, ctx: commands.Context) -> None:
        """List all open tickets."""
        from data_store import _tickets
        open_tickets = [(tid, t) for tid, t in _tickets.items() if t["status"] == "open"]
        if not open_tickets:
            await ctx.send(embed=discord.Embed(description="No open tickets.", color=COLOR_INFO))
            return
        lines = []
        for tid, t in open_tickets[:15]:
            try:
                user = await self.bot.fetch_user(t["user_id"])
                name = str(user)
            except Exception:
                name = f"User {t['user_id']}"
            lines.append(f"`#{tid}` — {t['category']} — {name}")
        embed = discord.Embed(
            title=f"🎫 Open Tickets ({len(open_tickets)})",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        await ctx.send(embed=embed)

    @reply_cmd.error
    @close_cmd.error
    @tickets_cmd.error
    async def support_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have permission to use this command.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`.", delete_after=10)
        else:
            await ctx.send(f"❌ Error: {error}", delete_after=10)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Support(bot))
