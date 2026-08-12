"""
Support Cog — slash command ticketing with live status tracking, auto-close, and staff buttons.

Commands:
  /ticket                     — Open a support ticket (DM menu)
  /reply <ticket_id> <msg>    — Staff reply to ticket user (DM)
  /close <ticket_id>          — Close a ticket
  /decline <ticket_id>        — Decline a ticket with reason
  /tickets                    — List open tickets (staff)

Auto-close: Tickets auto-close after 12 hours of inactivity (checked on every ticket access).
Staff can reply/close/decline via slash commands or button interactions.

Ticket flow:
  1. User runs /ticket → gets DM with category buttons
  2. User picks category → ticket created, admin channel notified with staff buttons
  3. User sends messages in DM → forwarded to admin channel (AI does NOT respond)
  4. Staff uses /reply or button → DM sent to user
  5. Staff uses /close or /decline → ticket closed, user notified
  6. After 12h of inactivity → auto-closed, user notified
"""
from __future__ import annotations

import datetime
import logging
import discord
from discord.ext import commands, tasks

from config import (
    ADMIN_CHANNEL_ID, SUPPORT_LINK, BOT_COLOR, COLOR_OK, COLOR_ERR, COLOR_INFO,
    TICKET_AUTO_CLOSE_HOURS,
)
from data_store import (
    create_ticket,
    get_ticket,
    close_ticket,
    get_user_open_ticket,
    _tickets,
    _save,
    TICKETS_FILE,
)
from utils import log_action

log = logging.getLogger("support")

CATEGORIES = ["Exploiter", "Bug", "Strike Report", "Other"]


def _check_auto_close(ticket: dict) -> bool:
    """Check if a ticket should be auto-closed (12h inactivity). Returns True if closed."""
    if ticket["status"] != "open":
        return False
    # Track last_activity in ticket
    last_activity = ticket.get("last_activity")
    if not last_activity:
        return False
    try:
        last_dt = datetime.datetime.fromisoformat(last_activity)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
        elapsed = (datetime.datetime.now(datetime.timezone.utc) - last_dt).total_seconds()
        if elapsed >= TICKET_AUTO_CLOSE_HOURS * 3600:
            return True
    except (ValueError, TypeError):
        pass
    return False


def _update_ticket_activity(ticket_id: str) -> None:
    """Update the last_activity timestamp on a ticket."""
    tid = ticket_id.upper()
    if tid in _tickets:
        _tickets[tid]["last_activity"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _save(TICKETS_FILE, _tickets)


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
                "Continue describing your issue in this DM channel. Staff will reply soon.",
                ephemeral=True,
            )
            return

        tid = await create_ticket(user.id, category)
        _update_ticket_activity(tid)

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"🎫 Ticket #{tid} — {category}",
                description=(
                    "Your ticket has been opened!\n\n"
                    "**Next step:** Describe your issue in your next message. "
                    "Staff will respond as soon as possible.\n\n"
                    f"⏰ This ticket will **auto-close in {TICKET_AUTO_CLOSE_HOURS} hours** if there's no activity.\n\n"
                    f"To appeal a moderation action: [Appeal Form]({SUPPORT_LINK})\n\n"
                    f"Your ticket ID is **#{tid}** — keep it for reference."
                ),
                color=COLOR_OK,
            )
        )

        bot_instance = interaction.client
        admin_channel = bot_instance.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            staff_view = StaffTicketView(tid)
            embed = discord.Embed(
                title=f"📥 New Ticket #{tid}",
                description=(
                    f"**Category:** {category}\n"
                    f"**User:** {user.mention} (`{user.id}`)\n"
                    f"**Username:** `{user}`\n\n"
                    f"**Status:** 🟡 Waiting for user message\n\n"
                    f"Use the buttons below or:\n"
                    f"`/reply {tid} <message>`\n"
                    f"`/close {tid}` or `/decline {tid} <reason>`"
                ),
                color=COLOR_INFO,
                timestamp=discord.utils.utcnow(),
            )
            await admin_channel.send(embed=embed, view=staff_view)

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


class StaffTicketView(discord.ui.View):
    """Buttons on the admin channel ticket message for quick staff actions."""

    def __init__(self, ticket_id: str) -> None:
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @discord.ui.button(label="Reply", style=discord.ButtonStyle.success, emoji="💬", custom_id="staff:reply")
    async def reply_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        ticket = await get_ticket(self.ticket_id)
        if not ticket or ticket["status"] == "closed":
            await interaction.response.send_message("❌ This ticket is closed.", ephemeral=True)
            return
        await interaction.response.send_modal(ReplyModal(self.ticket_id))

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="staff:close")
    async def close_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        ticket = await get_ticket(self.ticket_id)
        if not ticket or ticket["status"] == "closed":
            await interaction.response.send_message("❌ This ticket is already closed.", ephemeral=True)
            return
        success = await close_ticket(self.ticket_id)
        if not success:
            await interaction.response.send_message("❌ Failed to close ticket.", ephemeral=True)
            return
        # Notify user
        try:
            user = await interaction.client.fetch_user(ticket["user_id"])
            dm = await user.create_dm()
            await dm.send(embed=discord.Embed(
                title=f"🔒 Ticket #{self.ticket_id} Closed",
                description="Your support ticket has been closed by staff.\n\nIf you need further help, open a new ticket with `/ticket`.",
                color=COLOR_INFO,
            ))
        except discord.HTTPException:
            pass
        _update_ticket_activity(self.ticket_id)
        await interaction.response.send_message(f"✅ Ticket `#{self.ticket_id}` closed by {interaction.user.mention}.", ephemeral=False)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✋", custom_id="staff:decline")
    async def decline_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        ticket = await get_ticket(self.ticket_id)
        if not ticket or ticket["status"] == "closed":
            await interaction.response.send_message("❌ This ticket is closed.", ephemeral=True)
            return
        await interaction.response.send_modal(DeclineModal(self.ticket_id))


class ReplyModal(discord.ui.Modal, title="Reply to Ticket"):
    def __init__(self, ticket_id: str) -> None:
        super().__init__()
        self.ticket_id = ticket_id
        self.message_input = discord.ui.TextInput(
            label="Your reply",
            style=discord.TextStyle.paragraph,
            placeholder="Type your reply to the user...",
            max_length=2000,
            required=True,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ticket = await get_ticket(self.ticket_id)
        if not ticket or ticket["status"] == "closed":
            await interaction.response.send_message("❌ Ticket not found or closed.", ephemeral=True)
            return
        msg = self.message_input.value
        try:
            user = await interaction.client.fetch_user(ticket["user_id"])
            dm = await user.create_dm()
            embed = discord.Embed(
                title=f"📩 Reply to Ticket #{self.ticket_id}",
                description=msg[:2000],
                color=BOT_COLOR,
            )
            embed.set_footer(text=f"Staff reply • Category: {ticket['category']} • Reply in this DM to continue")
            await dm.send(embed=embed)
        except discord.HTTPException:
            await interaction.response.send_message("❌ Could not DM user.", ephemeral=True)
            return
        _update_ticket_activity(self.ticket_id)
        # Post in admin channel
        admin_channel = interaction.client.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(
                title=f"💬 Ticket #{self.ticket_id} — Staff Reply",
                description=msg[:2000],
                color=COLOR_OK,
            )
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"Status: 🟡 Waiting for user")
            await admin_channel.send(embed=embed)
        await interaction.response.send_message(f"✅ Reply sent to ticket `#{self.ticket_id}`.", ephemeral=True)


class DeclineModal(discord.ui.Modal, title="Decline Ticket"):
    def __init__(self, ticket_id: str) -> None:
        super().__init__()
        self.ticket_id = ticket_id
        self.reason_input = discord.ui.TextInput(
            label="Reason for declining",
            style=discord.TextStyle.paragraph,
            placeholder="Why is this ticket being declined?",
            max_length=500,
            required=True,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ticket = await get_ticket(self.ticket_id)
        if not ticket or ticket["status"] == "closed":
            await interaction.response.send_message("❌ Ticket not found or closed.", ephemeral=True)
            return
        reason = self.reason_input.value
        await close_ticket(self.ticket_id)
        # Notify user
        try:
            user = await interaction.client.fetch_user(ticket["user_id"])
            dm = await user.create_dm()
            await dm.send(embed=discord.Embed(
                title=f"✋ Ticket #{self.ticket_id} Declined",
                description=f"Your ticket was declined by staff.\n\n**Reason:** {reason}\n\nIf you believe this was an error, you can open a new ticket with `/ticket`.",
                color=COLOR_ERR,
            ))
        except discord.HTTPException:
            pass
        await log_action(
            interaction.client, "✋ Ticket Declined",
            f"**Ticket:** #{self.ticket_id}\n**Staff:** {interaction.user.mention}\n**Reason:** {reason}",
            color=COLOR_ERR,
        )
        await interaction.response.send_message(f"✋ Ticket `#{self.ticket_id}` declined by {interaction.user.mention}.", ephemeral=False)


class Support(commands.Cog, name="Support"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._auto_close_loop.start()

    def __del__(self) -> None:
        self._auto_close_loop.cancel()

    @tasks.loop(minutes=30)
    async def _auto_close_loop(self) -> None:
        """Auto-close tickets after 12 hours of inactivity."""
        closed = []
        for tid, ticket in list(_tickets.items()):
            if ticket["status"] != "open":
                continue
            if _check_auto_close(ticket):
                await close_ticket(tid)
                # Try to notify user
                try:
                    user = await self.bot.fetch_user(ticket["user_id"])
                    dm = await user.create_dm()
                    await dm.send(embed=discord.Embed(
                        title=f"⏰ Ticket #{tid} Auto-Closed",
                        description=(
                            f"Your ticket was auto-closed after {TICKET_AUTO_CLOSE_HOURS} hours of inactivity.\n\n"
                            "If you still need help, open a new ticket with `/ticket`."
                        ),
                        color=COLOR_INFO,
                    ))
                except (discord.HTTPException, discord.NotFound):
                    pass
                closed.append(tid)
        if closed:
            log.info("Auto-closed %d stale tickets: %s", len(closed), ", ".join(closed))
            admin_channel = self.bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_channel:
                await admin_channel.send(embed=discord.Embed(
                    title="⏰ Auto-Closed Tickets",
                    description=f"{len(closed)} ticket(s) auto-closed after {TICKET_AUTO_CLOSE_HOURS}h of inactivity:\n" + "\n".join(f"• `#{tid}`" for tid in closed),
                    color=COLOR_INFO,
                ))

    @_auto_close_loop.before_loop
    async def _before_auto_close(self) -> None:
        await self.bot.wait_until_ready()

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

        # Check auto-close
        ticket = await get_ticket(existing_tid)
        if ticket and _check_auto_close(ticket):
            await close_ticket(existing_tid)
            await message.reply(f"⏰ Your ticket #{existing_tid} was auto-closed after {TICKET_AUTO_CLOSE_HOURS}h of inactivity. Open a new one with `/ticket`.")
            return

        _update_ticket_activity(existing_tid)

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
        embed.set_footer(text=f"User ID: {user.id} | /reply {existing_tid} <msg> | /close {existing_tid} | /decline {existing_tid}")
        embed.add_field(name="Status", value="🟢 Waiting for staff reply", inline=False)

        if message.attachments:
            attach_links = "\n".join(a.url for a in message.attachments)
            embed.add_field(name="📎 Attachments", value=attach_links[:1024], inline=False)

        # Include staff buttons
        staff_view = StaffTicketView(existing_tid)
        await admin_channel.send(embed=embed, view=staff_view)
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
                        "Continue describing your issue and staff will reply soon.\n\n"
                        f"⏰ Auto-closes after {TICKET_AUTO_CLOSE_HOURS}h of inactivity."
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
                    title="🎫 Vyrion Support Center",
                    description=(
                        "Select the category that best describes your issue.\n\n"
                        "• **⚔️ Exploiter** — Report someone exploiting/abusing\n"
                        "• **🐛 Bug Report** — Report a Vyrion bug or glitch\n"
                        "• **⚠️ Strike Report** — Appeal or report a strike issue\n"
                        "• **❓ Other** — Anything else\n\n"
                        f"⏰ Tickets auto-close after {TICKET_AUTO_CLOSE_HOURS} hours of inactivity."
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

        _update_ticket_activity(ticket_id)
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
                    "If you need further help, open a new ticket with `/ticket`."
                ),
                color=COLOR_INFO,
            )
            await dm.send(embed=embed)
        except discord.HTTPException:
            pass

        _update_ticket_activity(ticket_id)
        await log_action(
            self.bot, "🔒 Ticket Closed",
            f"**Ticket:** #{ticket_id.upper()}\n**Staff:** {ctx.author.mention}",
            color=0x95A5A6,
        )
        await ctx.send(f"✅ Ticket `#{ticket_id.upper()}` has been closed.", delete_after=15)

    @commands.hybrid_command(name="decline", description="Decline a support ticket with a reason (staff only)")
    @commands.has_permissions(moderate_members=True)
    @discord.app_commands.describe(ticket_id="Ticket ID", reason="Reason for declining")
    async def decline_cmd(self, ctx: commands.Context, ticket_id: str, *, reason: str = "No reason provided") -> None:
        ticket = await get_ticket(ticket_id.upper())
        if ticket is None:
            await ctx.send(f"❌ Ticket `{ticket_id.upper()}` not found.", delete_after=10)
            return
        if ticket["status"] == "closed":
            await ctx.send(f"❌ Ticket `{ticket_id.upper()}` is already closed.", delete_after=10)
            return

        await close_ticket(ticket_id.upper())
        try:
            user = await self.bot.fetch_user(ticket["user_id"])
            dm = await user.create_dm()
            await dm.send(embed=discord.Embed(
                title=f"✋ Ticket #{ticket_id.upper()} Declined",
                description=f"Your ticket was declined by staff.\n\n**Reason:** {reason}\n\nIf you believe this was an error, open a new ticket with `/ticket`.",
                color=COLOR_ERR,
            ))
        except discord.HTTPException:
            pass

        _update_ticket_activity(ticket_id)
        await log_action(
            self.bot, "✋ Ticket Declined",
            f"**Ticket:** #{ticket_id.upper()}\n**Staff:** {ctx.author.mention}\n**Reason:** {reason}",
            color=COLOR_ERR,
        )
        await ctx.send(f"✋ Ticket `#{ticket_id.upper()}` has been declined.", delete_after=15)

    @commands.hybrid_command(name="tickets", description="List all open tickets (staff only)")
    @commands.has_permissions(moderate_members=True)
    async def tickets_cmd(self, ctx: commands.Context) -> None:
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
            # Check if auto-close approaching
            last_activity = t.get("last_activity", "")
            auto_close_warn = ""
            if last_activity:
                try:
                    last_dt = datetime.datetime.fromisoformat(last_activity)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
                    elapsed_h = (datetime.datetime.now(datetime.timezone.utc) - last_dt).total_seconds() / 3600
                    remaining_h = TICKET_AUTO_CLOSE_HOURS - elapsed_h
                    if remaining_h < 2:
                        auto_close_warn = " ⚠️ auto-closing soon"
                    elif remaining_h < 6:
                        auto_close_warn = f" ⏰ {remaining_h:.0f}h left"
                except (ValueError, TypeError):
                    pass
            lines.append(f"`#{tid}` — {t['category']} — {name}{auto_close_warn}")
        embed = discord.Embed(
            title=f"🎫 Open Tickets ({len(open_tickets)})",
            description="\n".join(lines),
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"Auto-close after {TICKET_AUTO_CLOSE_HOURS}h inactivity")
        await ctx.send(embed=embed)

    @reply_cmd.error
    @close_cmd.error
    @decline_cmd.error
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
