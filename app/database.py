import logging
import os
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

DATABASE_PATH = os.getenv("DATABASE_PATH", "newsletter.db")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                confirmed INTEGER NOT NULL DEFAULT 0,
                token TEXT NOT NULL,
                token_created_at TEXT NOT NULL DEFAULT '',
                subscribed_at TEXT NOT NULL,
                confirmed_at TEXT,
                tags TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS newsletters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                body_text TEXT,
                body_html TEXT,
                template TEXT NOT NULL DEFAULT 'minimal',
                status TEXT NOT NULL DEFAULT 'draft',
                recipient_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                scheduled_at TEXT,
                sent_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                newsletter_id INTEGER NOT NULL,
                subscriber_id INTEGER NOT NULL,
                event TEXT NOT NULL,
                url TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (newsletter_id) REFERENCES newsletters(id),
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                newsletter_id INTEGER NOT NULL,
                subscriber_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (newsletter_id) REFERENCES newsletters(id),
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                events TEXT NOT NULL DEFAULT 'all',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        # Migrate: add columns that may not exist in older DBs
        _migrate(conn)
    logger.info("Database initialized at %s", DATABASE_PATH)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that were introduced after initial schema."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(subscribers)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("tags", "ALTER TABLE subscribers ADD COLUMN tags TEXT NOT NULL DEFAULT ''"),
        ("notes", "ALTER TABLE subscribers ADD COLUMN notes TEXT NOT NULL DEFAULT ''"),
        ("token_created_at", "ALTER TABLE subscribers ADD COLUMN token_created_at TEXT NOT NULL DEFAULT ''"),
    ]
    for col, sql in migrations:
        if col not in existing:
            conn.execute(sql)


# ---------------------------------------------------------------------------
# Subscribers
# ---------------------------------------------------------------------------

TOKEN_EXPIRY_HOURS = 48


def create_or_update_subscriber(email: str, token: str) -> dict[str, Any]:
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT * FROM subscribers WHERE email = ?", (email,)
        ).fetchone()
        now = _now_iso()

        if existing is None:
            conn.execute(
                """
                INSERT INTO subscribers (email, confirmed, token, token_created_at, subscribed_at)
                VALUES (?, 0, ?, ?, ?)
                """,
                (email, token, now, now),
            )
            logger.info("New subscriber created: %s", email)
            return {"status": "created"}

        if existing["confirmed"]:
            return {"status": "already_confirmed"}

        conn.execute(
            "UPDATE subscribers SET token = ?, token_created_at = ?, subscribed_at = ? WHERE email = ?",
            (token, now, now, email),
        )
        logger.info("Subscriber token refreshed: %s", email)
        return {"status": "updated"}


def confirm_by_token(token: str) -> bool:
    with get_connection() as conn:
        subscriber = conn.execute(
            "SELECT * FROM subscribers WHERE token = ?", (token,)
        ).fetchone()
        if subscriber is None:
            return False

        # Check token expiry
        token_created = subscriber["token_created_at"]
        if token_created:
            try:
                created_dt = datetime.fromisoformat(token_created)
                age_hours = (datetime.now(UTC) - created_dt).total_seconds() / 3600
                if age_hours > TOKEN_EXPIRY_HOURS:
                    logger.warning("Expired confirmation token for subscriber %s", subscriber["email"])
                    return False
            except (ValueError, TypeError):
                pass

        now = _now_iso()
        new_token = secrets.token_urlsafe(32)
        conn.execute(
            "UPDATE subscribers SET confirmed = 1, confirmed_at = ?, token = ?, token_created_at = ? WHERE id = ?",
            (now, new_token, now, subscriber["id"]),
        )
        logger.info("Subscriber confirmed: %s", subscriber["email"])
        return True


def unsubscribe_by_token(token: str) -> bool:
    with get_connection() as conn:
        subscriber = conn.execute(
            "SELECT * FROM subscribers WHERE token = ?", (token,)
        ).fetchone()
        if subscriber is None:
            return False

        now = _now_iso()
        new_token = secrets.token_urlsafe(32)
        conn.execute(
            "UPDATE subscribers SET confirmed = 0, token = ?, token_created_at = ? WHERE id = ?",
            (new_token, now, subscriber["id"]),
        )
        logger.info("Subscriber unsubscribed: %s", subscriber["email"])
        return True


def list_subscribers(
    search: str = "",
    confirmed_only: bool = False,
    page: int = 1,
    per_page: int = 50,
    tag: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, total_count) with pagination and optional filters."""
    with get_connection() as conn:
        conditions: list[str] = []
        params: list[Any] = []
        if confirmed_only:
            conditions.append("confirmed = 1")
        if search:
            conditions.append("email LIKE ?")
            params.append(f"%{search}%")
        if tag:
            conditions.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{tag},%")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        count_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM subscribers {where}", params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""
            SELECT id, email, confirmed, token, subscribed_at, confirmed_at, tags, notes
            FROM subscribers
            {where}
            ORDER BY subscribed_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()

    return [dict(row) for row in rows], total


def list_confirmed_subscribers() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, email, confirmed, token, subscribed_at, confirmed_at, tags
            FROM subscribers
            WHERE confirmed = 1
            ORDER BY subscribed_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_subscriber(subscriber_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, email, confirmed, token, subscribed_at, confirmed_at, tags, notes FROM subscribers WHERE id = ?",
            (subscriber_id,),
        ).fetchone()
    return dict(row) if row else None


def get_subscriber_by_email(email: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, email, confirmed, token, subscribed_at, confirmed_at, tags, notes FROM subscribers WHERE email = ?",
            (email,),
        ).fetchone()
    return dict(row) if row else None


def delete_subscriber(subscriber_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM subscribers WHERE id = ?", (subscriber_id,))
        if cur.rowcount > 0:
            logger.info("Subscriber %d deleted", subscriber_id)
            return True
    return False


def add_subscriber_manual(email: str, tags: str = "", notes: str = "") -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    now = _now_iso()
    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO subscribers (email, confirmed, token, token_created_at, subscribed_at, confirmed_at, tags, notes)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (email, token, now, now, now, tags, notes),
            )
        except sqlite3.IntegrityError:
            return {"status": "duplicate"}
    logger.info("Subscriber added manually: %s", email)
    return {"status": "created"}


def update_subscriber_tags(subscriber_id: int, tags: str) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE subscribers SET tags = ? WHERE id = ?", (tags, subscriber_id)
        )
        return cur.rowcount > 0


def update_subscriber_notes(subscriber_id: int, notes: str) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE subscribers SET notes = ? WHERE id = ?", (notes, subscriber_id)
        )
        return cur.rowcount > 0


def get_subscriber_count_by_date() -> list[dict[str, Any]]:
    """Return subscriber growth data (count per day)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DATE(subscribed_at) as date, COUNT(*) as count
            FROM subscribers
            GROUP BY DATE(subscribed_at)
            ORDER BY date ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_tags() -> list[str]:
    """Return all unique tags used across subscribers."""
    with get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT tags FROM subscribers WHERE tags != ''").fetchall()
    tags_set: set[str] = set()
    for row in rows:
        for tag in row["tags"].split(","):
            stripped = tag.strip()
            if stripped:
                tags_set.add(stripped)
    return sorted(tags_set)


# ---------------------------------------------------------------------------
# Newsletters
# ---------------------------------------------------------------------------

def create_newsletter(
    subject: str,
    body_text: str | None = None,
    body_html: str | None = None,
    template: str = "minimal",
    status: str = "draft",
    scheduled_at: str | None = None,
) -> int:
    now = _now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO newsletters (subject, body_text, body_html, template, status, created_at, scheduled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (subject, body_text, body_html, template, status, now, scheduled_at),
        )
        newsletter_id = cur.lastrowid
    logger.info("Newsletter %d created (status=%s)", newsletter_id, status)
    return newsletter_id  # type: ignore[return-value]


def update_newsletter(
    newsletter_id: int,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    template: str | None = None,
    status: str | None = None,
    scheduled_at: str | None = None,
    recipient_count: int | None = None,
    sent_at: str | None = None,
) -> bool:
    fields: list[str] = []
    values: list[Any] = []
    for col, val in [
        ("subject", subject), ("body_text", body_text), ("body_html", body_html),
        ("template", template), ("status", status), ("scheduled_at", scheduled_at),
        ("recipient_count", recipient_count), ("sent_at", sent_at),
    ]:
        if val is not None:
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        return False
    values.append(newsletter_id)
    with get_connection() as conn:
        cur = conn.execute(
            f"UPDATE newsletters SET {', '.join(fields)} WHERE id = ?", values
        )
        return cur.rowcount > 0


def get_newsletter(newsletter_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM newsletters WHERE id = ?", (newsletter_id,)).fetchone()
    return dict(row) if row else None


def list_newsletters(status_filter: str = "", page: int = 1, per_page: int = 20) -> tuple[list[dict[str, Any]], int]:
    with get_connection() as conn:
        where = ""
        params: list[Any] = []
        if status_filter:
            where = "WHERE status = ?"
            params.append(status_filter)

        count_row = conn.execute(f"SELECT COUNT(*) as cnt FROM newsletters {where}", params).fetchone()
        total = count_row["cnt"] if count_row else 0

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT * FROM newsletters {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
    return [dict(row) for row in rows], total


def delete_newsletter(newsletter_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM newsletters WHERE id = ? AND status = 'draft'", (newsletter_id,))
        return cur.rowcount > 0


def list_scheduled_newsletters() -> list[dict[str, Any]]:
    now = _now_iso()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM newsletters WHERE status = 'scheduled' AND scheduled_at <= ?",
            (now,),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def record_analytics_event(newsletter_id: int, subscriber_id: int, event: str, url: str | None = None) -> None:
    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO analytics (newsletter_id, subscriber_id, event, url, created_at) VALUES (?, ?, ?, ?, ?)",
            (newsletter_id, subscriber_id, event, url, now),
        )


def get_newsletter_analytics(newsletter_id: int) -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT event, COUNT(DISTINCT subscriber_id) as cnt FROM analytics WHERE newsletter_id = ? GROUP BY event",
            (newsletter_id,),
        ).fetchall()
    result: dict[str, int] = {}
    for row in rows:
        result[row["event"]] = row["cnt"]
    return result


def get_subscriber_id_by_token(token: str) -> int | None:
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM subscribers WHERE token = ?", (token,)).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Delivery log
# ---------------------------------------------------------------------------

def record_delivery(newsletter_id: int, subscriber_id: int, delivery_status: str, error_message: str | None = None) -> None:
    now = _now_iso()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO delivery_log (newsletter_id, subscriber_id, status, error_message, created_at) VALUES (?, ?, ?, ?, ?)",
            (newsletter_id, subscriber_id, delivery_status, error_message, now),
        )


def get_delivery_failures(newsletter_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if newsletter_id:
            rows = conn.execute(
                """SELECT dl.*, s.email FROM delivery_log dl
                   JOIN subscribers s ON s.id = dl.subscriber_id
                   WHERE dl.newsletter_id = ? AND dl.status = 'failed'
                   ORDER BY dl.created_at DESC LIMIT ?""",
                (newsletter_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT dl.*, s.email FROM delivery_log dl
                   JOIN subscribers s ON s.id = dl.subscriber_id
                   WHERE dl.status = 'failed'
                   ORDER BY dl.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def create_webhook(url: str, events: str = "all") -> int:
    now = _now_iso()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO webhooks (url, events, created_at) VALUES (?, ?, ?)",
            (url, events, now),
        )
        return cur.lastrowid  # type: ignore[return-value]


def list_webhooks() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM webhooks WHERE active = 1 ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def delete_webhook(webhook_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        return cur.rowcount > 0
