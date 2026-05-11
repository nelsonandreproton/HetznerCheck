"""Unit tests for monitor/heartbeat.py."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from monitor.heartbeat import beat, read_all


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Temporary heartbeats.db for each test."""
    return tmp_path / "heartbeats.db"


# ---------------------------------------------------------------------------
# beat()
# ---------------------------------------------------------------------------

class TestBeat:
    def test_creates_db_and_row(self, db: Path) -> None:
        beat("TestBot", db=db)

        assert db.exists()
        rows = read_all(db=db)
        assert len(rows) == 1
        assert rows[0]["bot_name"] == "TestBot"
        assert rows[0]["status"] == "ok"

    def test_default_status_is_ok(self, db: Path) -> None:
        beat("TestBot", db=db)
        assert read_all(db=db)[0]["status"] == "ok"

    def test_custom_status(self, db: Path) -> None:
        beat("TestBot", status="degraded", db=db)
        assert read_all(db=db)[0]["status"] == "degraded"

    def test_note_stored(self, db: Path) -> None:
        beat("TestBot", note="API slow", db=db)
        assert read_all(db=db)[0]["note"] == "API slow"

    def test_next_expected_set(self, db: Path) -> None:
        before = datetime.now(UTC)
        beat("TestBot", next_in_seconds=3600, db=db)
        after = datetime.now(UTC)

        row = read_all(db=db)[0]
        next_dt = datetime.fromisoformat(row["next_expected_utc"])
        assert before + timedelta(seconds=3600) <= next_dt <= after + timedelta(seconds=3600)

    def test_next_expected_none_when_not_set(self, db: Path) -> None:
        beat("TestBot", db=db)
        assert read_all(db=db)[0]["next_expected_utc"] is None

    def test_upsert_updates_existing_row(self, db: Path) -> None:
        beat("TestBot", status="ok", note="first", db=db)
        beat("TestBot", status="degraded", note="second", db=db)

        rows = read_all(db=db)
        assert len(rows) == 1  # still one row
        assert rows[0]["status"] == "degraded"
        assert rows[0]["note"] == "second"

    def test_multiple_bots_stored_separately(self, db: Path) -> None:
        beat("BotA", db=db)
        beat("BotB", status="degraded", db=db)

        rows = read_all(db=db)
        names = {r["bot_name"] for r in rows}
        assert names == {"BotA", "BotB"}

    def test_last_run_utc_is_recent(self, db: Path) -> None:
        before = datetime.now(UTC)
        beat("TestBot", db=db)
        after = datetime.now(UTC)

        row = read_all(db=db)[0]
        last_run = datetime.fromisoformat(row["last_run_utc"])
        assert before <= last_run <= after

    def test_beat_does_not_raise_on_bad_db_path(self) -> None:
        """beat() must never crash the caller, even if the DB path is unwritable."""
        bad_path = Path("/nonexistent/readonly/dir/heartbeats.db")
        # Should log an error but not raise
        beat("TestBot", db=bad_path)


# ---------------------------------------------------------------------------
# read_all()
# ---------------------------------------------------------------------------

class TestReadAll:
    def test_returns_empty_list_when_db_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.db"
        assert read_all(db=missing) == []

    def test_returns_all_rows(self, db: Path) -> None:
        beat("BotA", db=db)
        beat("BotB", db=db)
        beat("BotC", db=db)

        rows = read_all(db=db)
        assert len(rows) == 3

    def test_row_has_expected_keys(self, db: Path) -> None:
        beat("TestBot", db=db)
        row = read_all(db=db)[0]
        assert set(row.keys()) == {
            "bot_name", "last_run_utc", "status", "next_expected_utc", "note"
        }

    def test_wal_allows_concurrent_reads(self, db: Path) -> None:
        """Two connections can read simultaneously without locking errors."""
        beat("TestBot", db=db)

        conn1 = sqlite3.connect(str(db))
        conn2 = sqlite3.connect(str(db))
        r1 = conn1.execute("SELECT bot_name FROM heartbeats").fetchall()
        r2 = conn2.execute("SELECT bot_name FROM heartbeats").fetchall()
        conn1.close()
        conn2.close()

        assert r1 == r2 == [("TestBot",)]
