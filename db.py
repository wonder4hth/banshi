"""SQLite persistence layer for 伴时 (BanShi).

Kept deliberately dependency-free (stdlib sqlite3 only) so the whole app
runs with nothing but Flask installed.
"""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# BANSHI_DATA_DIR lets the desktop build keep data in %APPDATA% instead of
# next to the code (which, inside a PyInstaller exe, is a throwaway temp dir).
DATA_DIR = Path(os.environ.get("BANSHI_DATA_DIR") or Path(__file__).parent)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "banshi.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    avatar               TEXT NOT NULL,
    persona              TEXT NOT NULL DEFAULT '',  -- short blurb shown on the agent picker
    profile              TEXT NOT NULL DEFAULT '',  -- full character sheet, only fed to the LLM
    speaking_style       TEXT NOT NULL,
    distraction_attitude TEXT NOT NULL,
    engine               TEXT NOT NULL DEFAULT 'llm',  -- unused since the template engine option was removed; kept so old dbs need no rebuild
    deleted_at           TEXT,  -- soft delete: row kept so story_logs/tasks survive
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id     INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    task_name    TEXT NOT NULL,
    task_desc    TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'in_progress',
    outcome      TEXT,
    note         TEXT NOT NULL DEFAULT '',
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    task_id    INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    entry_date TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS letters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    letter_date TEXT NOT NULL,  -- local date the letter is about
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'daily',  -- 'daily' (scheduled) | 'debug' (test trigger)
    read_at     TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    sender     TEXT NOT NULL,  -- 'user' | 'agent'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_letters(conn: sqlite3.Connection) -> None:
    """Early `letters` tables had UNIQUE(user_id, letter_date), which also blocked
    debug-triggered letters. Rebuild those without it; one-daily-letter-per-day
    is now a partial unique index that only covers source = 'daily'."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='letters'").fetchone()[0]
    if "UNIQUE" in sql:
        conn.executescript("""
            ALTER TABLE letters RENAME TO letters_old;
            CREATE TABLE letters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                letter_date TEXT NOT NULL,
                content     TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'daily',
                read_at     TEXT,
                created_at  TEXT NOT NULL
            );
            INSERT INTO letters (id, user_id, agent_id, letter_date, content, read_at, created_at)
                SELECT id, user_id, agent_id, letter_date, content, read_at, created_at FROM letters_old;
            DROP TABLE letters_old;
        """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS letters_one_daily ON letters(user_id, letter_date) "
        "WHERE source = 'daily'"
    )


def get_or_create_local_user(conn: sqlite3.Connection) -> sqlite3.Row:
    """Single-user (desktop) mode: the oldest account is "the" user; create one on first run."""
    row = conn.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    if row is None:
        # unusable password hash: this account can't be logged into from the web version
        conn.execute(
            "INSERT INTO users (email, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            ("local@banshi", "我", "!", now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    return row


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA)
    # Forward-compatible migration for dbs created before the `engine` column existed.
    try:
        conn.execute("ALTER TABLE agents ADD COLUMN engine TEXT NOT NULL DEFAULT 'llm'")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE tasks ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE agents ADD COLUMN deleted_at TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        conn.execute("ALTER TABLE agents ADD COLUMN profile TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # column already exists
    _migrate_letters(conn)
    conn.commit()
    conn.close()
