import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

DATABASE_PATH = os.getenv("DATABASE_PATH", "newsletter.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                confirmed INTEGER NOT NULL DEFAULT 0,
                token TEXT NOT NULL,
                subscribed_at TEXT NOT NULL,
                confirmed_at TEXT
            )
            """
        )


def create_or_update_subscriber(email: str, token: str) -> dict[str, Any]:
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM subscribers WHERE email = ?", (email,)
        ).fetchone()
        now = _now_iso()

        if existing is None:
            conn.execute(
                """
                INSERT INTO subscribers (email, confirmed, token, subscribed_at)
                VALUES (?, 0, ?, ?)
                """,
                (email, token, now),
            )
            return {"status": "created"}

        if existing["confirmed"]:
            return {"status": "already_confirmed"}

        conn.execute(
            "UPDATE subscribers SET token = ?, subscribed_at = ? WHERE email = ?",
            (token, now, email),
        )
        return {"status": "updated"}


def confirm_by_token(token: str) -> bool:
    with get_connection() as conn:
        subscriber = conn.execute(
            "SELECT * FROM subscribers WHERE token = ?", (token,)
        ).fetchone()
        if subscriber is None:
            return False

        conn.execute(
            "UPDATE subscribers SET confirmed = 1, confirmed_at = ? WHERE id = ?",
            (_now_iso(), subscriber["id"]),
        )
        return True


def unsubscribe_by_token(token: str) -> bool:
    with get_connection() as conn:
        subscriber = conn.execute(
            "SELECT * FROM subscribers WHERE token = ?", (token,)
        ).fetchone()
        if subscriber is None:
            return False

        conn.execute(
            "UPDATE subscribers SET confirmed = 0 WHERE id = ?", (subscriber["id"],),
        )
        return True


def list_subscribers() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, email, confirmed, token, subscribed_at, confirmed_at
            FROM subscribers
            ORDER BY subscribed_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def list_confirmed_subscribers() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, email, confirmed, token, subscribed_at, confirmed_at
            FROM subscribers
            WHERE confirmed = 1
            ORDER BY subscribed_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]
