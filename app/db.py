"""Lokaler Chat-Verlauf in SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

from .config import data_dir

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(data_dir() / "chats.db", check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                session_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                attachments TEXT NOT NULL DEFAULT '[]',
                activity TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, created_at);
            """
        )
    return _conn


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for key in ("attachments", "activity"):
        if key in d:
            d[key] = json.loads(d[key])
    return d


def create_chat(title: str = "Neuer Chat") -> dict[str, Any]:
    now = time.time()
    cid = uuid.uuid4().hex
    with _lock:
        conn().execute(
            "INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)", (cid, title, now, now)
        )
        conn().commit()
    return get_chat(cid)  # type: ignore[return-value]


def list_chats(query: str = "") -> list[dict[str, Any]]:
    with _lock:
        if query:
            like = f"%{query}%"
            rows = conn().execute(
                """SELECT DISTINCT c.* FROM chats c LEFT JOIN messages m ON m.chat_id = c.id
                   WHERE c.title LIKE ? OR m.content LIKE ? ORDER BY c.updated_at DESC""",
                (like, like),
            ).fetchall()
        else:
            rows = conn().execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_chat(chat_id: str) -> dict[str, Any] | None:
    with _lock:
        return _row(conn().execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone())


def update_chat(chat_id: str, **fields: Any) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("title", "session_id")}
    if not allowed:
        return
    allowed["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in allowed)
    with _lock:
        conn().execute(f"UPDATE chats SET {cols} WHERE id = ?", (*allowed.values(), chat_id))
        conn().commit()


def delete_chat(chat_id: str) -> None:
    with _lock:
        conn().execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn().commit()


def add_message(
    chat_id: str, role: str, content: str, attachments: list | None = None, activity: list | None = None
) -> dict[str, Any]:
    mid = uuid.uuid4().hex
    now = time.time()
    with _lock:
        conn().execute(
            "INSERT INTO messages (id, chat_id, role, content, attachments, activity, created_at) VALUES (?,?,?,?,?,?,?)",
            (mid, chat_id, role, content, json.dumps(attachments or []), json.dumps(activity or []), now),
        )
        conn().execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        conn().commit()
    return {"id": mid, "chat_id": chat_id, "role": role, "content": content,
            "attachments": attachments or [], "activity": activity or [], "created_at": now}


def list_messages(chat_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at", (chat_id,)
        ).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]
