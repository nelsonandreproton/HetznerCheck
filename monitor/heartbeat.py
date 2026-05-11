"""Shared heartbeat module for bot liveness tracking.

Each bot calls beat() after every successful run cycle. HetznerCheck reads
the heartbeats.db file and alerts when a bot is overdue.

Usage (in any bot):
    from heartbeat import beat   # when mounted at /hetznercheck

    beat("GarminBot", next_in_seconds=86400)          # daily bot
    beat("PTEvents", status="degraded", note="API slow", next_in_seconds=300)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Default DB path — overridden by HEARTBEAT_DB env var or explicit argument.
# On Hetzner: /app/heartbeats/heartbeats.db (shared Docker volume).
_DEFAULT_DB = Path("/app/heartbeats/heartbeats.db")


def _db_path() -> Path:
    import os
    env = os.environ.get("HEARTBEAT_DB")
    return Path(env) if env else _DEFAULT_DB


def _connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")  # safe for multi-process concurrent writes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heartbeats (
            bot_name        TEXT PRIMARY KEY,
            last_run_utc    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'ok',
            next_expected_utc TEXT,
            note            TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn


def beat(
    bot_name: str,
    *,
    status: str = "ok",
    note: str = "",
    next_in_seconds: int | None = None,
    db: Path | None = None,
) -> None:
    """Record a successful heartbeat for bot_name.

    Args:
        bot_name: Unique bot identifier (e.g. "GarminBot", "PTEvents").
        status: "ok" | "degraded" | "error". Use "degraded" for partial
                success (e.g. Garmin sync succeeded but report failed).
        note: Short human-readable note logged alongside the heartbeat.
        next_in_seconds: Seconds until the next expected run. HetznerCheck
                         will alert if this deadline is missed. If None,
                         no deadline is tracked.
        db: Override DB path (useful for tests).
    """
    target = db or _db_path()
    now = datetime.now(UTC)
    next_expected = (
        (now + timedelta(seconds=next_in_seconds)).isoformat()
        if next_in_seconds is not None
        else None
    )

    try:
        conn = _connect(target)
        conn.execute(
            """
            INSERT INTO heartbeats (bot_name, last_run_utc, status, next_expected_utc, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(bot_name) DO UPDATE SET
                last_run_utc      = excluded.last_run_utc,
                status            = excluded.status,
                next_expected_utc = excluded.next_expected_utc,
                note              = excluded.note
            """,
            (bot_name, now.isoformat(), status, next_expected, note),
        )
        conn.commit()
        conn.close()
        logger.debug("Heartbeat: %s status=%s next_in=%s", bot_name, status, next_in_seconds)
    except Exception:
        # Never crash the bot because the heartbeat failed.
        logger.exception("Heartbeat write failed for %s — continuing", bot_name)


def read_all(db: Path | None = None) -> list[dict]:
    """Return all heartbeat rows as dicts. Used by HetznerCheck watcher."""
    target = db or _db_path()
    if not target.exists():
        return []
    try:
        conn = _connect(target)
        rows = conn.execute(
            "SELECT bot_name, last_run_utc, status, next_expected_utc, note FROM heartbeats"
        ).fetchall()
        conn.close()
        return [
            {
                "bot_name": r[0],
                "last_run_utc": r[1],
                "status": r[2],
                "next_expected_utc": r[3],
                "note": r[4],
            }
            for r in rows
        ]
    except Exception:
        logger.exception("Heartbeat read failed")
        return []
