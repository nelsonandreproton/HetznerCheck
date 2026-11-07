"""Atomic JSON snapshots shared between hetzner-monitor (writer) and GarminBot (reader)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def write_snapshot(path: str, data: dict) -> None:
    """Write data + top-level collected_at (UTC ISO-8601) atomically to path."""
    payload = {**data, "collected_at": datetime.now(UTC).isoformat()}
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".snapshot-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, default=_json_default)
        os.chmod(tmp_path, 0o644)  # readable by the (unprivileged) reader container
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _json_default(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def read_snapshot(path: str) -> tuple[dict, datetime] | None:
    """Return (data, collected_at) or None if the file is missing or corrupt."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        collected_at = datetime.fromisoformat(data["collected_at"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Snapshot %s unreadable: %s", path, exc)
        return None
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=UTC)
    return data, collected_at


def age_minutes(collected_at: datetime) -> int:
    """Whole minutes elapsed since collected_at."""
    return int((datetime.now(UTC) - collected_at).total_seconds() // 60)
