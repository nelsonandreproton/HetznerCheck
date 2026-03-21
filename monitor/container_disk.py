"""Docker container disk usage collector for /container_disk command.

For each container reports:
  - Full virtual size (image layers + writable layer)
  - Writable layer size
  - Total file count
  - Top 5 largest files with sizes

Also scans the openclaw host installation at OPENCLAW_HOST_PATH.

Requires the container to have:
  - /:/rootfs:ro       (host filesystem access)
  - docker.sock access (Docker API)
"""

from __future__ import annotations

import heapq
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Host root as seen from inside the monitor container
ROOTFS = "/rootfs"
OPENCLAW_HOST_PATH = "/home/garminbot/.openclaw"

# Pseudo-filesystem dirs to skip when walking
_SKIP_DIRS = frozenset({"proc", "sys", "dev"})


@dataclass
class ContainerDiskInfo:
    name: str
    virtual_size: int               # bytes: full image + writable layer
    writable_size: int              # bytes: writable layer only
    file_count: int
    top_files: list[tuple[int, str]]  # [(size_bytes, relative_path), ...]
    note: str = ""


def _fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _scan_directory(path: str) -> tuple[int, list[tuple[int, str]]]:
    """Return (file_count, top_5_largest) for a directory tree.

    Uses a min-heap to track top 5 without storing all file entries.
    Skips proc/sys/dev and handles permission errors gracefully.
    """
    count = 0
    heap: list[tuple[int, str]] = []  # min-heap by size

    try:
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    size = os.lstat(fpath).st_size
                    count += 1
                    rel = os.path.relpath(fpath, path)
                    if len(heap) < 5:
                        heapq.heappush(heap, (size, rel))
                    elif size > heap[0][0]:
                        heapq.heapreplace(heap, (size, rel))
                except OSError:
                    pass
    except OSError as exc:
        logger.warning("_scan_directory(%s): %s", path, exc)

    return count, sorted(heap, reverse=True)


def _dir_total_size(path: str) -> int:
    """Sum of all file sizes under path (lstat, no symlink follow)."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    total += os.lstat(fpath).st_size
                except OSError:
                    pass
    except OSError as exc:
        logger.warning("_dir_total_size(%s): %s", path, exc)
    return total


def collect_container_disk() -> list[ContainerDiskInfo]:
    """Collect disk info for all Docker containers + openclaw host installation."""
    import docker

    results: list[ContainerDiskInfo] = []

    try:
        client = docker.from_env()
        raw_list = client.api.containers(all=True, size=True)
    except Exception as exc:
        logger.error("Docker API unavailable: %s", exc)
        return results

    for raw in raw_list:
        container_id = raw["Id"]
        names = raw.get("Names", [])
        name = names[0].lstrip("/") if names else container_id[:12]
        virtual_size = raw.get("SizeRootFs", 0) or 0
        writable_size = raw.get("SizeRw", 0) or 0

        # Get overlay2 paths from container inspect
        merged_dir = ""
        upper_dir = ""
        try:
            info = client.api.inspect_container(container_id)
            graph_data = (info.get("GraphDriver") or {}).get("Data") or {}
            merged_dir = graph_data.get("MergedDir", "")
            upper_dir = graph_data.get("UpperDir", "")
        except Exception as exc:
            logger.warning("inspect_container(%s): %s", name, exc)

        note = ""
        scan_path = ""

        if merged_dir:
            host_merged = ROOTFS + merged_dir
            if os.path.isdir(host_merged):
                scan_path = host_merged
            elif upper_dir:
                # Container stopped: MergedDir not mounted, fall back to writable layer
                host_upper = ROOTFS + upper_dir
                if os.path.isdir(host_upper):
                    scan_path = host_upper
                    note = "container parado — camada de escrita apenas"

        elif upper_dir:
            host_upper = ROOTFS + upper_dir
            if os.path.isdir(host_upper):
                scan_path = host_upper
                note = "container parado — camada de escrita apenas"

        if scan_path:
            file_count, top_files = _scan_directory(scan_path)
        else:
            file_count, top_files = 0, []
            if not note:
                note = "sistema de ficheiros não acessível"

        results.append(ContainerDiskInfo(
            name=name,
            virtual_size=virtual_size,
            writable_size=writable_size,
            file_count=file_count,
            top_files=top_files,
            note=note,
        ))

    # Openclaw host installation
    openclaw_host = ROOTFS + OPENCLAW_HOST_PATH
    if os.path.isdir(openclaw_host):
        file_count, top_files = _scan_directory(openclaw_host)
        total_size = _dir_total_size(openclaw_host)
        results.append(ContainerDiskInfo(
            name="openclaw (instalação host)",
            virtual_size=total_size,
            writable_size=total_size,
            file_count=file_count,
            top_files=top_files,
        ))

    return results


def format_container_disk(results: list[ContainerDiskInfo]) -> str:
    """Format disk info as HTML for Telegram (max ~4096 chars)."""
    if not results:
        return "❌ Não foi possível obter informação de disco dos containers."

    lines = ["💾 <b>Disk Usage por Container</b>\n"]

    for info in results:
        is_host = "openclaw" in info.name and "host" in info.name
        icon = "📁" if is_host else "🐳"
        lines.append(f"{icon} <b>{info.name}</b>")

        if is_host:
            lines.append(f"  Tamanho total: {_fmt_size(info.virtual_size)}")
        else:
            lines.append(f"  Imagem+dados: {_fmt_size(info.virtual_size)}")
            lines.append(f"  Escrita: {_fmt_size(info.writable_size)}")

        lines.append(f"  Ficheiros: {info.file_count:,}")

        if info.top_files:
            lines.append("  Top 5 maiores:")
            for i, (size, path) in enumerate(info.top_files, 1):
                lines.append(f"    {i}. {_fmt_size(size)} — <code>{path}</code>")

        if info.note:
            lines.append(f"  ⚠️ {info.note}")

        lines.append("")

    text = "\n".join(lines).rstrip()

    # Telegram hard limit is 4096 chars
    if len(text) > 4090:
        text = text[:4090] + "\n..."

    return text
