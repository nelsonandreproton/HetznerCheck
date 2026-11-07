"""Tests for monitor.snapshot and the snapshot-backed bot handlers / main loop."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitor import snapshot
from monitor.container_disk import ContainerDiskInfo


def _metrics() -> dict:
    return {
        "cpu": {"percent": 10.0, "load_1": 0.1, "load_5": 0.2, "load_15": 0.3, "cpu_count": 2},
        "memory": {"total": 1, "available": 1, "percent": 50.0, "swap_total": 0,
                   "swap_used": 0, "swap_percent": 0.0},
        "disk": {"/": {"total": 10, "used": 5, "free": 5, "percent": 50.0, "path": "/rootfs"}},
        "network": {"bytes_sent_rate": 1.0, "bytes_recv_rate": 2.0},
        "processes": {"total": 100, "zombie_count": 0},
        "docker": [{"name": "web", "status": "running", "health": "healthy"}],
        "ssh": {"failures_last_hour": 0},
        "uptime": {"uptime_human": "1d 2h 3m"},
        "timestamp": datetime(2026, 1, 2, 3, 4, 5),
    }


# --- write/read --------------------------------------------------------------

def test_write_read_roundtrip(tmp_path):
    p = tmp_path / "m.json"
    snapshot.write_snapshot(str(p), {"a": 1, "nested": {"b": [1, 2]}})
    data, collected_at = snapshot.read_snapshot(str(p))
    assert data["a"] == 1 and data["nested"] == {"b": [1, 2]}
    assert collected_at.tzinfo is not None
    assert abs((datetime.now(UTC) - collected_at).total_seconds()) < 5


def test_write_leaves_no_temp_files(tmp_path):
    p = tmp_path / "m.json"
    snapshot.write_snapshot(str(p), {"a": 1})
    snapshot.write_snapshot(str(p), {"a": 2})
    assert os.listdir(tmp_path) == ["m.json"]


def test_write_failure_keeps_old_file_and_cleans_temp(tmp_path):
    p = tmp_path / "m.json"
    snapshot.write_snapshot(str(p), {"a": 1})
    with patch("monitor.snapshot.os.replace", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            snapshot.write_snapshot(str(p), {"a": 2})
    assert os.listdir(tmp_path) == ["m.json"]
    assert snapshot.read_snapshot(str(p))[0]["a"] == 1


def test_write_serializes_datetime(tmp_path):
    p = tmp_path / "m.json"
    snapshot.write_snapshot(str(p), {"timestamp": datetime(2026, 1, 2, 3, 4, 5)})
    data, _ = snapshot.read_snapshot(str(p))
    assert data["timestamp"] == "2026-01-02T03:04:05"


def test_read_missing_returns_none(tmp_path):
    assert snapshot.read_snapshot(str(tmp_path / "nope.json")) is None


@pytest.mark.parametrize("content", ["", "{not json", "[1,2]", '{"a": 1}',
                                     '{"collected_at": "garbage"}'])
def test_read_corrupt_returns_none(tmp_path, content):
    p = tmp_path / "m.json"
    p.write_text(content)
    assert snapshot.read_snapshot(str(p)) is None


def test_snapshot_age_minutes():
    ts = datetime.now(UTC) - timedelta(minutes=3, seconds=10)
    assert snapshot.age_minutes(ts) == 3


# --- bot handlers ------------------------------------------------------------

def _update():
    update = MagicMock()
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    update.effective_message = msg
    return update, msg


def _write_metrics(dirpath, age_min=0):
    snapshot.write_snapshot(str(dirpath / "metrics.json"), _metrics())
    if age_min:
        path = dirpath / "metrics.json"
        d = json.loads(path.read_text())
        d["collected_at"] = (datetime.now(UTC) - timedelta(minutes=age_min)).isoformat()
        path.write_text(json.dumps(d))


def _status_handler(status_dir):
    from monitor.bot_handler import _make_snapshot_handler
    return _make_snapshot_handler(str(status_dir))


def test_server_status_from_snapshot_fresh(tmp_path):
    _write_metrics(tmp_path)
    update, msg = _update()
    asyncio.run(_status_handler(tmp_path)(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "Estado do Servidor" in text
    assert "2026-01-02 03:04" in text
    assert "dados de há 0 min" in text
    assert "desatualizados" not in text


def test_server_status_from_snapshot_stale_warns(tmp_path):
    _write_metrics(tmp_path, age_min=30)
    update, msg = _update()
    asyncio.run(_status_handler(tmp_path)(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "dados de há 30 min" in text
    assert "desatualizados" in text


def test_server_status_snapshot_missing(tmp_path):
    update, msg = _update()
    asyncio.run(_status_handler(tmp_path)(update, MagicMock()))
    assert "❌" in msg.reply_text.call_args.args[0]


def test_snapshot_handler_does_not_collect_live(tmp_path):
    _write_metrics(tmp_path)
    update, msg = _update()
    with patch("monitor.collectors.collect_all", side_effect=AssertionError("live collect")):
        asyncio.run(_status_handler(tmp_path)(update, MagicMock()))
    msg.reply_text.assert_awaited_once()


def test_register_server_status_with_status_dir_skips_config(tmp_path):
    from monitor.bot_handler import register_server_status_handler
    app = MagicMock()
    register_server_status_handler(app, config_path="/does/not/exist.yml", status_dir=str(tmp_path))
    app.add_handler.assert_called_once()


def _disk_dicts():
    return [{"name": "web", "virtual_size": 2048, "writable_size": 1024, "file_count": 3,
             "top_files": [[1024, "a/b.log"]], "note": ""}]


def test_container_disk_from_snapshot(tmp_path):
    from monitor.bot_handler import register_container_disk_handler
    snapshot.write_snapshot(str(tmp_path / "container_disk.json"), {"containers": _disk_dicts()})
    app = MagicMock()
    register_container_disk_handler(app, status_dir=str(tmp_path))
    handler = app.add_handler.call_args.args[0].callback
    update, msg = _update()
    asyncio.run(handler(update, MagicMock()))
    text = msg.reply_text.call_args.args[0]
    assert "web" in text and "a/b.log" in text and "dados de há 0 min" in text


def test_container_disk_snapshot_missing(tmp_path):
    from monitor.bot_handler import register_container_disk_handler
    app = MagicMock()
    register_container_disk_handler(app, status_dir=str(tmp_path))
    handler = app.add_handler.call_args.args[0].callback
    update, msg = _update()
    asyncio.run(handler(update, MagicMock()))
    assert "❌" in msg.reply_text.call_args.args[0]


def test_format_container_disk_accepts_dicts():
    from monitor.container_disk import format_container_disk
    infos = [ContainerDiskInfo("web", 2048, 1024, 3, [(1024, "a/b.log")], "")]
    assert format_container_disk(_disk_dicts()) == format_container_disk(infos)


def test_container_disk_to_dicts_json_roundtrip():
    from monitor.container_disk import container_disk_to_dicts
    infos = [ContainerDiskInfo("web", 2048, 1024, 3, [(1024, "a/b.log")], "n")]
    assert json.loads(json.dumps(container_disk_to_dicts(infos))) == [
        {"name": "web", "virtual_size": 2048, "writable_size": 1024, "file_count": 3,
         "top_files": [[1024, "a/b.log"]], "note": "n"}]


# --- main loop ---------------------------------------------------------------

def test_writer_writes_metrics_and_disk_first_cycle(tmp_path):
    from monitor.main import SnapshotWriter
    w = SnapshotWriter(str(tmp_path))
    with patch("monitor.main.collect_container_disk",
               return_value=[ContainerDiskInfo("web", 1, 1, 1, [], "")]) as cd:
        w.write(_metrics())
    assert cd.call_count == 1
    assert snapshot.read_snapshot(str(tmp_path / "metrics.json")) is not None
    data, _ = snapshot.read_snapshot(str(tmp_path / "container_disk.json"))
    assert data["containers"][0]["name"] == "web"


def test_container_disk_throttled_to_once_per_hour(tmp_path):
    from monitor.main import SnapshotWriter
    w = SnapshotWriter(str(tmp_path))
    with patch("monitor.main.collect_container_disk", return_value=[]) as cd, \
         patch("monitor.main.time.monotonic") as mono:
        mono.return_value = 1000.0
        w.write(_metrics())
        mono.return_value = 1000.0 + 3599
        w.write(_metrics())
        assert cd.call_count == 1
        mono.return_value = 1000.0 + 3601
        w.write(_metrics())
        assert cd.call_count == 2


def test_writer_survives_errors(tmp_path):
    from monitor.main import SnapshotWriter
    w = SnapshotWriter(str(tmp_path))
    with patch("monitor.main.write_snapshot", side_effect=OSError("disk full")), \
         patch("monitor.main.collect_container_disk", side_effect=RuntimeError("docker")):
        w.write(_metrics())  # must not raise


def test_writer_disk_failure_does_not_block_metrics(tmp_path):
    from monitor.main import SnapshotWriter
    w = SnapshotWriter(str(tmp_path))
    with patch("monitor.main.collect_container_disk", side_effect=RuntimeError("docker")):
        w.write(_metrics())
    assert snapshot.read_snapshot(str(tmp_path / "metrics.json")) is not None


def _run_once(**patches):
    from monitor import main
    config = {"telegram": {"bot_token": "t", "chat_id": "c"}, "schedule": {"check_interval_seconds": 1}}
    checker = MagicMock()
    checker.check.return_value = []
    checker.check_heartbeats.return_value = []
    with patch.object(main, "load_config", return_value=config), \
         patch.object(main, "TelegramNotifier"), \
         patch.object(main, "ThresholdChecker", return_value=checker), \
         patch.object(main, "collect_all", return_value=_metrics()), \
         patch.object(main, "collect_container_disk", return_value=[]), \
         patch.object(main.time, "sleep", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            main.run()


def test_run_loop_writes_snapshot_when_status_dir_set(tmp_path, monkeypatch):
    monkeypatch.setenv("STATUS_DIR", str(tmp_path))
    _run_once()
    assert (tmp_path / "metrics.json").exists()


def test_run_loop_no_status_dir_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("STATUS_DIR", raising=False)
    with patch("monitor.main.write_snapshot") as ws:
        _run_once()
    ws.assert_not_called()


def test_run_loop_survives_snapshot_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("STATUS_DIR", str(tmp_path))
    with patch("monitor.main.write_snapshot", side_effect=OSError("ro fs")):
        _run_once()  # reaches sleep (KeyboardInterrupt) rather than crashing earlier
