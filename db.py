"""SQLite persistence layer for 伴时 (BanShi).

Kept deliberately dependency-free (stdlib sqlite3 only) so the whole app
runs with nothing but Flask installed.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "banshi.db"

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
    persona              TEXT NOT NULL DEFAULT '',
    speaking_style       TEXT NOT NULL,
    distraction_attitude TEXT NOT NULL,
    engine               TEXT NOT NULL DEFAULT 'llm',  -- 'llm' (local Ollama) | 'template'
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


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA)
    # Forward-compatible migration for dbs created before the `engine` column existed.
    try:
        conn.execute("ALTER TABLE agents ADD COLUMN engine TEXT NOT NULL DEFAULT 'llm'")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    conn.close()
