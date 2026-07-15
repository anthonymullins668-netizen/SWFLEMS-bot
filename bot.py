import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import io
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
import motor.motor_asyncio

EST = ZoneInfo("America/New_York")

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN             = "MTUyNjU1MTA5NzgwNjY4NDIwMA.GhoZC_.gDqZIXwhMvtXAh8F3NA4N9uipw7RGsi7Ps12zw"
MONGO_URI             = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
GUILD_ID              = 1526407839629709353
CLOCKIN_CHANNEL_ID    = 1526407840439210049
LOG_CHANNEL_ID        = 1526407840900710449
ALLOWED_ROLE_ID       = 1526407839667589130
HELP_CHANNEL_ID       = 1526599111015661698
CODES_CHANNEL_ID      = 1526407840267505678
TICKET_CHANNEL_ID     = 1526558806425997473
TRANSCRIPT_CHANNEL_ID = 1526642639858958488
LOA_CHANNEL_ID        = 1526587409091920034
HIGHERUP_NOTIFY_ID    = 1526407840900710443

# Ticket categories
CAT_GENERAL  = 1526586988223008941
CAT_INCIDENT = 1526587148483170314

# Roles
ROLE_LT_VIEWER = 1526407839655137295
ROLE_CAP_VIEW1 = 1526407839667589130
ROLE_CAP_VIEW2 = 1526407839667589134
ROLE_HIGHER_UP = 1526407839655137295
ROLE_CHIEF     = 1526407839667589130
ROLE_MED_DIR   = 1526407839667589134

# Ranks ordered low → high
RANKS = [
    (1526407839642288200, "EMR"),
    (1526407839642288201, "EMT"),
    (1526417635837743255, "Junior Paramedic"),
    (1526407839642288202, "Paramedic"),
    (1526407839655137290, "Sr. Paramedic"),
    (1526407839655137294, "Lieutenant"),
    (1526407839655137297, "Captain"),
    (1526407839655137298, "Medical Chief Advisor"),
    (1526407839655137299, "Assistant Chief"),
]
LT_AND_BELOW_IDS = {r[0] for r in RANKS[:6]}

# ── MongoDB ───────────────────────────────────────────────────────────────────
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db           = mongo_client["swfl_ems"]
clock_col    = db["clock_data"]    # { _id: user_id_str, total: float, clocked_in_at: str|None }
panels_col   = db["panels"]        # { _id: panel_name, channel_id: int, message_id: int }

# ── DB helpers ────────────────────────────────────────────────────────────────

async def get_user_clock(uid: str) -> dict:
    doc = await clock_col.find_one({"_id": uid})
    if doc is None:
        return {"total": 0.0, "clocked_in_at": None}
    return {"total": doc.get("total", 0.0), "clocked_in_at": doc.get("clocked_in_at")}

async def save_user_clock(uid: str, data: dict):
    await clock_col.update_one({"_id": uid}, {"$set": data}, upsert=True)

async def get_all_clock() -> list:
    docs = []
    async for doc in clock_col.find():
        docs.append((doc["_id"], doc.get("total", 0.0), doc.get("clocked_in_at")))
    return docs

async def reset_all_clock():
    await clock_col.update_many({}, {"$set": {"total": 0.0, "clocked_in_at": None}})

async def get_panel(name: str) -> dict | None:
    return await panels_col.find_one({"_id": name})

async def save_panel(name: str, channel_id: int, message_id: int):
    await panels_col.update_one(
        {"_id": name},
        {"$set": {"channel_id": channel_id, "message_id": message_id}},
        upsert=True
    )

# ── Helpers ───────────────────────────────────────────────────────────────────

def format_seconds(total: float) -> str:
    total = int(total)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h}h {m}m {s}s"

def parse_dt(iso_str: str) -> datetime:
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EST)
    return dt.astimezone(EST)

def has_allowed_role(interaction: discord.Interaction) -> bool:
    allowed = discord.utils.get(interaction.guild.roles, id=ALLOWED_ROLE_ID)
    if allowed is None:
        return False
    return any(r.position >= allowed.position for r in interaction.user.roles)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree
G    = discord.Object(id=GUILD_ID)

# ── Embed builders ────────────────────────────────────────────────────────────

def build_clock_embed() -> discord.Embed:
    e = discord.Embed(title="🕐 Time Clock", color=discord.Color.gold(),
        description="Use the buttons below to manage your time.\n\n🟢 **Clock In** — Start your session\n🔴 **Clock Out** — End your session\n🔵 **Check My Time** — View your total hours this week")
    e.set_footer(text="Time resets every Sunday at midnight EST")
    return e

def build_ticket_embed() -> discord.Embed:
    e = discord.Embed(title="🎫 Southwest Florida EMS — Support Center", color=discord.Color.dark_red(),
        description="Need help or want to file a report? Select an option below.\n\n📋 **General Support** — Questions, issues, or general requests\n📝 **Incident Report** — Report an incident involving a member")
    e.set_footer(text="Only open a ticket if you have a genuine request.")
    return e

def build_help_embed() -> discord.Embed:
    e = discord.Embed(title="📖 Southwest Florida EMS Bot — Command Reference",
        description="Below is a list of all available commands and what they do.\n\u200b", color=discord.Color.blue())
    e.add_field(name="🎭 /role `[member]` `[role]`",           value="Assigns the chosen role to the chosen member.", inline=False)
    e.add_field(name="📢 /embedcreate `[channel]` `[title]` `[links]`", value="Posts an embed with clickable links into the specified channel.", inline=False)
    e.add_field(name="🕐 /clockinembed",                        value=f"Posts the Time Clock panel in <#{CLOCKIN_CHANNEL_ID}>.", inline=False)
    e.add_field(name="🏆 /clockleaderboard",                    value=f"Top 10 members by clocked hours this week. Only in <#{LOG_CHANNEL_ID}>.", inline=False)
    e.add_field(name="⏱️ /checktime `[user]`",                  value=f"Shows weekly clocked hours for a user. Only in <#{LOG_CHANNEL_ID}>.", inline=False)
    e.add_field(name="🎫 /ticketpanel",                         value=f"Posts the support ticket panel in <#{TICKET_CHANNEL_ID}>.", inline=False)
    e.add_field(name="📋 /loarequest `[start]` `[end]` `[reason]`", value=f"Submit a Leave of Absence request in <#{LOA_CHANNEL_ID}>.", inline=False)
    e.add_field(name="✏️ /rename `[name]`",                     value="Rename a ticket channel (ticket channels only).", inline=False)
    e.add_field(name="🔔 /higherup",                            value="Ping leadership to review the current ticket.", inline=False)
    e.add_field(name="\u200b", value="⚠️ **All commands require the designated staff role or higher.**\n🔄 Clock time resets every **Sunday at midnight EST**.", inline=False)
    e.set_footer(text="Southwest Florida EMS Bot")
    e.timestamp = datetime.now(EST)
    return e

def build_codes_embed() -> discord.Embed:
    e = discord.Embed(title="📻 Southwest Florida EMS — Radio Codes & Response Codes", color=discord.Color.red())
    e.add_field(name="📡 10-Codes", inline=True, value=(
        "`10-1` — Change Frequency\n`10-2` — On Standby\n`10-3` — Clear Radio Traffic\n"
        "`10-4` — Copy *(acknowledged)*\n`10-5` — Radio Check Heard\n`10-6` — Busy\n"
        "`10-7` — Out of Service\n`10-8` — In Service\n`10-9` — Repeat\n"
        "`10-12` — Active Ride Along\n`10-13` — General 911 Call\n`10-19` — Enroute to Hospital"))
    e.add_field(name="\u200b", inline=True, value=(
        "`10-20` — Location\n`10-22` — Disregard\n`10-23` — Arrived on Scene\n"
        "`10-32` — Requesting Backup\n`10-41` — Beginning of Shift *(On Duty)*\n"
        "`10-42` — End of Shift *(Off Duty)*\n`10-50` — Vehicle Accident\n"
        "`10-71` — Requesting Supervisor\n`10-72` — Flagged Down\n"
        "`10-73` — Advise Status\n`10-97` — In Route\n`10-99` — Officer Down"))
    e.add_field(name="\u200b", value="\u200b", inline=False)
    e.add_field(name="🚨 Response Codes", inline=False, value=(
        "`Code 0` — Game Crash\n`Code 1` — No Lights and No Sirens\n"
        "`Code 2` — Lights Only\n`Code 3` — Lights and Sirens On\n`Code 4` — Scene Clear"))
    e.set_footer(text="Southwest Florida EMS")
    e.timestamp = datetime.now(EST)
    return e

# ── Ticket: Transcript ────────────────────────────────────────────────────────

async def send_transcript(channel: discord.TextChannel, opener: discord.Member):
    messages = []
    async for msg in channel.history(limit=500, oldest_first=True):
        ts      = msg.created_at.astimezone(EST).strftime("%Y-%m-%d %H:%M:%S EST")
        content = msg.content or ""
        for emb in msg.embeds:
            title  = emb.title or ""
            desc   = emb.description or ""
            fields = " | ".join(f"{f.name}: {f.value}" for f in emb.fields)
            content += f" [EMBED] {title} — {desc} {fields}".strip()
        if content:
            messages.append(f"[{ts}] {msg.author.display_name}: {content}")

    text         = "\n".join(messages) if messages else "No messages recorded."
    ticket_name  = channel.name
    raw          = text.encode("utf-8")

    embed = discord.Embed(title=f"📄 Transcript — #{ticket_name}", color=discord.Color.greyple(), timestamp=datetime.now(EST))
    embed.add_field(name="Opened by", value=opener.mention if opener else "Unknown", inline=True)
    embed.add_field(name="Channel",   value=f"#{ticket_name}",                       inline=True)
    embed.set_footer(text="Ticket closed")

    transcript_ch = channel.guild.get_channel(TRANSCRIPT_CHANNEL_ID)
    if opener:
        try:
            await opener.send(embed=embed, file=discord.File(fp=io.BytesIO(raw), filename=f"{ticket_name}-transcript.txt"))
        except discord.Forbidden:
            pass
    if transcript_ch:
        await transcript_ch.send(embed=embed, file=discord.File(fp=io.BytesIO(raw), filename=f"{ticket_name}-transcript.txt"))

# ── Ticket: Confirm close ─────────────────────────────────────────────────────

class ConfirmCloseView(discord.ui.View):
    def __init__(self, opener: discord.Member):
        super().__init__(timeout=60)
        self.opener = opener

    @discord.ui.button(label="✅ Confirm Close", style=discord.ButtonStyle.red, custom_id="confirm_close_yes")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Saving transcript and closing ticket...", ephemeral=True)
        try:
            await send_transcript(interaction.channel, self.opener)
        except Exception:
            print(f"[TRANSCRIPT ERROR]\n{traceback.format_exc()}")
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.grey, custom_id="confirm_close_no")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Close cancelled.", ephemeral=True)
        self.stop()

# ── Ticket: Close button ──────────────────────────────────────────────────────

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.red, custom_id="persistent:close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        opener = interaction.user
        async for msg in interaction.channel.history(oldest_first=True, limit=10):
            if not msg.author.bot:
                opener = msg.author
                break
        embed = discord.Embed(title="⚠️ Close Ticket?",
            description="Are you sure you want to close this ticket?\n\nA transcript will be saved and sent to the ticket opener before deletion.",
            color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, view=ConfirmCloseView(opener), ephemeral=True)

# ── Ticket: Incident report modal ─────────────────────────────────────────────

class IncidentReportModal(discord.ui.Modal, title="Incident Report"):
    reported_user = discord.ui.TextInput(label="Who are you reporting?", placeholder="Their username or display name", max_length=100)
    reason        = discord.ui.TextInput(label="Reason for report", style=discord.TextStyle.paragraph, max_length=1000)
    evidence      = discord.ui.TextInput(label="Evidence (links/screenshots)", style=discord.TextStyle.paragraph, required=False, max_length=500)

    def __init__(self, rank_role_id: int, rank_name: str):
        super().__init__()
        self.rank_role_id = rank_role_id
        self.rank_name    = rank_name

    async def on_submit(self, interaction: discord.Interaction):
        try:
            guild    = interaction.guild
            category = guild.get_channel(CAT_INCIDENT)
            is_lt_or_below = self.rank_role_id in LT_AND_BELOW_IDS
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user:   discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            if is_lt_or_below:
                r = guild.get_role(ROLE_LT_VIEWER)
                if r: overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            else:
                for rid in (ROLE_CAP_VIEW1, ROLE_CAP_VIEW2):
                    r = guild.get_role(rid)
                    if r: overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            channel = await guild.create_text_channel(name=f"incident-{interaction.user.name}", category=category, overwrites=overwrites)
            embed = discord.Embed(title="📝 Incident Report", color=discord.Color.orange(), timestamp=datetime.now(EST))
            embed.add_field(name="Opened by",     value=interaction.user.mention, inline=True)
            embed.add_field(name="Rank Reported", value=self.rank_name,           inline=True)
            embed.add_field(name="Reported User", value=self.reported_user.value, inline=False)
            embed.add_field(name="Reason",        value=self.reason.value,        inline=False)
            if self.evidence.value:
                embed.add_field(name="Evidence",  value=self.evidence.value,      inline=False)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text="Use the button below to close this ticket when resolved.")
            await channel.send(content=interaction.user.mention, embed=embed, view=CloseTicketView())
            await interaction.response.send_message(f"Your incident report has been opened: {channel.mention}", ephemeral=True)
        except Exception:
            err = traceback.format_exc()
            print(f"[INCIDENT MODAL ERROR]\n{err}")
            try:
                await interaction.response.send_message(f"Error:\n```{err[-1800:]}```", ephemeral=True)
            except Exception:
                pass

# ── Ticket: Rank selector ─────────────────────────────────────────────────────

class RankSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=name, value=str(rid)) for rid, name in RANKS]
        super().__init__(placeholder="Select the rank of the person you are reporting...",
            min_values=1, max_values=1, options=options, custom_id="persistent:rank_select")

    async def callback(self, interaction: discord.Interaction):
        rank_role_id = int(self.values[0])
        rank_name    = next(name for rid, name in RANKS if rid == rank_role_id)
        await interaction.response.send_modal(IncidentReportModal(rank_role_id, rank_name))

class RankSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(RankSelect())

# ── Ticket: Main dropdown ─────────────────────────────────────────────────────

class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="📋 General Support",  value="general",  description="Questions, issues, or general requests"),
            discord.SelectOption(label="📝 Incident Report",  value="incident", description="Report an incident involving a member"),
        ]
        super().__init__(placeholder="Select a ticket type...", min_values=1, max_values=1,
            options=options, custom_id="persistent:ticket_select")

    async def callback(self, interaction: discord.Interaction):
        try:
            if self.values[0] == "general":
                guild    = interaction.guild
                category = guild.get_channel(CAT_GENERAL)
                staff    = guild.get_role(ALLOWED_ROLE_ID)
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user:   discord.PermissionOverwrite(read_messages=True, send_messages=True),
                }
                if staff: overwrites[staff] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                channel = await guild.create_text_channel(name=f"support-{interaction.user.name}", category=category, overwrites=overwrites)
                embed = discord.Embed(title="📋 General Support",
                    description=f"Welcome {interaction.user.mention}!\n\nPlease describe your issue and a staff member will assist you shortly.",
                    color=discord.Color.blurple(), timestamp=datetime.now(EST))
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                embed.set_footer(text="Use the button below to close this ticket when resolved.")
                await channel.send(content=interaction.user.mention, embed=embed, view=CloseTicketView())
                await interaction.response.send_message(f"Your ticket has been opened: {channel.mention}", ephemeral=True)
            elif self.values[0] == "incident":
                await interaction.response.send_message("Select the **rank** of the person you are reporting:", view=RankSelectView(), ephemeral=True)
        except Exception:
            err = traceback.format_exc()
            print(f"[TICKET SELECT ERROR]\n{err}")
            try:
                await interaction.response.send_message(f"Error:\n```{err[-1800:]}```", ephemeral=True)
            except Exception:
                pass

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())

# ── Clock View ────────────────────────────────────────────────────────────────

class ClockView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Clock In", style=discord.ButtonStyle.green, custom_id="persistent:clock_in")
    async def clock_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            uid       = str(interaction.user.id)
            user_data = await get_user_clock(uid)
            if user_data["clocked_in_at"] is not None:
                await interaction.followup.send("You are already clocked in!", ephemeral=True)
                return
            user_data["clocked_in_at"] = datetime.now(EST).isoformat()
            await save_user_clock(uid, user_data)
            log_ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
            if log_ch:
                embed = discord.Embed(title="🟢 Clock In", description=f"{interaction.user.mention} clocked **in**.",
                    color=discord.Color.green(), timestamp=datetime.now(EST))
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                await log_ch.send(embed=embed)
            await interaction.followup.send("You have been **clocked in**!", ephemeral=True)
        except Exception:
            err = traceback.format_exc(); print(f"[CLOCK IN ERROR]\n{err}")
            try: await interaction.followup.send(f"Error:\n```{err[-1800:]}```", ephemeral=True)
            except Exception: pass

    @discord.ui.button(label="Clock Out", style=discord.ButtonStyle.red, custom_id="persistent:clock_out")
    async def clock_out(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            uid       = str(interaction.user.id)
            user_data = await get_user_clock(uid)
            if user_data["clocked_in_at"] is None:
                await interaction.followup.send("You are not clocked in!", ephemeral=True)
                return
            elapsed = (datetime.now(EST) - parse_dt(user_data["clocked_in_at"])).total_seconds()
            user_data["total"]         = user_data.get("total", 0.0) + elapsed
            user_data["clocked_in_at"] = None
            await save_user_clock(uid, user_data)
            log_ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
            if log_ch:
                embed = discord.Embed(title="🔴 Clock Out",
                    description=f"{interaction.user.mention} clocked **out**.\nSession: **{format_seconds(elapsed)}**\nTotal this week: **{format_seconds(user_data['total'])}**",
                    color=discord.Color.red(), timestamp=datetime.now(EST))
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                await log_ch.send(embed=embed)
            await interaction.followup.send(f"You have been **clocked out**!\nSession: **{format_seconds(elapsed)}**\nTotal this week: **{format_seconds(user_data['total'])}**", ephemeral=True)
        except Exception:
            err = traceback.format_exc(); print(f"[CLOCK OUT ERROR]\n{err}")
            try: await interaction.followup.send(f"Error:\n```{err[-1800:]}```", ephemeral=True)
            except Exception: pass

    @discord.ui.button(label="Check My Time", style=discord.ButtonStyle.blurple, custom_id="persistent:check_my_time")
    async def check_time_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            uid       = str(interaction.user.id)
            user_data = await get_user_clock(uid)
            total     = user_data.get("total", 0.0)
            if user_data["clocked_in_at"]:
                session = (datetime.now(EST) - parse_dt(user_data["clocked_in_at"])).total_seconds()
                total  += session
                status  = f"🟢 Currently clocked in (session: **{format_seconds(session)}**)"
            else:
                status = "🔴 Not clocked in"
            embed = discord.Embed(title="⏱️ Your Time This Week", color=discord.Color.blurple(), timestamp=datetime.now(EST))
            embed.add_field(name="Status",     value=status,                         inline=False)
            embed.add_field(name="Total Time", value=f"**{format_seconds(total)}**", inline=False)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            err = traceback.format_exc(); print(f"[CHECK TIME ERROR]\n{err}")
            try: await interaction.followup.send(f"Error:\n```{err[-1800:]}```", ephemeral=True)
            except Exception: pass

# ── LOA Approval View ─────────────────────────────────────────────────────────

class LOAApprovalView(discord.ui.View):
    def __init__(self, submitter_id: int, is_higher_up: bool):
        super().__init__(timeout=None)
        self.submitter_id = submitter_id
        self.is_higher_up = is_higher_up

    def can_action(self, interaction: discord.Interaction) -> bool:
        if self.is_higher_up:
            return any(r.id in {ROLE_CHIEF, ROLE_MED_DIR} for r in interaction.user.roles)
        higher_up_role = interaction.guild.get_role(ROLE_HIGHER_UP)
        return higher_up_role and any(r.position >= higher_up_role.position for r in interaction.user.roles)

    async def _respond(self, interaction: discord.Interaction, approved: bool):
        if not self.can_action(interaction):
            await interaction.response.send_message("You don't have permission to action this LOA.", ephemeral=True); return
        if interaction.user.id == self.submitter_id:
            await interaction.response.send_message("You cannot action your own LOA request.", ephemeral=True); return
        embed     = interaction.message.embeds[0]
        new_embed = embed.copy()
        label     = f"{'✅ Approved' if approved else '❌ Denied'} by {interaction.user.mention}"
        for i, field in enumerate(new_embed.fields):
            if field.name == "Status":
                new_embed.set_field_at(i, name="Status", value=label, inline=False); break
        new_embed.color = discord.Color.green() if approved else discord.Color.red()
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=new_embed, view=self)
        submitter = interaction.guild.get_member(self.submitter_id)
        if submitter:
            try:
                dm = discord.Embed(
                    title=f"{'✅ LOA Approved' if approved else '❌ LOA Denied'}",
                    description=f"Your Leave of Absence request has been **{'approved' if approved else 'denied'}** by {interaction.user.mention}.",
                    color=discord.Color.green() if approved else discord.Color.red(),
                    timestamp=datetime.now(EST))
                await submitter.send(embed=dm)
            except discord.Forbidden: pass

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green, custom_id="loa_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._respond(interaction, approved=True)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.red, custom_id="loa_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._respond(interaction, approved=False)

# ── Weekly Reset ──────────────────────────────────────────────────────────────

@tasks.loop(hours=1)
async def weekly_reset():
    now = datetime.now(EST)
    if now.weekday() == 6 and now.hour == 0:
        await reset_all_clock()
        print(f"[{now}] Weekly clock data reset.")

# ── Slash Commands ────────────────────────────────────────────────────────────

@tree.command(guild=G, name="role", description="Give a role to a member.")
@app_commands.describe(member="The member to give the role to", role="The role to give")
async def role_command(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    try:
        await member.add_roles(role)
        embed = discord.Embed(title="✅ Role Assigned", description=f"{role.mention} given to {member.mention}.",
            color=role.color if role.color.value else discord.Color.green())
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have permission to assign that role.", ephemeral=True)

@tree.command(guild=G, name="embedcreate", description="Post links in an embed to a channel.")
@app_commands.describe(channel="Target channel", title="Embed title", links="Space-separated links")
async def embedcreate_command(interaction: discord.Interaction, channel: discord.TextChannel, title: str, links: str):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    formatted = "\n".join(f"[Link {i+1}]({u})" for i, u in enumerate(links.split()))
    embed = discord.Embed(title=title, description=formatted, color=discord.Color.blurple(), timestamp=datetime.now(EST))
    embed.set_footer(text=f"Posted by {interaction.user.display_name}")
    try:
        await channel.send(embed=embed)
        await interaction.response.send_message(f"Embed posted in {channel.mention}!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I can't send messages in that channel.", ephemeral=True)

@tree.command(guild=G, name="clockinembed", description="Post the clock-in/out embed (clock-in channel only).")
async def clockinembed_command(interaction: discord.Interaction):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    if interaction.channel_id != CLOCKIN_CHANNEL_ID:
        await interaction.response.send_message("Only usable in the clock-in channel.", ephemeral=True); return
    msg = await interaction.channel.send(embed=build_clock_embed(), view=ClockView())
    await save_panel("clock", msg.channel.id, msg.id)
    await interaction.response.send_message("Clock panel posted!", ephemeral=True)

@tree.command(guild=G, name="ticketpanel", description="Post the support ticket panel.")
async def ticketpanel_command(interaction: discord.Interaction):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    ch = bot.get_channel(TICKET_CHANNEL_ID) or await bot.fetch_channel(TICKET_CHANNEL_ID)
    msg = await ch.send(embed=build_ticket_embed(), view=TicketView())
    await save_panel("ticket", ch.id, msg.id)
    await interaction.response.send_message(f"Ticket panel posted in {ch.mention}!", ephemeral=True)

@tree.command(guild=G, name="codespanel", description="Post the radio & response codes embed.")
async def codespanel_command(interaction: discord.Interaction):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    ch = bot.get_channel(CODES_CHANNEL_ID) or await bot.fetch_channel(CODES_CHANNEL_ID)
    msg = await ch.send(embed=build_codes_embed())
    await save_panel("codes", ch.id, msg.id)
    await interaction.response.send_message(f"Codes panel posted in {ch.mention}!", ephemeral=True)

@tree.command(guild=G, name="helppanel", description="Post the command reference embed.")
async def helppanel_command(interaction: discord.Interaction):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    ch = bot.get_channel(HELP_CHANNEL_ID) or await bot.fetch_channel(HELP_CHANNEL_ID)
    msg = await ch.send(embed=build_help_embed())
    await save_panel("help", ch.id, msg.id)
    await interaction.response.send_message(f"Help panel posted in {ch.mention}!", ephemeral=True)

@tree.command(guild=G, name="clockleaderboard", description="Top 10 members by hours this week.")
async def clockleaderboard_command(interaction: discord.Interaction):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    if interaction.channel_id != LOG_CHANNEL_ID:
        await interaction.response.send_message("Only usable in the log channel.", ephemeral=True); return
    now    = datetime.now(EST)
    docs   = await get_all_clock()
    totals = []
    for uid, total, clocked_in_at in docs:
        if clocked_in_at:
            total += (now - parse_dt(clocked_in_at)).total_seconds()
        totals.append((uid, total))
    totals.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="🏆 Clock Leaderboard — Top 10 This Week", color=discord.Color.gold(), timestamp=now)
    if not totals:
        embed.description = "No clock data recorded this week."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (uid, total) in enumerate(totals[:10]):
            member = interaction.guild.get_member(int(uid))
            name   = member.display_name if member else f"User {uid}"
            prefix = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{prefix} {name} — **{format_seconds(total)}**")
        embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed)

@tree.command(guild=G, name="checktime", description="Check a user's clocked hours for the week.")
@app_commands.describe(user="The user to check")
async def checktime_command(interaction: discord.Interaction, user: discord.Member):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    if interaction.channel_id != LOG_CHANNEL_ID:
        await interaction.response.send_message("Only usable in the log channel.", ephemeral=True); return
    uid       = str(user.id)
    user_data = await get_user_clock(uid)
    now       = datetime.now(EST)
    total     = user_data.get("total", 0.0)
    if user_data["clocked_in_at"]:
        session = (now - parse_dt(user_data["clocked_in_at"])).total_seconds()
        total  += session
        status  = f"🟢 Currently clocked in (session: **{format_seconds(session)}**)"
    else:
        status = "🔴 Not clocked in"
    embed = discord.Embed(title=f"⏱️ Time Check — {user.display_name}", color=discord.Color.blurple(), timestamp=now)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Status",               value=status,                        inline=False)
    embed.add_field(name="Total Time This Week", value=f"**{format_seconds(total)}**", inline=False)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

@tree.command(guild=G, name="loarequest", description="Submit a Leave of Absence request.")
@app_commands.describe(start_date="Start date (e.g. 07/14/2026)", end_date="End date (e.g. 07/21/2026)", reason="Reason for your LOA")
async def loarequest_command(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    if interaction.channel_id != LOA_CHANNEL_ID:
        await interaction.response.send_message("This command can only be used in the LOA channel.", ephemeral=True); return
    higher_up_role = interaction.guild.get_role(ROLE_HIGHER_UP)
    is_higher_up   = higher_up_role and any(r.position >= higher_up_role.position for r in interaction.user.roles)
    embed = discord.Embed(title="📋 Leave of Absence Request", color=discord.Color.yellow(), timestamp=datetime.now(EST))
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.add_field(name="Member",     value=interaction.user.mention, inline=True)
    embed.add_field(name="Start Date", value=start_date,               inline=True)
    embed.add_field(name="End Date",   value=end_date,                 inline=True)
    embed.add_field(name="Reason",     value=reason,                   inline=False)
    embed.add_field(name="Status",     value="⏳ Pending",              inline=False)
    embed.set_footer(text=f"Submitted by {interaction.user.display_name} • {'Higher-Up LOA — Chiefs only' if is_higher_up else 'Regular LOA'}")
    await interaction.response.send_message(embed=embed, view=LOAApprovalView(interaction.user.id, is_higher_up))

@tree.command(guild=G, name="rename", description="Rename a ticket channel.")
@app_commands.describe(name="New name for the channel")
async def rename_command(interaction: discord.Interaction, name: str):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or ch.category_id not in (CAT_GENERAL, CAT_INCIDENT):
        await interaction.response.send_message("This command can only be used inside a ticket channel.", ephemeral=True); return
    safe = name.lower().replace(" ", "-")
    try:
        old = ch.name
        await ch.edit(name=safe, reason=f"Renamed by {interaction.user}")
        await interaction.response.send_message(f"Channel renamed from `{old}` to `{safe}`.")
    except discord.Forbidden:
        await interaction.response.send_message("I don't have permission to rename this channel.", ephemeral=True)

@tree.command(guild=G, name="higherup", description="Ping leadership to review this ticket.")
async def higherup_command(interaction: discord.Interaction):
    if not has_allowed_role(interaction):
        await interaction.response.send_message("You don't have permission.", ephemeral=True); return
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or ch.category_id not in (CAT_GENERAL, CAT_INCIDENT):
        await interaction.response.send_message("This command can only be used inside a ticket channel.", ephemeral=True); return
    notify_ch = interaction.guild.get_channel(HIGHERUP_NOTIFY_ID)
    if not notify_ch:
        await interaction.response.send_message("Could not find the notification channel.", ephemeral=True); return
    ping_roles = [interaction.guild.get_role(rid) for rid in (1526407839655137299, ROLE_CHIEF, ROLE_MED_DIR)]
    mentions   = " ".join(r.mention for r in ping_roles if r)
    embed = discord.Embed(title="🔔 Ticket Needs Attention",
        description=f"A ticket requires your review.\n\n**Channel:** {ch.mention}\n**Requested by:** {interaction.user.mention}",
        color=discord.Color.orange(), timestamp=datetime.now(EST))
    await notify_ch.send(content=mentions, embed=embed)
    await interaction.response.send_message(f"Leadership has been notified in {notify_ch.mention}.", ephemeral=True)

# ── Startup ───────────────────────────────────────────────────────────────────

async def reattach_panel(name: str, build_embed, view=None):
    doc = await get_panel(name)
    if not doc:
        return
    try:
        ch  = bot.get_channel(doc["channel_id"]) or await bot.fetch_channel(doc["channel_id"])
        msg = await ch.fetch_message(doc["message_id"])
        await msg.edit(embed=build_embed(), view=view)
        print(f"[on_ready] {name} panel re-attached")
    except Exception:
        print(f"[on_ready] {name} panel error:\n{traceback.format_exc()}")

@bot.event
async def on_member_join(member: discord.Member):
    role = member.guild.get_role(1526407839629709357)
    if role:
        try:
            await member.add_roles(role, reason="Auto-role on join")
        except discord.Forbidden:
            print(f"[on_member_join] Missing permission to assign role to {member}")

@bot.event
async def on_ready():
    bot.add_view(ClockView())
    bot.add_view(CloseTicketView())
    bot.add_view(TicketView())
    bot.add_view(LOAApprovalView(submitter_id=0, is_higher_up=False))
    bot.add_view(LOAApprovalView(submitter_id=0, is_higher_up=True))

    await reattach_panel("clock",  build_clock_embed,  ClockView())
    await reattach_panel("ticket", build_ticket_embed, TicketView())
    await reattach_panel("help",   build_help_embed)
    await reattach_panel("codes",  build_codes_embed)

    await tree.sync(guild=G)
    weekly_reset.start()
    print(f"Logged in as {bot.user} (ID: {bot.user.id}) — MongoDB connected")

bot.run(BOT_TOKEN)
