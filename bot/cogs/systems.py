"""
Systems Cog — comprehensive server management features.

Features:
  - Ticket panels with auto-handling and transcripts
  - Auto-role on join
  - Welcome / goodbye messages
  - Suggestion system (upvote/downvote)
  - Applications / forms with submissions
  - Giveaways with auto-end and winner picking
  - Verification system (button to get role)
  - Server snapshots + restore
  - Action history with undo
  - Scheduled recurring actions
  - Automation triggers (member join → role + message)
  - Always-allow-deletes toggle (bypass confirmations for owner)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import random
import string
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import BOT_COLOR, COLOR_OK, COLOR_ERR, COLOR_WARN, BOT_NAME, BOT_OWNER_ID
from utils import log_action
import data_store

log = logging.getLogger("vyrion.systems")


def _gen_id(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ── Ticket Panel View ──────────────────────────────────────────────────────────

class TicketPanelView(discord.ui.View):
    def __init__(self, panel_id: str, categories: list[str]):
        super().__init__(timeout=None)
        self.panel_id = panel_id
        for cat in categories[:5]:
            btn = discord.ui.Button(label=cat[:80], style=discord.ButtonStyle.primary, custom_id=f"ticket_panel:{panel_id}:{cat}")
            self.add_item(btn)


class TicketCloseView(discord.ui.View):
    def __init__(self, ticket_id: str):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild_id = interaction.guild.id if interaction.guild else 0
        panel_id = interaction.message.embeds[0].footer.text.split("|")[0].strip() if interaction.message.embeds else ""
        channel = interaction.channel
        # Build transcript
        transcript_lines = []
        try:
            async for msg in channel.history(limit=200, oldest_first=True):
                ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                transcript_lines.append(f"[{ts}] {msg.author}: {msg.content}")
        except Exception:
            pass
        transcript = "\n".join(transcript_lines) or "(no messages)"
        transcript_file = discord.File(
            fp=Path(__file__).parent / "data" / f"transcript_{self.ticket_id}.txt",
            filename=f"transcript_{self.ticket_id}.txt",
        )
        Path(__file__).parent.joinpath("data", f"transcript_{self.ticket_id}.txt").write_text(transcript)

        # Notify user
        try:
            user = interaction.guild.get_member(interaction.user.id) or interaction.user
            embed = discord.Embed(title=f"🔒 Ticket {self.ticket_id} Closed", description="Your ticket has been closed. A transcript is attached.", color=COLOR_ERR)
            embed.set_footer(text=BOT_NAME)
            await user.send(embed=embed, file=transcript_file)
        except Exception:
            pass

        await data_store.close_panel_ticket(guild_id, panel_id, self.ticket_id)
        await interaction.response.send_message("🔒 Ticket closed. Deleting channel in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        try:
            await channel.delete()
        except Exception:
            pass


# ── Verification View ──────────────────────────────────────────────────────────

class VerificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Verify", style=discord.ButtonStyle.success, custom_id="verify_btn")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        v = await data_store.get_verification(interaction.guild.id)
        if not v or not v["enabled"]:
            await interaction.response.send_message("Verification is not set up.", ephemeral=True)
            return
        role = interaction.guild.get_role(v["role_id"]) if v["role_id"] else None
        if not role:
            await interaction.response.send_message("Verification role not found.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message("You're already verified!", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ You've been verified! You now have the **@{role.name}** role.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I lack permission to assign that role.", ephemeral=True)


# ── Application View ───────────────────────────────────────────────────────────

class ApplicationView(discord.ui.View):
    def __init__(self, app_id: str, questions: list[str]):
        super().__init__(timeout=None)
        self.app_id = app_id
        self.questions = questions

    @discord.ui.button(label="📝 Apply", style=discord.ButtonStyle.primary, custom_id="apply_btn")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        app = await data_store.get_application(interaction.guild.id, self.app_id)
        if not app:
            await interaction.response.send_message("Application not found.", ephemeral=True)
            return
        await interaction.response.send_modal(ApplicationModal(self.app_id, self.questions, app["name"]))


class ApplicationModal(discord.ui.Modal):
    def __init__(self, app_id: str, questions: list[str], app_name: str):
        super().__init__(title=f"📝 {app_name}"[:45])
        self.app_id = app_id
        self.questions = questions
        for q in questions[:5]:
            self.add_item(discord.ui.TextInput(label=q[:45], placeholder=q[:100], style=discord.TextStyle.paragraph, max_length=500))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        answers = [c.value for c in self.children]
        sub_id = _gen_id(8)
        await data_store.add_application_submission(interaction.guild.id, self.app_id, sub_id, interaction.user.id, answers)
        app = await data_store.get_application(interaction.guild.id, self.app_id)
        if app and app["channel_id"]:
            channel = interaction.guild.get_channel(app["channel_id"])
            if channel:
                embed = discord.Embed(title=f"📋 New Application — {app['name']}", color=BOT_COLOR)
                embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
                for q, a in zip(self.questions, answers):
                    embed.add_field(name=q[:256], value=a[:1024], inline=False)
                embed.set_footer(text=f"Submission ID: {sub_id} | /app-review {self.app_id} {sub_id}")
                await channel.send(embed=embed)
        await interaction.response.send_message(f"✅ Application submitted! ID: `{sub_id}`", ephemeral=True)


# ── Giveaway View ──────────────────────────────────────────────────────────────

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="🎉 Enter", style=discord.ButtonStyle.success, custom_id="giveaway_enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            return
        added = await data_store.add_giveaway_participant(interaction.guild.id, self.giveaway_id, interaction.user.id)
        if added:
            await interaction.response.send_message("🎉 You've entered the giveaway!", ephemeral=True)
        else:
            await interaction.response.send_message("This giveaway has ended or you're already entered.", ephemeral=True)


# ── Suggestion View ────────────────────────────────────────────────────────────

class SuggestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👍 Upvote", style=discord.ButtonStyle.success, custom_id="suggestion_up", emoji="👍")
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("👍 You upvoted this suggestion!", ephemeral=True)

    @discord.ui.button(label="👎 Downvote", style=discord.ButtonStyle.danger, custom_id="suggestion_down", emoji="👎")
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("👎 You downvoted this suggestion!", ephemeral=True)


# ── Cog ────────────────────────────────────────────────────────────────────────

class Systems(commands.Cog, name="Systems"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.giveaway_loop.start()
        self.scheduled_loop.start()

    def cog_unload(self) -> None:
        self.giveaway_loop.cancel()
        self.scheduled_loop.cancel()

    # ── Ticket Panel command ───────────────────────────────────────────────────

    ticket_panel_group = app_commands.Group(name="ticket-panel", description="Manage ticket panels")

    @ticket_panel_group.command(name="create", description="Create a ticket panel with buttons")
    @app_commands.describe(
        channel="Channel to send the panel in",
        title="Panel title",
        description="Panel description",
        categories="Comma-separated categories (e.g. 'Support,Bug,Report')",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def panel_create(self, interaction: discord.Interaction, channel: discord.TextChannel, title: str, description: str, categories: str) -> None:
        if not interaction.guild:
            return
        cats = [c.strip() for c in categories.split(",") if c.strip()][:5]
        if not cats:
            await interaction.response.send_message("Provide at least one category.", ephemeral=True)
            return
        panel_id = _gen_id(6)
        await data_store.create_ticket_panel(interaction.guild.id, panel_id, channel.id, title, description, cats)
        embed = discord.Embed(title=title, description=description, color=BOT_COLOR)
        embed.set_footer(text=f"Panel ID: {panel_id} | {BOT_NAME}")
        view = TicketPanelView(panel_id, cats)
        msg = await channel.send(embed=embed, view=view)
        await data_store.set_panel_message_id(interaction.guild.id, panel_id, msg.id)
        await interaction.response.send_message(f"✅ Ticket panel created in {channel.mention} (ID: `{panel_id}`)", ephemeral=True)

    @ticket_panel_group.command(name="delete", description="Delete a ticket panel")
    @app_commands.describe(panel_id="Panel ID to delete")
    @app_commands.default_permissions(manage_guild=True)
    async def panel_delete(self, interaction: discord.Interaction, panel_id: str) -> None:
        if not interaction.guild:
            return
        ok = await data_store.delete_ticket_panel(interaction.guild.id, panel_id)
        await interaction.response.send_message("✅ Panel deleted." if ok else "Panel not found.", ephemeral=True)

    @ticket_panel_group.command(name="list", description="List all ticket panels")
    @app_commands.default_permissions(manage_guild=True)
    async def panel_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        panels = await data_store.list_ticket_panels(interaction.guild.id)
        if not panels:
            await interaction.response.send_message("No ticket panels.", ephemeral=True)
            return
        embed = discord.Embed(title="🎫 Ticket Panels", color=BOT_COLOR)
        for pid, p in panels.items():
            embed.add_field(name=f"ID: {pid}", value=f"**{p['title']}** — {p['description'][:100]}\nCategories: {', '.join(p['categories'])}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Button listener for ticket panels ─────────────────────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if not interaction.data or not isinstance(interaction.data, dict):
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("ticket_panel:"):
            parts = cid.split(":", 2)
            if len(parts) < 3:
                return
            panel_id = parts[1]
            category = parts[2]
            if not interaction.guild:
                return
            panel = await data_store.get_ticket_panel(interaction.guild.id, panel_id)
            if not panel:
                await interaction.response.send_message("This ticket panel no longer exists.", ephemeral=True)
                return
            # Create ticket channel
            category_obj = discord.utils.get(interaction.guild.categories, name="Tickets")
            if not category_obj:
                try:
                    category_obj = await interaction.guild.create_category("Tickets")
                except Exception:
                    category_obj = None
            ticket_id = _gen_id(6).upper()
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            try:
                ch = await interaction.guild.create_text_channel(f"ticket-{ticket_id}", category=category_obj, overwrites=overwrites)
            except discord.Forbidden:
                await interaction.response.send_message("I lack permission to create channels.", ephemeral=True)
                return
            await data_store.add_panel_ticket(interaction.guild.id, panel_id, ticket_id, interaction.user.id, ch.id, category)
            embed = discord.Embed(title=f"🎫 Ticket {ticket_id} — {category}", description=f"Hello {interaction.user.mention}! Please describe your issue.\n\nUse the button below to close this ticket.", color=BOT_COLOR)
            embed.set_footer(text=f"{panel_id} | {ticket_id}")
            await ch.send(embed=embed, view=TicketCloseView(ticket_id))
            await interaction.response.send_message(f"✅ Ticket created: {ch.mention}", ephemeral=True)

    # ── Autorole ────────────────────────────────────────────────────────────────

    autorole_group = app_commands.Group(name="autorole", description="Configure auto-role on join")

    @autorole_group.command(name="set", description="Set roles to give when a member joins")
    @app_commands.describe(enabled="Enable or disable", roles="Comma-separated role IDs or names")
    @app_commands.default_permissions(manage_guild=True)
    async def autorole_set(self, interaction: discord.Interaction, enabled: bool, roles: str) -> None:
        if not interaction.guild:
            return
        role_ids: list[int] = []
        for r in roles.split(","):
            r = r.strip()
            if r.isdigit():
                role_ids.append(int(r))
            else:
                role = discord.utils.get(interaction.guild.roles, name=r)
                if role:
                    role_ids.append(role.id)
        await data_store.set_autorole(interaction.guild.id, enabled, role_ids)
        await interaction.response.send_message(f"✅ Auto-role {'enabled' if enabled else 'disabled'} with {len(role_ids)} role(s).", ephemeral=True)

    @autorole_group.command(name="view", description="View current autorole config")
    async def autorole_view(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        cfg = await data_store.get_autorole(interaction.guild.id)
        embed = discord.Embed(title="🎭 Auto-role", color=BOT_COLOR)
        embed.add_field(name="Enabled", value=str(cfg["enabled"]), inline=False)
        names = []
        for rid in cfg["roles"]:
            r = interaction.guild.get_role(rid)
            names.append(r.name if r else f"Unknown ({rid})")
        embed.add_field(name="Roles", value=", ".join(names) or "None", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Welcome / Goodbye ───────────────────────────────────────────────────────

    welcome_group = app_commands.Group(name="welcome", description="Configure welcome/goodbye messages")

    @welcome_group.command(name="set", description="Set welcome message")
    @app_commands.describe(
        enabled="Enable or disable",
        channel="Channel for welcome messages",
        message="Welcome message (use {user}, {server}, {count} as placeholders)",
        goodbye_channel="Channel for goodbye messages (optional, same as welcome if omitted)",
        goodbye_message="Goodbye message (use {user}, {server} as placeholders)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def welcome_set(
        self, interaction: discord.Interaction, enabled: bool, channel: discord.TextChannel, message: str,
        goodbye_channel: discord.TextChannel | None = None, goodbye_message: str = "",
    ) -> None:
        if not interaction.guild:
            return
        await data_store.set_welcome(interaction.guild.id, enabled, channel.id, message, goodbye_channel.id if goodbye_channel else channel.id, goodbye_message)
        await interaction.response.send_message("✅ Welcome/goodbye configured.", ephemeral=True)

    @welcome_group.command(name="view", description="View welcome config")
    async def welcome_view(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        cfg = await data_store.get_welcome(interaction.guild.id)
        embed = discord.Embed(title="👋 Welcome/Goodbye", color=BOT_COLOR)
        embed.add_field(name="Enabled", value=str(cfg["enabled"]), inline=False)
        ch = interaction.guild.get_channel(cfg["channel_id"]) if cfg["channel_id"] else None
        embed.add_field(name="Welcome Channel", value=ch.mention if ch else "None", inline=False)
        embed.add_field(name="Welcome Message", value=cfg["message"][:500] or "None", inline=False)
        gch = interaction.guild.get_channel(cfg["goodbye_channel_id"]) if cfg["goodbye_channel_id"] else None
        embed.add_field(name="Goodbye Channel", value=gch.mention if gch else "None", inline=False)
        embed.add_field(name="Goodbye Message", value=cfg["goodbye_message"][:500] or "None", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Suggestions ─────────────────────────────────────────────────────────────

    @app_commands.command(name="suggestion-channel", description="Set the channel for suggestions")
    @app_commands.describe(channel="Channel for suggestions")
    @app_commands.default_permissions(manage_guild=True)
    async def set_suggestion_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.guild:
            return
        await data_store.set_suggestion_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(f"✅ Suggestion channel set to {channel.mention}", ephemeral=True)

    @app_commands.command(name="suggest", description="Submit a suggestion")
    @app_commands.describe(suggestion="Your suggestion")
    async def suggest(self, interaction: discord.Interaction, suggestion: str) -> None:
        if not interaction.guild:
            return
        cfg = await data_store.get_suggestions(interaction.guild.id)
        if not cfg or not cfg.get("channel_id"):
            await interaction.response.send_message("Suggestions not configured.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(cfg["channel_id"])
        if not channel:
            await interaction.response.send_message("Suggestion channel not found.", ephemeral=True)
            return
        embed = discord.Embed(title="💡 New Suggestion", description=suggestion[:4000], color=BOT_COLOR)
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"User ID: {interaction.user.id}")
        msg = await channel.send(embed=embed, view=SuggestionView())
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        await interaction.response.send_message(f"✅ Suggestion submitted in {channel.mention}", ephemeral=True)

    # ── Applications ────────────────────────────────────────────────────────────

    application_group = app_commands.Group(name="application", description="Manage application forms")

    @application_group.command(name="create", description="Create an application form")
    @app_commands.describe(
        name="Application name",
        description="Application description",
        questions="Comma-separated questions (up to 5)",
        channel="Channel to post the application in",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def app_create(self, interaction: discord.Interaction, name: str, description: str, questions: str, channel: discord.TextChannel) -> None:
        if not interaction.guild:
            return
        qs = [q.strip() for q in questions.split(",") if q.strip()][:5]
        if not qs:
            await interaction.response.send_message("Provide at least one question.", ephemeral=True)
            return
        app_id = _gen_id(6)
        await data_store.create_application(interaction.guild.id, app_id, name, description, qs, channel.id)
        embed = discord.Embed(title=f"📝 {name}", description=description, color=BOT_COLOR)
        embed.add_field(name="Questions", value="\n".join(f"{i+1}. {q}" for i, q in enumerate(qs)), inline=False)
        embed.set_footer(text=f"App ID: {app_id}")
        view = ApplicationView(app_id, qs)
        msg = await channel.send(embed=embed, view=view)
        await data_store.set_application_message_id(interaction.guild.id, app_id, msg.id)
        await interaction.response.send_message(f"✅ Application created in {channel.mention} (ID: `{app_id}`)", ephemeral=True)

    @application_group.command(name="list", description="List all applications")
    @app_commands.default_permissions(manage_guild=True)
    async def app_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        apps = await data_store.list_applications(interaction.guild.id)
        if not apps:
            await interaction.response.send_message("No applications.", ephemeral=True)
            return
        embed = discord.Embed(title="📝 Applications", color=BOT_COLOR)
        for aid, a in apps.items():
            embed.add_field(name=f"ID: {aid}", value=f"**{a['name']}** — {a['description'][:100]}\n{len(a['questions'])} questions, {len(a['submissions'])} submissions", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @application_group.command(name="delete", description="Delete an application")
    @app_commands.describe(app_id="Application ID")
    @app_commands.default_permissions(manage_guild=True)
    async def app_delete(self, interaction: discord.Interaction, app_id: str) -> None:
        if not interaction.guild:
            return
        ok = await data_store.delete_application(interaction.guild.id, app_id)
        await interaction.response.send_message("✅ Deleted." if ok else "Not found.", ephemeral=True)

    @application_group.command(name="review", description="Review a submission")
    @app_commands.describe(app_id="Application ID", submission_id="Submission ID", status="accept or reject")
    @app_commands.choices(status=[app_commands.Choice(name="Accept", value="accepted"), app_commands.Choice(name="Reject", value="rejected")])
    @app_commands.default_permissions(manage_guild=True)
    async def app_review(self, interaction: discord.Interaction, app_id: str, submission_id: str, status: app_commands.Choice[str]) -> None:
        if not interaction.guild:
            return
        ok = await data_store.update_application_submission_status(interaction.guild.id, app_id, submission_id, status.value)
        if not ok:
            await interaction.response.send_message("Submission not found.", ephemeral=True)
            return
        app = await data_store.get_application(interaction.guild.id, app_id)
        if app and submission_id in app["submissions"]:
            sub = app["submissions"][submission_id]
            try:
                user = await self.bot.fetch_user(sub["user_id"])
                dm = await user.create_dm()
                embed = discord.Embed(title=f"📋 Application {status.name}", description=f"Your application for **{app['name']}** has been **{status.value}**.", color=COLOR_OK if status.value == "accepted" else COLOR_ERR)
                await dm.send(embed=embed)
            except Exception:
                pass
        await interaction.response.send_message(f"✅ Submission {status.name}.", ephemeral=True)

    # ── Giveaways ───────────────────────────────────────────────────────────────

    giveaway_group = app_commands.Group(name="giveaway", description="Manage giveaways")

    @giveaway_group.command(name="start", description="Start a giveaway")
    @app_commands.describe(
        channel="Channel for the giveaway",
        title="Giveaway title",
        description="Giveaway description",
        prize="Prize",
        duration_minutes="Duration in minutes",
        winners="Number of winners",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway_start(self, interaction: discord.Interaction, channel: discord.TextChannel, title: str, description: str, prize: str, duration_minutes: int, winners: int) -> None:
        if not interaction.guild:
            return
        gid = _gen_id(6)
        gw = await data_store.create_giveaway(interaction.guild.id, gid, channel.id, title, description, prize, duration_minutes, winners)
        embed = discord.Embed(title=f"🎉 {title}", description=description, color=COLOR_OK)
        embed.add_field(name="Prize", value=prize, inline=False)
        embed.add_field(name="Winners", value=str(winners), inline=True)
        embed.add_field(name="Ends", value=f"<t:{gw['end_ts']}:R>", inline=True)
        embed.set_footer(text=f"Giveaway ID: {gid}")
        view = GiveawayView(gid)
        msg = await channel.send(embed=embed, view=view)
        await data_store.set_giveaway_message_id(interaction.guild.id, gid, msg.id)
        await interaction.response.send_message(f"✅ Giveaway started in {channel.mention} (ID: `{gid}`)", ephemeral=True)

    @giveaway_group.command(name="list", description="List all giveaways")
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        gws = await data_store.list_giveaways(interaction.guild.id)
        if not gws:
            await interaction.response.send_message("No giveaways.", ephemeral=True)
            return
        embed = discord.Embed(title="🎉 Giveaways", color=BOT_COLOR)
        for gid, g in gws.items():
            status = "Ended" if g["ended"] else "Active"
            embed.add_field(name=f"ID: {gid} ({status})", value=f"**{g['title']}** — Prize: {g['prize']}\n{len(g['participants'])} participants", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @giveaway_group.command(name="end", description="End a giveaway early")
    @app_commands.describe(giveaway_id="Giveaway ID")
    @app_commands.default_permissions(manage_guild=True)
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: str) -> None:
        if not interaction.guild:
            return
        await self._end_giveaway(interaction.guild, giveaway_id)
        await interaction.response.send_message("✅ Giveaway ended.", ephemeral=True)

    async def _end_giveaway(self, guild: discord.Guild, giveaway_id: str) -> None:
        gw = await data_store.get_giveaway(guild.id, giveaway_id)
        if not gw or gw["ended"]:
            return
        await data_store.end_giveaway(guild.id, giveaway_id)
        channel = guild.get_channel(gw["channel_id"])
        if not channel:
            return
        try:
            msg = await channel.fetch_message(gw["message_id"]) if gw["message_id"] else None
        except Exception:
            msg = None
        winners_list = []
        if gw["participants"]:
            n = min(gw["winners"], len(gw["participants"]))
            winners_list = random.sample(gw["participants"], n)
        winner_mentions = ", ".join(f"<@{w}>" for w in winners_list) or "No participants"
        embed = discord.Embed(title=f"🎉 {gw['title']} [ENDED]", description=f"**Prize:** {gw['prize']}\n**Winners:** {winner_mentions}", color=COLOR_OK)
        embed.set_footer(text=f"Giveaway ID: {giveaway_id} | Ended at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        if msg:
            try:
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass
        await channel.send(f"🎉 Giveaway ended! Winners: {winner_mentions}")

    @tasks.loop(minutes=1)
    async def giveaway_loop(self) -> None:
        for guild in self.bot.guilds:
            gws = await data_store.list_giveaways(guild.id)
            for gid, g in gws.items():
                if not g["ended"] and time.time() >= g["end_ts"]:
                    await self._end_giveaway(guild, gid)

    @giveaway_loop.before_loop
    async def _before_giveaway(self) -> None:
        await self.bot.wait_until_ready()

    # ── Verification ────────────────────────────────────────────────────────────

    verification_group = app_commands.Group(name="verification", description="Configure verification")

    @verification_group.command(name="set", description="Set up verification")
    @app_commands.describe(
        enabled="Enable or disable",
        role="Role to give on verification",
        channel="Channel to post verification message in",
        message="Verification message",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def verify_set(self, interaction: discord.Interaction, enabled: bool, role: discord.Role, channel: discord.TextChannel, message: str) -> None:
        if not interaction.guild:
            return
        await data_store.set_verification(interaction.guild.id, enabled, role.id, channel.id, message)
        if enabled:
            embed = discord.Embed(title="✅ Verification", description=message, color=COLOR_OK)
            embed.set_footer(text="Click the button below to verify")
            await channel.send(embed=embed, view=VerificationView())
        await interaction.response.send_message("✅ Verification configured.", ephemeral=True)

    @verification_group.command(name="view", description="View verification config")
    async def verify_view(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        v = await data_store.get_verification(interaction.guild.id)
        embed = discord.Embed(title="✅ Verification", color=BOT_COLOR)
        embed.add_field(name="Enabled", value=str(v["enabled"]), inline=False)
        r = interaction.guild.get_role(v["role_id"]) if v["role_id"] else None
        embed.add_field(name="Role", value=r.name if r else "None", inline=False)
        ch = interaction.guild.get_channel(v["channel_id"]) if v["channel_id"] else None
        embed.add_field(name="Channel", value=ch.mention if ch else "None", inline=False)
        embed.add_field(name="Message", value=v["message"][:500] or "None", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Snapshots ───────────────────────────────────────────────────────────────

    snapshot_group = app_commands.Group(name="snapshot", description="Server snapshots and restore")

    @snapshot_group.command(name="save", description="Save a snapshot of the server structure")
    @app_commands.describe(name="Snapshot name")
    @app_commands.default_permissions(manage_guild=True)
    async def snapshot_save(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        data: dict = {"categories": [], "channels": [], "roles": []}
        for cat in guild.categories:
            data["categories"].append({"name": cat.name, "position": cat.position})
        for ch in guild.channels:
            ch_type = type(ch).__name__
            data["channels"].append({
                "name": ch.name, "type": ch_type, "category": ch.category.name if ch.category else None,
                "topic": getattr(ch, "topic", None), "position": ch.position,
            })
        for r in guild.roles:
            if r.is_default():
                continue
            data["roles"].append({
                "name": r.name, "color": f"#{r.color.value:06X}", "hoist": r.hoist,
                "mentionable": r.mentionable, "position": r.position,
                "permissions": r.permissions.value,
            })
        sid = _gen_id(6)
        await data_store.save_snapshot(guild.id, sid, name, data)
        await interaction.followup.send(f"✅ Snapshot saved: **{name}** (ID: `{sid}`) — {len(data['channels'])} channels, {len(data['roles'])} roles, {len(data['categories'])} categories", ephemeral=True)

    @snapshot_group.command(name="list", description="List all snapshots")
    @app_commands.default_permissions(manage_guild=True)
    async def snapshot_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        snaps = await data_store.list_snapshots(interaction.guild.id)
        if not snaps:
            await interaction.response.send_message("No snapshots.", ephemeral=True)
            return
        embed = discord.Embed(title="📸 Snapshots", color=BOT_COLOR)
        for sid, s in snaps.items():
            ts = datetime.datetime.fromtimestamp(s["ts"]).strftime("%Y-%m-%d %H:%M")
            embed.add_field(name=f"ID: {sid}", value=f"**{s['name']}** — {ts}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @snapshot_group.command(name="restore", description="Restore channels and roles from a snapshot")
    @app_commands.describe(snapshot_id="Snapshot ID")
    @app_commands.default_permissions(manage_guild=True)
    async def snapshot_restore(self, interaction: discord.Interaction, snapshot_id: str) -> None:
        if not interaction.guild:
            return
        snap = await data_store.get_snapshot(interaction.guild.id, snapshot_id)
        if not snap:
            await interaction.response.send_message("Snapshot not found.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        data = snap["data"]
        created = 0
        # Restore roles
        for r in sorted(data["roles"], key=lambda x: x["position"]):
            existing = discord.utils.get(guild.roles, name=r["name"])
            if not existing:
                try:
                    await guild.create_role(
                        name=r["name"], color=discord.Color(int(r["color"].lstrip("#"), 16)),
                        hoist=r["hoist"], mentionable=r["mentionable"],
                        permissions=discord.Permissions(r["permissions"]),
                    )
                    created += 1
                except Exception:
                    pass
        # Restore categories
        for c in data["categories"]:
            existing = discord.utils.get(guild.categories, name=c["name"])
            if not existing:
                try:
                    await guild.create_category(c["name"])
                    created += 1
                except Exception:
                    pass
        # Restore channels
        for ch in data["channels"]:
            existing = discord.utils.get(guild.channels, name=ch["name"])
            if not existing:
                cat = discord.utils.get(guild.categories, name=ch["category"]) if ch["category"] else None
                try:
                    if ch["type"] == "TextChannel":
                        await guild.create_text_channel(ch["name"], category=cat, topic=ch.get("topic"))
                    elif ch["type"] == "VoiceChannel":
                        await guild.create_voice_channel(ch["name"], category=cat)
                    elif ch["type"] == "ForumChannel":
                        await guild.create_forum_channel(ch["name"], category=cat)
                    elif ch["type"] == "StageChannel":
                        await guild.create_stage_channel(ch["name"], category=cat)
                    created += 1
                except Exception:
                    pass
        await interaction.followup.send(f"✅ Restore complete. Created {created} items from snapshot **{snap['name']}**.", ephemeral=True)

    @snapshot_group.command(name="delete", description="Delete a snapshot")
    @app_commands.default_permissions(manage_guild=True)
    async def snapshot_delete(self, interaction: discord.Interaction, snapshot_id: str) -> None:
        if not interaction.guild:
            return
        ok = await data_store.delete_snapshot(interaction.guild.id, snapshot_id)
        await interaction.response.send_message("✅ Deleted." if ok else "Not found.", ephemeral=True)

    # ── Action History ──────────────────────────────────────────────────────────

    @app_commands.command(name="action-history", description="View recent actions taken by the bot/subagent")
    @app_commands.describe(limit="Number of entries (max 25)")
    @app_commands.default_permissions(manage_guild=True)
    async def action_history(self, interaction: discord.Interaction, limit: int = 25) -> None:
        if not interaction.guild:
            return
        entries = await data_store.get_action_history(interaction.guild.id, min(limit, 25))
        if not entries:
            await interaction.response.send_message("No action history.", ephemeral=True)
            return
        embed = discord.Embed(title="📋 Action History", color=BOT_COLOR)
        for e in entries:
            ts = datetime.datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M")
            embed.add_field(name=f"{ts} — {e['action']}", value=e["detail"][:200], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="undo-last", description="Undo the last action taken by the subagent")
    @app_commands.default_permissions(manage_guild=True)
    async def undo_last(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        last = await data_store.get_last_action(interaction.guild.id)
        if not last or not last.get("undo_data"):
            await interaction.response.send_message("No undoable action found.", ephemeral=True)
            return
        undo = last["undo_data"]
        action = last["action"]
        guild = interaction.guild
        try:
            if action in ("create_text_channel", "create_voice_channel", "create_forum_channel", "create_announcement_channel", "create_stage_channel"):
                ch = discord.utils.get(guild.channels, name=undo.get("name", ""))
                if ch:
                    await ch.delete()
                    await interaction.response.send_message(f"✅ Undone: deleted #{undo['name']}", ephemeral=True)
                    return
            elif action == "create_role":
                role = discord.utils.get(guild.roles, name=undo.get("name", ""))
                if role:
                    await role.delete()
                    await interaction.response.send_message(f"✅ Undone: deleted @{undo['name']}", ephemeral=True)
                    return
            elif action == "create_category":
                cat = discord.utils.get(guild.categories, name=undo.get("name", ""))
                if cat:
                    await cat.delete()
                    await interaction.response.send_message(f"✅ Undone: deleted category {undo['name']}", ephemeral=True)
                    return
            elif action == "create_scheduled_event":
                for e in guild.scheduled_events:
                    if e.name == undo.get("name", ""):
                        await e.delete()
                        await interaction.response.send_message(f"✅ Undone: deleted event {undo['name']}", ephemeral=True)
                        return
            elif action == "send_message":
                ch = guild.get_channel(undo.get("channel_id", 0))
                if ch:
                    msg = await ch.fetch_message(undo.get("message_id", 0))
                    await msg.delete()
                    await interaction.response.send_message("✅ Undone: deleted message", ephemeral=True)
                    return
            await interaction.response.send_message("Could not undo this action type.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Undo failed: {e}", ephemeral=True)

    # ── Always allow deletes ─────────────────────────────────────────────────────

    @app_commands.command(name="always-allow-deletes", description="Toggle bypassing delete confirmations (owner only)")
    @app_commands.describe(enabled="True to bypass confirmations, False to require them")
    @app_commands.default_permissions(administrator=True)
    async def always_allow_deletes(self, interaction: discord.Interaction, enabled: bool) -> None:
        if not interaction.guild:
            return
        if interaction.user.id != BOT_OWNER_ID and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the server owner or bot owner can use this.", ephemeral=True)
            return
        await data_store.set_always_allow_deletes(interaction.guild.id, enabled)
        await interaction.response.send_message(f"✅ Always-allow-deletes is now **{'ON' if enabled else 'OFF'}**.", ephemeral=True)

    # ── Scheduled actions ───────────────────────────────────────────────────────

    scheduled_group = app_commands.Group(name="scheduled", description="Scheduled recurring actions")

    @scheduled_group.command(name="add", description="Schedule a recurring message")
    @app_commands.describe(
        channel="Channel to post in",
        content="Message content",
        day="Day of week (monday, tuesday, ... or 'daily')",
        hour="Hour (0-23, UTC)",
        minute="Minute (0-59)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def sched_add(self, interaction: discord.Interaction, channel: discord.TextChannel, content: str, day: str, hour: int, minute: int) -> None:
        if not interaction.guild:
            return
        day = day.lower().strip()
        valid = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "daily"]
        if day not in valid:
            await interaction.response.send_message(f"Invalid day. Use one of: {', '.join(valid)}", ephemeral=True)
            return
        sid = _gen_id(6)
        await data_store.add_scheduled_action(interaction.guild.id, sid, channel.id, content, day, hour, minute)
        await interaction.response.send_message(f"✅ Scheduled action created (ID: `{sid}`)", ephemeral=True)

    @scheduled_group.command(name="list", description="List scheduled actions")
    @app_commands.default_permissions(manage_guild=True)
    async def sched_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        entries = await data_store.list_scheduled_actions(interaction.guild.id)
        if not entries:
            await interaction.response.send_message("No scheduled actions.", ephemeral=True)
            return
        embed = discord.Embed(title="⏰ Scheduled Actions", color=BOT_COLOR)
        for e in entries:
            embed.add_field(name=f"ID: {e['id']}", value=f"Day: {e['day']} at {e['hour']:02d}:{e['minute']:02d} UTC\nChannel: <#{e['channel_id']}>\nContent: {e['content'][:100]}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @scheduled_group.command(name="remove", description="Remove a scheduled action")
    @app_commands.describe(sched_id="Scheduled action ID")
    @app_commands.default_permissions(manage_guild=True)
    async def sched_remove(self, interaction: discord.Interaction, sched_id: str) -> None:
        if not interaction.guild:
            return
        ok = await data_store.remove_scheduled_action(interaction.guild.id, sched_id)
        await interaction.response.send_message("✅ Removed." if ok else "Not found.", ephemeral=True)

    @tasks.loop(minutes=1)
    async def scheduled_loop(self) -> None:
        now = datetime.datetime.utcnow()
        day_name = now.strftime("%A").lower()
        for guild in self.bot.guilds:
            entries = await data_store.list_scheduled_actions(guild.id)
            for e in entries:
                if not e["enabled"]:
                    continue
                if e["day"] != "daily" and e["day"] != day_name:
                    continue
                if now.hour != e["hour"] or now.minute != e["minute"]:
                    continue
                if time.time() - e["last_run"] < 60:
                    continue
                channel = guild.get_channel(e["channel_id"])
                if channel:
                    try:
                        await channel.send(e["content"][:2000])
                    except Exception:
                        pass
                await data_store.update_scheduled_last_run(guild.id, e["id"], int(time.time()))

    @scheduled_loop.before_loop
    async def _before_scheduled(self) -> None:
        await self.bot.wait_until_ready()

    # ── Automation triggers ─────────────────────────────────────────────────────

    automation_group = app_commands.Group(name="automation", description="Configure automation triggers")

    @automation_group.command(name="set", description="Set an automation trigger")
    @app_commands.describe(
        trigger="Trigger type (member_join, member_leave)",
        role_name="Role to assign (for member_join, use 'none' to skip)",
        message="Message to send (use {user}, {server})",
        channel="Channel to send message in (optional)",
    )
    @app_commands.choices(trigger=[
        app_commands.Choice(name="Member Join", value="member_join"),
        app_commands.Choice(name="Member Leave", value="member_leave"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def auto_set(self, interaction: discord.Interaction, trigger: app_commands.Choice[str], role_name: str, message: str, channel: discord.TextChannel | None = None) -> None:
        if not interaction.guild:
            return
        role_id = None
        if role_name.lower() != "none":
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            role_id = role.id if role else None
        await data_store.set_automation(interaction.guild.id, trigger.value, {
            "role_id": role_id, "message": message, "channel_id": channel.id if channel else None,
        })
        await interaction.response.send_message(f"✅ Automation set for **{trigger.name}**.", ephemeral=True)

    @automation_group.command(name="list", description="List automation triggers")
    @app_commands.default_permissions(manage_guild=True)
    async def auto_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        autos = await data_store.list_automation(interaction.guild.id)
        if not autos:
            await interaction.response.send_message("No automation configured.", ephemeral=True)
            return
        embed = discord.Embed(title="🤖 Automation", color=BOT_COLOR)
        for trig, a in autos.items():
            r = interaction.guild.get_role(a["role_id"]) if a["role_id"] else None
            ch = interaction.guild.get_channel(a["channel_id"]) if a["channel_id"] else None
            embed.add_field(name=trig, value=f"Role: {r.name if r else 'None'}\nChannel: {ch.mention if ch else 'None'}\nMessage: {a['message'][:100]}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @automation_group.command(name="remove", description="Remove an automation trigger")
    @app_commands.describe(trigger="Trigger to remove")
    @app_commands.choices(trigger=[
        app_commands.Choice(name="Member Join", value="member_join"),
        app_commands.Choice(name="Member Leave", value="member_leave"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def auto_remove(self, interaction: discord.Interaction, trigger: app_commands.Choice[str]) -> None:
        if not interaction.guild:
            return
        ok = await data_store.remove_automation(interaction.guild.id, trigger.value)
        await interaction.response.send_message("✅ Removed." if ok else "Not found.", ephemeral=True)

    # ── Event listeners ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        # Autorole
        cfg = await data_store.get_autorole(guild.id)
        if cfg["enabled"] and cfg["roles"]:
            for rid in cfg["roles"]:
                role = guild.get_role(rid)
                if role:
                    try:
                        await member.add_roles(role)
                    except Exception:
                        pass
        # Welcome message
        wcfg = await data_store.get_welcome(guild.id)
        if wcfg["enabled"] and wcfg["channel_id"]:
            ch = guild.get_channel(wcfg["channel_id"])
            if ch:
                msg = wcfg["message"].replace("{user}", member.mention).replace("{server}", guild.name).replace("{count}", str(guild.member_count))
                try:
                    await ch.send(msg[:2000])
                except Exception:
                    pass
        # Automation: member_join
        auto = await data_store.get_automation(guild.id, "member_join")
        if auto:
            if auto["role_id"]:
                role = guild.get_role(auto["role_id"])
                if role:
                    try:
                        await member.add_roles(role)
                    except Exception:
                        pass
            if auto["channel_id"] and auto["message"]:
                ch = guild.get_channel(auto["channel_id"])
                if ch:
                    msg = auto["message"].replace("{user}", member.mention).replace("{server}", guild.name)
                    try:
                        await ch.send(msg[:2000])
                    except Exception:
                        pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        # Goodbye message
        wcfg = await data_store.get_welcome(guild.id)
        if wcfg["enabled"] and wcfg["goodbye_channel_id"] and wcfg["goodbye_message"]:
            ch = guild.get_channel(wcfg["goodbye_channel_id"])
            if ch:
                msg = wcfg["goodbye_message"].replace("{user}", member.name).replace("{server}", guild.name)
                try:
                    await ch.send(msg[:2000])
                except Exception:
                    pass
        # Automation: member_leave
        auto = await data_store.get_automation(guild.id, "member_leave")
        if auto and auto["channel_id"] and auto["message"]:
            ch = guild.get_channel(auto["channel_id"])
            if ch:
                msg = auto["message"].replace("{user}", member.name).replace("{server}", guild.name)
                try:
                    await ch.send(msg[:2000])
                except Exception:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Systems(bot))
