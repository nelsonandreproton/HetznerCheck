"""Unit tests for monitor/heartbeat.py."""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
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

    def test_concurrent_connections_can_read(self, db: Path) -> None:
        """Two connections can read simultaneously without locking errors."""
        beat("TestBot", db=db)

        conn1 = sqlite3.connect(str(db))
        conn2 = sqlite3.connect(str(db))
        r1 = conn1.execute("SELECT bot_name FROM heartbeats").fetchall()
        r2 = conn2.execute("SELECT bot_name FROM heartbeats").fetchall()
        conn1.close()
        conn2.close()

        assert r1 == r2 == [("TestBot",)]


# ---------------------------------------------------------------------------
# Read-only reader / journal mode
# ---------------------------------------------------------------------------

def _journal_mode(db: Path) -> str:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def readonly_db(db: Path):
    """A populated DB whose file (and dir, on POSIX) is read-only."""
    beat("BotA", db=db)
    beat("BotB", status="degraded", db=db)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=DELETE")  # no -wal/-shm files, as in production
    conn.close()
    posix = sys.platform != "win32"
    db.chmod(stat.S_IREAD)
    if posix:
        db.parent.chmod(stat.S_IREAD | stat.S_IEXEC)
    yield db
    if posix:
        db.parent.chmod(stat.S_IRWXU)
    db.chmod(stat.S_IREAD | stat.S_IWRITE)


class TestReadOnlyReader:
    def test_read_all_readonly_file_and_dir_returns_rows(self, readonly_db: Path) -> None:
        rows = read_all(db=readonly_db)
        assert {r["bot_name"] for r in rows} == {"BotA", "BotB"}

    @pytest.mark.skipif(
        sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        reason="needs POSIX permissions enforced for non-root",
    )
    def test_read_all_readonly_dir_does_not_create_side_files(self, readonly_db: Path) -> None:
        read_all(db=readonly_db)
        assert sorted(p.name for p in readonly_db.parent.iterdir()) == ["heartbeats.db"]

    def test_read_all_missing_db_does_not_create_db(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.db"
        assert read_all(db=missing) == []
        assert not missing.exists()

    def test_read_all_missing_dir_does_not_create_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope" / "heartbeats.db"
        assert read_all(db=missing) == []
        assert not missing.parent.exists()

    def test_read_all_db_without_table_returns_empty_list(self, db: Path) -> None:
        sqlite3.connect(str(db)).close()
        assert read_all(db=db) == []

    def test_read_all_path_with_spaces_and_special_chars_returns_rows(self, tmp_path: Path) -> None:
        odd = tmp_path / "dir with space#and%chars" / "hb.db"
        beat("BotA", db=odd)
        assert [r["bot_name"] for r in read_all(db=odd)] == ["BotA"]


class TestJournalMode:
    def test_beat_new_db_uses_delete_journal_mode(self, db: Path) -> None:
        beat("TestBot", db=db)
        assert _journal_mode(db) == "delete"

    def test_beat_existing_wal_db_converts_to_delete(self, db: Path) -> None:
        beat("TestBot", db=db)
        conn = sqlite3.connect(str(db))
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.close()
        assert _journal_mode(db) == "wal"

        beat("TestBot", db=db)

        assert _journal_mode(db) == "delete"
        assert [r["bot_name"] for r in read_all(db=db)] == ["TestBot"]
