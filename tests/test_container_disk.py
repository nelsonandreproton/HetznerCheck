"""Tests for monitor.container_disk."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from monitor.container_disk import (
    ContainerDiskInfo,
    _dir_total_size,
    _fmt_size,
    _scan_directory,
    collect_container_disk,
    format_container_disk,
)


# ---------------------------------------------------------------------------
# _fmt_size
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,expected", [
    (0, "0.0 B"),
    (512, "512.0 B"),
    (1024, "1.0 KB"),
    (1024 ** 2, "1.0 MB"),
    (1024 ** 3, "1.0 GB"),
    (1536, "1.5 KB"),
])
def test_fmt_size(n, expected):
    assert _fmt_size(n) == expected


# ---------------------------------------------------------------------------
# _scan_directory
# ---------------------------------------------------------------------------

def _make_tree(root: str, files: dict[str, bytes]) -> None:
    """Write {relative_path: content} into root."""
    for rel, data in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


def test_scan_directory_counts_files():
    with tempfile.TemporaryDirectory() as tmp:
        _make_tree(tmp, {
            "a.txt": b"x" * 100,
            "b.txt": b"x" * 200,
            "sub/c.txt": b"x" * 50,
        })
        count, top = _scan_directory(tmp)
        assert count == 3


def test_scan_directory_top5_sorted_descending():
    with tempfile.TemporaryDirectory() as tmp:
        sizes = [100, 200, 300, 400, 500, 600]
        for i, s in enumerate(sizes):
            _make_tree(tmp, {f"f{i}.bin": b"x" * s})
        count, top = _scan_directory(tmp)
        assert count == 6
        assert len(top) == 5
        # Largest first
        assert top[0][0] >= top[1][0] >= top[2][0]
        # Largest file should be 600 bytes
        assert top[0][0] == 600


def test_scan_directory_fewer_than_5_files():
    with tempfile.TemporaryDirectory() as tmp:
        _make_tree(tmp, {"only.txt": b"hello"})
        count, top = _scan_directory(tmp)
        assert count == 1
        assert len(top) == 1


def test_scan_directory_skips_proc_sys_dev():
    with tempfile.TemporaryDirectory() as tmp:
        _make_tree(tmp, {
            "real.txt": b"data",
            "proc/cpuinfo": b"cpu data",
            "sys/kernel": b"kernel",
            "dev/null": b"",
        })
        count, _ = _scan_directory(tmp)
        # Only real.txt should be counted
        assert count == 1


def test_scan_directory_nonexistent_returns_empty():
    count, top = _scan_directory("/nonexistent/path/xyz")
    assert count == 0
    assert top == []


# ---------------------------------------------------------------------------
# _dir_total_size
# ---------------------------------------------------------------------------

def test_dir_total_size():
    with tempfile.TemporaryDirectory() as tmp:
        _make_tree(tmp, {
            "a.txt": b"x" * 100,
            "b.txt": b"x" * 200,
            "sub/c.txt": b"x" * 300,
        })
        total = _dir_total_size(tmp)
        assert total == 600


def test_dir_total_size_nonexistent():
    assert _dir_total_size("/nonexistent/xyz") == 0


# ---------------------------------------------------------------------------
# collect_container_disk
# ---------------------------------------------------------------------------

def _make_raw_container(name: str, container_id: str, merged_dir: str = "", upper_dir: str = "") -> dict:
    return {
        "Id": container_id,
        "Names": [f"/{name}"],
        "SizeRootFs": 500 * 1024 * 1024,
        "SizeRw": 10 * 1024 * 1024,
    }


def _make_inspect(merged_dir: str = "", upper_dir: str = "") -> dict:
    return {
        "GraphDriver": {
            "Data": {
                "MergedDir": merged_dir,
                "UpperDir": upper_dir,
            }
        }
    }


def test_collect_docker_api_unavailable(stub_docker_module):
    stub_docker_module.from_env.side_effect = Exception("Docker not available")
    results = collect_container_disk()
    assert results == []


@patch("monitor.container_disk.os.path.isdir", return_value=False)
def test_collect_inaccessible_filesystem(mock_isdir, stub_docker_module):
    mock_client = MagicMock()
    stub_docker_module.from_env.return_value = mock_client
    mock_client.api.containers.return_value = [
        _make_raw_container("myapp", "abc123")
    ]
    mock_client.api.inspect_container.return_value = _make_inspect(
        merged_dir="/var/lib/docker/overlay2/abc/merged",
        upper_dir="/var/lib/docker/overlay2/abc/diff",
    )

    results = collect_container_disk()

    assert len(results) == 1
    assert results[0].name == "myapp"
    assert results[0].file_count == 0
    assert results[0].note == "sistema de ficheiros não acessível"


@patch("monitor.container_disk._scan_directory")
@patch("monitor.container_disk.os.path.isdir", return_value=True)
def test_collect_running_container_uses_merged_dir(mock_isdir, mock_scan, stub_docker_module):
    mock_scan.return_value = (42, [(1000, "big.log"), (500, "data.db")])

    mock_client = MagicMock()
    stub_docker_module.from_env.return_value = mock_client
    mock_client.api.containers.return_value = [
        _make_raw_container("webapp", "def456")
    ]
    mock_client.api.inspect_container.return_value = _make_inspect(
        merged_dir="/var/lib/docker/overlay2/def/merged",
        upper_dir="/var/lib/docker/overlay2/def/diff",
    )

    results = collect_container_disk()

    r = next(x for x in results if x.name == "webapp")
    assert r.name == "webapp"
    assert r.file_count == 42
    assert r.top_files == [(1000, "big.log"), (500, "data.db")]
    assert r.virtual_size == 500 * 1024 * 1024
    assert r.writable_size == 10 * 1024 * 1024


@patch("monitor.container_disk._scan_directory")
@patch("monitor.container_disk._dir_total_size", return_value=999)
@patch("monitor.container_disk.os.path.isdir", return_value=True)
def test_collect_includes_openclaw_if_present(mock_isdir, mock_total, mock_scan, stub_docker_module):
    mock_scan.return_value = (10, [(200, "model.bin")])

    mock_client = MagicMock()
    stub_docker_module.from_env.return_value = mock_client
    mock_client.api.containers.return_value = []

    results = collect_container_disk()

    assert len(results) == 1
    assert "openclaw" in results[0].name


# ---------------------------------------------------------------------------
# format_container_disk
# ---------------------------------------------------------------------------

def test_format_empty_results():
    text = format_container_disk([])
    assert "❌" in text


def test_format_single_container():
    info = ContainerDiskInfo(
        name="myapp",
        virtual_size=200 * 1024 * 1024,
        writable_size=5 * 1024 * 1024,
        file_count=123,
        top_files=[(1024 * 1024, "app/data.db"), (512 * 1024, "app/log.txt")],
    )
    text = format_container_disk([info])
    assert "myapp" in text
    assert "200.0 MB" in text
    assert "5.0 MB" in text
    assert "123" in text
    assert "data.db" in text
    assert "log.txt" in text


def test_format_shows_note():
    info = ContainerDiskInfo(
        name="stopped",
        virtual_size=0,
        writable_size=0,
        file_count=0,
        top_files=[],
        note="container parado — camada de escrita apenas",
    )
    text = format_container_disk([info])
    assert "container parado" in text


def test_format_truncates_at_4096():
    # Create enough containers to exceed 4096 chars
    infos = [
        ContainerDiskInfo(
            name=f"container_{i}",
            virtual_size=i * 1024 * 1024,
            writable_size=i * 512 * 1024,
            file_count=i * 100,
            top_files=[(j * 10000, f"very/long/path/to/file_{j}.log") for j in range(5)],
        )
        for i in range(1, 30)
    ]
    text = format_container_disk(infos)
    assert len(text) <= 4096


def test_format_openclaw_label():
    info = ContainerDiskInfo(
        name="openclaw (instalação host)",
        virtual_size=300 * 1024 * 1024,
        writable_size=300 * 1024 * 1024,
        file_count=50,
        top_files=[],
    )
    text = format_container_disk([info])
    assert "📁" in text
    assert "Tamanho total" in text
    # Should NOT show "Escrita" line for host entries
    assert "Escrita:" not in text
