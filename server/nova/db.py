"""SQLite hafıza: görevler, notlar, sohbet geçmişi, onay bekleyen işlemler."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, project TEXT, due TEXT,
  status TEXT NOT NULL DEFAULT 'open', created REAL NOT NULL, done_at REAL);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, project TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pending_actions (
  id INTEGER PRIMARY KEY, tool TEXT NOT NULL, args TEXT NOT NULL, summary TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', result TEXT, created REAL NOT NULL);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def rows(sql: str, *params) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def execute(sql: str, *params) -> int:
    with conn() as c:
        return c.execute(sql, params).lastrowid


# --- sohbet geçmişi ---
def add_message(role: str, content: str) -> None:
    execute("INSERT INTO messages(role, content, created) VALUES (?,?,?)", role, content, time.time())


def recent_messages(n: int, minutes: float | None = None) -> list[dict]:
    """Son n mesaj; minutes verilirse sadece o kadar dakika içindekiler (eski sohbetler bağlamı kirletmesin)."""
    since = time.time() - minutes * 60 if minutes else 0
    r = rows("SELECT role, content FROM messages WHERE created >= ? ORDER BY id DESC LIMIT ?", since, n)
    return list(reversed(r))


# --- onay bekleyen işlemler ---
def create_pending(tool: str, args: dict, summary: str) -> int:
    return execute("INSERT INTO pending_actions(tool, args, summary, created) VALUES (?,?,?,?)",
                   tool, json.dumps(args, ensure_ascii=False), summary, time.time())


def get_pending(action_id: int) -> dict | None:
    r = rows("SELECT * FROM pending_actions WHERE id=?", action_id)
    if not r:
        return None
    r[0]["args"] = json.loads(r[0]["args"])
    return r[0]


def list_pending() -> list[dict]:
    return rows("SELECT id, tool, summary, created FROM pending_actions WHERE status='pending' ORDER BY id")


def finish_pending(action_id: int, status: str, result: str = "") -> None:
    execute("UPDATE pending_actions SET status=?, result=? WHERE id=?", status, result, action_id)
