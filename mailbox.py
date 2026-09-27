"""Daily letters (信箱).

Once a day, at LETTER_TIME (server local time), the non-deleted agent that
kept the user company the most that day writes them a letter.

"Company" = actual focus time on tasks that ended that day; ties (including
the all-zero case) are broken by how many messages the user sent that agent
that day. A day with no activity at all gets no letter.

A background thread checks every minute. If the app wasn't running at
LETTER_TIME, the next check catches up on the most recent due day only.
"""
import os
import threading
import time as _time
from datetime import date, datetime, time, timedelta, timezone

import db
import dialogue
import llm_engine

_h, _m = os.environ.get("BANSHI_LETTER_TIME", "21:00").split(":")
LETTER_TIME = time(int(_h), int(_m))
CHECK_INTERVAL = 60  # seconds
HISTORY_LIMIT = 20   # last ~10 rounds (both sides)


def due_date(now: datetime | None = None) -> date:
    """The latest local day whose letter should already exist."""
    now = now or datetime.now()
    return now.date() if now.time() >= LETTER_TIME else now.date() - timedelta(days=1)


def _utc_window(day: date):
    """[start, end) of a local calendar day, as UTC ISO strings comparable to created_at."""
    start = datetime.combine(day, time.min).astimezone()
    end = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat())


def pick_companion(conn, user_id: int, day: date):
    """Return (agent_row, focus_minutes, task_names, message_count) or None."""
    start, end = _utc_window(day)
    agents = conn.execute(
        "SELECT * FROM agents WHERE user_id = ? AND deleted_at IS NULL", (user_id,)
    ).fetchall()
    best = None
    for agent in agents:
        tasks = conn.execute(
            """SELECT task_name, duration_min, started_at, ended_at FROM tasks
               WHERE user_id = ? AND agent_id = ? AND status != 'in_progress'
                 AND ended_at >= ? AND ended_at < ? ORDER BY id""",
            (user_id, agent["id"], start, end),
        ).fetchall()
        focus_sec = 0
        for t in tasks:
            # actual time spent, capped at the planned duration (gave_up ends early)
            spent = (datetime.fromisoformat(t["ended_at"]) - datetime.fromisoformat(t["started_at"])).total_seconds()
            focus_sec += max(0, min(spent, t["duration_min"] * 60))
        msg_count = conn.execute(
            """SELECT COUNT(*) FROM messages WHERE user_id = ? AND agent_id = ? AND sender = 'user'
               AND created_at >= ? AND created_at < ?""",
            (user_id, agent["id"], start, end),
        ).fetchone()[0]
        score = (focus_sec, msg_count)
        if score == (0, 0):
            continue
        if best is None or score > best[0]:
            best = (score, agent, round(focus_sec / 60), [t["task_name"] for t in tasks], msg_count)
    return best[1:] if best else None


def write_letter(conn, user_id: int, day: date) -> bool:
    """Generate and store the daily letter for (user, day) if due and not yet written."""
    if conn.execute(
        "SELECT 1 FROM letters WHERE user_id = ? AND letter_date = ? AND source = 'daily'",
        (user_id, day.isoformat()),
    ).fetchone():
        return False
    picked = pick_companion(conn, user_id, day)
    if picked is None:
        return False
    _compose_and_store(conn, user_id, day, *picked, source="daily")
    return True


# Test trigger: a task note ending with this word makes today's letter get
# written right away on delivery, with no daily limit. Remove before release.
DEBUG_TRIGGER = "banshi"


def write_debug_letter(conn, user_id: int, fallback_agent) -> None:
    """Write today's letter immediately, ignoring LETTER_TIME and the daily limit."""
    day = datetime.now().date()
    picked = pick_companion(conn, user_id, day) or (fallback_agent, 0, [], 0)
    _compose_and_store(conn, user_id, day, *picked, source="debug")


def _compose_and_store(conn, user_id, day, agent, focus_minutes, task_names, msg_count, source):
    activity = dialogue.describe_day(focus_minutes, task_names, msg_count)
    history = list(reversed(conn.execute(
        "SELECT sender, content FROM messages WHERE agent_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
        (agent["id"], user_id, HISTORY_LIMIT),
    ).fetchall()))
    content = llm_engine.generate_letter(agent, history, activity)
    # OR IGNORE: the partial unique index on daily letters guards against a double write
    conn.execute(
        """INSERT OR IGNORE INTO letters (user_id, agent_id, letter_date, content, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, agent["id"], day.isoformat(), content, source, db.now_iso()),
    )
    conn.commit()


def run_due_letters(now: datetime | None = None) -> None:
    day = due_date(now)
    conn = db.get_db()
    try:
        for (user_id,) in conn.execute("SELECT id FROM users").fetchall():
            try:
                write_letter(conn, user_id, day)
            except Exception as e:  # one bad row shouldn't stop everyone else's letters
                print(f"[mailbox] letter for user {user_id} failed: {e}")
    finally:
        conn.close()


def start_scheduler() -> None:
    def loop():
        while True:
            try:
                run_due_letters()
            except Exception as e:
                print(f"[mailbox] scheduler error: {e}")
            _time.sleep(CHECK_INTERVAL)

    threading.Thread(target=loop, name="mailbox-scheduler", daemon=True).start()
