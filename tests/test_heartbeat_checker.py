"""Tests for ThresholdChecker.check_heartbeats()."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from monitor.checker import ThresholdChecker
from monitor.heartbeat import beat


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "heartbeats.db"


@pytest.fixture()
def checker() -> ThresholdChecker:
    return ThresholdChecker({"alerts": {"cooldown_minutes": 0}})  # no cooldown in tests


def _write_beat(
    db: Path,
    bot_name: str,
    *,
    last_run_offset: int = -3600,   # seconds relative to now (negative = past)
    next_in_seconds: int | None = 3600,
    status: str = "ok",
    note: str = "",
) -> None:
    """Helper: write a heartbeat with controlled timestamps via monkeypatching."""
    from datetime import UTC, datetime, timedelta
    from monitor import heartbeat as hb_mod

    fake_now = datetime.now(UTC) + timedelta(seconds=last_run_offset)

    with patch.object(hb_mod, "_db_path", return_value=db):
        import monitor.heartbeat as _hb
        original_now = _hb.datetime

        class _FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now

        import monitor.heartbeat
        monitor.heartbeat.datetime = _FakeDatetime
        try:
            beat(bot_name, status=status, note=note, next_in_seconds=next_in_seconds, db=db)
        finally:
            monitor.heartbeat.datetime = original_now


class TestCheckHeartbeats:
    def test_no_alerts_when_db_missing(self, checker: ThresholdChecker, tmp_path: Path) -> None:
        missing = tmp_path / "no.db"
        with patch("monitor.checker._read_heartbeats", return_value=[]):
            alerts = checker.check_heartbeats()
        assert alerts == []

    def test_no_alerts_when_bot_on_time(self, checker: ThresholdChecker, db: Path) -> None:
        """Bot ran 1 minute ago, next expected in 59 minutes — no alert."""
        beat("OnTimeBot", next_in_seconds=3600, db=db)  # just ran, next in 1h

        rows = _read_rows(db)
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            alerts = checker.check_heartbeats()
        assert alerts == []

    def test_alert_when_bot_overdue(self, checker: ThresholdChecker, db: Path) -> None:
        """Bot last ran 2h ago, expected every 1h — overdue."""
        # last_run = 2h ago, next_expected = 1h ago
        now = datetime.now(UTC)
        rows = [{
            "bot_name": "LateBot",
            "last_run_utc": (now - timedelta(hours=2)).isoformat(),
            "status": "ok",
            "next_expected_utc": (now - timedelta(hours=1)).isoformat(),
            "note": "",
        }]
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            alerts = checker.check_heartbeats()

        assert len(alerts) == 1
        assert "LateBot" in alerts[0]["message"]
        assert alerts[0]["level"] == "warning"

    def test_no_alert_within_grace_period(self, checker: ThresholdChecker) -> None:
        """Bot is past next_expected but within grace period (1.5× interval)."""
        now = datetime.now(UTC)
        # interval = 1h, grace = 1.5h → alert threshold = last_run + 1.5h
        # last_run = 70min ago, next_expected = 10min ago → within grace
        rows = [{
            "bot_name": "GraceBot",
            "last_run_utc": (now - timedelta(minutes=70)).isoformat(),
            "status": "ok",
            "next_expected_utc": (now - timedelta(minutes=10)).isoformat(),
            "note": "",
        }]
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            alerts = checker.check_heartbeats()
        assert alerts == []

    def test_alert_after_grace_period_expired(self, checker: ThresholdChecker) -> None:
        """Bot is past the grace deadline (1.5× interval)."""
        now = datetime.now(UTC)
        # interval = 1h, grace = 1.5h → alert at last_run + 1.5h
        # last_run = 100min ago → past grace
        rows = [{
            "bot_name": "ExpiredBot",
            "last_run_utc": (now - timedelta(minutes=100)).isoformat(),
            "status": "ok",
            "next_expected_utc": (now - timedelta(minutes=40)).isoformat(),
            "note": "",
        }]
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            alerts = checker.check_heartbeats()
        assert len(alerts) == 1
        assert "ExpiredBot" in alerts[0]["message"]

    def test_no_alert_for_bots_without_deadline(self, checker: ThresholdChecker) -> None:
        """Bots that didn't set next_in_seconds are skipped."""
        now = datetime.now(UTC)
        rows = [{
            "bot_name": "NoDeadlineBot",
            "last_run_utc": (now - timedelta(days=10)).isoformat(),
            "status": "ok",
            "next_expected_utc": None,
            "note": "",
        }]
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            alerts = checker.check_heartbeats()
        assert alerts == []

    def test_alert_includes_note(self, checker: ThresholdChecker) -> None:
        now = datetime.now(UTC)
        rows = [{
            "bot_name": "NoteBot",
            "last_run_utc": (now - timedelta(hours=2)).isoformat(),
            "status": "error",
            "next_expected_utc": (now - timedelta(hours=1)).isoformat(),
            "note": "Garmin auth expired",
        }]
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            alerts = checker.check_heartbeats()
        assert "Garmin auth expired" in alerts[0]["message"]

    def test_cooldown_suppresses_repeated_alert(self, checker: ThresholdChecker) -> None:
        """Second call within cooldown window returns no alert."""
        checker2 = ThresholdChecker({"alerts": {"cooldown_minutes": 60}})
        now = datetime.now(UTC)
        rows = [{
            "bot_name": "CoolBot",
            "last_run_utc": (now - timedelta(hours=2)).isoformat(),
            "status": "ok",
            "next_expected_utc": (now - timedelta(hours=1)).isoformat(),
            "note": "",
        }]
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            first = checker2.check_heartbeats()
            second = checker2.check_heartbeats()

        assert len(first) == 1
        assert len(second) == 0  # suppressed by cooldown

    def test_multiple_bots_independent_alerts(self, checker: ThresholdChecker) -> None:
        now = datetime.now(UTC)
        rows = [
            {
                "bot_name": "BotA",
                "last_run_utc": (now - timedelta(hours=2)).isoformat(),
                "status": "ok",
                "next_expected_utc": (now - timedelta(hours=1)).isoformat(),
                "note": "",
            },
            {
                "bot_name": "BotB",
                "last_run_utc": now.isoformat(),
                "status": "ok",
                "next_expected_utc": (now + timedelta(hours=1)).isoformat(),
                "note": "",
            },
            {
                "bot_name": "BotC",
                "last_run_utc": (now - timedelta(hours=3)).isoformat(),
                "status": "degraded",
                "next_expected_utc": (now - timedelta(hours=2)).isoformat(),
                "note": "API slow",
            },
        ]
        with patch("monitor.checker._read_heartbeats", return_value=rows):
            alerts = checker.check_heartbeats()

        alerted = {a["message"] for a in alerts}
        assert any("BotA" in m for m in alerted)
        assert not any("BotB" in m for m in alerted)   # on time
        assert any("BotC" in m for m in alerted)


def _read_rows(db: Path) -> list[dict]:
    from monitor.heartbeat import read_all
    return read_all(db=db)
