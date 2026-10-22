import os
import asyncio
import logging
import sqlite3
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

# Required environment variables:
# DISCORD_TOKEN, DISCORD_CHANNEL_ID
TZ = ZoneInfo("Asia/Seoul")
TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
DB_PATH = os.environ.get("DB_PATH", "attendance.sqlite3")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("roll-attendance")


def now_kst():
    return datetime.now(TZ)


def today_key():
    return now_kst().date().isoformat()


def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily (
            day TEXT PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            result_sent INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            day TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('O','X')),
            PRIMARY KEY(day, user_id)
        )
    """)
    conn.commit()
    return conn


def get_daily(day):
    with connect_db() as conn:
        return conn.execute(
            "SELECT channel_id, message_id, result_sent FROM daily WHERE day=?",
            (day,)
        ).fetchone()


def set_message(day, channel_id, message_id):
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO daily(day, channel_id, message_id, result_sent) VALUES(?,?,?,0) "
            "ON CONFLICT(day) DO UPDATE SET channel_id=excluded.channel_id, message_id=excluded.message_id",
            (day, channel_id, message_id)
        )
        conn.commit()


def set_vote(day, user_id, status):
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO votes(day,user_id,status) VALUES(?,?,?) "
            "ON CONFLICT(day,user_id) DO UPDATE SET status=excluded.status",
            (day, user_id, status)
        )
        conn.commit()


def get_counts(day):
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM votes WHERE day=? GROUP BY status", (day,)
        ).fetchall()
    counts = {"O": 0, "X": 0}
    counts.update(dict(rows))
    return counts


def get_names(day, status, guild):
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM votes WHERE day=? AND status=? ORDER BY user_id",
            (day, status)
        ).fetchall()
    names = []
    for (user_id,) in rows:
        member = guild.get_member(user_id) if guild else None
        names.append(member.display_name if member else f"<@{user_id}>")
    return names


def mark_result_sent(day):
    # Claim the result before sending to avoid duplicate announcements after restarts.
    # This favors at-most-once announcements; a process crash between claim and send
    # can mean the announcement is missed.
    with connect_db() as conn:
        cur = conn.execute(
            "UPDATE daily SET result_sent=1 WHERE day=? AND result_sent=0",
            (day,)
        )
        conn.commit()
        return cur.rowcount == 1


class AttendanceView(discord.ui.View):
    def __init__(self, day: str):
        super().__init__(timeout=None)
        self.day = day
        self.add_item(AttendanceButton(day, "O"))
        self.add_item(AttendanceButton(day, "X"))


class AttendanceButton(discord.ui.Button):
    def __init__(self, day: str, status: str):
        self.day = day
        self.status = status
        label = "O 참석 가능" if status == "O" else "X 참석 불가"
        style = discord.ButtonStyle.success if status == "O" else discord.ButtonStyle.danger
        super().__init__(
            label=label,
            style=style,
            custom_id=f"rollattend:{day}:{status}"
        )

    async def callback(self, interaction: discord.Interaction):
        if now_kst().date().isoformat() != self.day or now_kst().time() >= time(19, 0):
            await interaction.response.send_message(
                "오늘 출석 등록 시간이 지났습니다. 출석은 오후 7시까지 가능합니다.",
                ephemeral=True
            )
            return

        set_vote(self.day, interaction.user.id, self.status)
        counts = get_counts(self.day)
        await interaction.response.send_message(
            f"출석 상태를 **{'참석 가능(O)' if self.status == 'O' else '참석 불가(X)'}**로 등록했습니다. "
            f"(현재 참석 가능 {counts['O']}명)",
            ephemeral=True
        )
        # Update the visible count in the attendance message.
        try:
            channel = interaction.client.get_channel(CHANNEL_ID)
            daily = get_daily(self.day)
            if channel and daily and daily[1]:
                msg = await channel.fetch_message(daily[1])
                await msg.edit(embed=make_attendance_embed(self.day), view=AttendanceView(self.day))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.exception("Could not refresh attendance message")


def make_attendance_embed(day: str):
    counts = get_counts(day)
    embed = discord.Embed(
        title=f"🎮 롤 내전 출석부 · {day}",
        description=(
            "오늘 내전에 참석할 수 있다면 **O 참석 가능**, 불가능하다면 **X 참석 불가**를 눌러주세요.\n"
            "아무 버튼도 누르지 않으면 **미응답**으로 처리됩니다.\n\n"
            "⏰ 출석 마감: 매일 오후 7시 (한국 시간)\n"
            "👥 참석 가능 인원 5명 이상이면 진행, 5명 미만이면 휴일입니다."
        ),
        color=discord.Color.blurple()
    )
    embed.add_field(name="🟢 참석 가능(O)", value=f"**{counts['O']}명**", inline=True)
    embed.add_field(name="🔴 참석 불가(X)", value=f"**{counts['X']}명**", inline=True)
    embed.add_field(name="⚪ 미응답", value="버튼을 누르지 않은 서버 멤버", inline=True)
    embed.set_footer(text="버튼을 다시 누르면 출석 상태를 변경할 수 있습니다.")
    return embed


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def get_target_channel():
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(CHANNEL_ID)
    return channel


async def ensure_daily_post(day: str):
    existing = get_daily(day)
    channel = await get_target_channel()
    if existing and existing[1]:
        try:
            await channel.fetch_message(existing[1])
            return
        except (discord.NotFound, discord.HTTPException):
            log.warning("Saved attendance message missing; recreating it.")
    msg = await channel.send(embed=make_attendance_embed(day), view=AttendanceView(day))
    set_message(day, channel.id, msg.id)
    log.info("Posted attendance for %s", day)


async def announce_result(day: str):
    daily = get_daily(day)
    if not daily or not daily[1] or daily[2]:
        return
    # Ensure only one worker/process claims this date.
    if not mark_result_sent(day):
        return

    counts = get_counts(day)
    channel = await get_target_channel()
    if counts["O"] >= 5:
        title = "✅ 오늘 롤 내전 진행!"
        description = f"참석 가능 인원 **{counts['O']}명**으로 5명을 충족했습니다. 오늘 내전을 진행합니다!"
        color = discord.Color.green()
    else:
        title = "💤 오늘은 휴일입니다"
        description = (
            f"참석 가능 인원은 **{counts['O']}명**으로, 기준인 5명에 미달했습니다.\n"
            "오늘 내전은 쉬어갑니다!"
        )
        color = discord.Color.orange()

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(name="참석 가능(O)", value=str(counts["O"]), inline=True)
    embed.add_field(name="참석 불가(X)", value=str(counts["X"]), inline=True)
    embed.add_field(name="미응답", value="출석 버튼을 누르지 않은 인원", inline=True)
    await channel.send(embed=embed)
    log.info("Announced result for %s", day)


async def scheduler_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = now_kst()
        day = now.date().isoformat()

        try:
            # Automatically create today's attendance shortly after midnight.
            if now.time() >= time(0, 5) and get_daily(day) is None:
                await ensure_daily_post(day)

            # If the bot was offline at 19:00, post the result on next startup/check.
            if now.time() >= time(19, 0):
                daily = get_daily(day)
                if daily is None:
                    await ensure_daily_post(day)
                await announce_result(day)
        except Exception:
            log.exception("Scheduler iteration failed")

        # Check periodically; database state prevents duplicate daily posts/results.
        await asyncio.sleep(30)


@bot.event
async def on_ready():
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    # Re-register today's persistent buttons after restart.
    day = today_key()
    bot.add_view(AttendanceView(day))
    if now_kst().time() >= time(19, 0):
        bot.loop.create_task(ensure_daily_post(day))
        bot.loop.create_task(announce_result(day))
    if not getattr(bot, "_scheduler_started", False):
        bot._scheduler_started = True
        bot.loop.create_task(scheduler_loop())


@bot.event
async def setup_hook():
    # Restore persistent views for the current date; older messages are closed at 19:00.
    bot.add_view(AttendanceView(today_key()))


@bot.command(name="출석부재생성")
@commands.has_permissions(administrator=True)
async def recreate_attendance(ctx):
    """Admin-only manual recovery command."""
    day = today_key()
    await ensure_daily_post(day)
    await ctx.reply("오늘 출석부를 확인했습니다.", mention_author=False)


@recreate_attendance.error
async def recreate_attendance_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("이 명령어는 서버 관리자만 사용할 수 있습니다.", mention_author=False)
    else:
        log.exception("Command failed", exc_info=error)
        await ctx.reply("출석부 생성 중 오류가 발생했습니다. 봇 로그를 확인해주세요.", mention_author=False)


if __name__ == "__main__":
    connect_db().close()
    bot.run(TOKEN)
