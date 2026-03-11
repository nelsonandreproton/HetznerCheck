import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import docker

logger = logging.getLogger(__name__)

# Estado para calcular delta de rede entre chamadas
_prev_net_io = None
_prev_net_time = None


def get_cpu():
    cpu_percent = psutil.cpu_percent(interval=1)
    load_1, load_5, load_15 = os.getloadavg()
    return {
        "percent": cpu_percent,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "cpu_count": psutil.cpu_count(),
    }


def get_memory():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total": mem.total,
        "available": mem.available,
        "percent": mem.percent,
        "swap_total": swap.total,
        "swap_used": swap.used,
        "swap_percent": swap.percent,
    }


def get_disk(paths):
    results = {}
    for path in paths:
        try:
            usage = psutil.disk_usage(path)
            # Para display, remove o prefixo /rootfs
            display = path.replace("/rootfs", "") or "/"
            results[display] = {
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": usage.percent,
                "path": path,
            }
        except (PermissionError, FileNotFoundError) as e:
            logger.warning(f"Não foi possível ler disco {path}: {e}")
    return results


def get_network():
    global _prev_net_io, _prev_net_time

    current = psutil.net_io_counters(pernic=False)
    current_time = time.time()

    sent_rate = 0.0
    recv_rate = 0.0
    if _prev_net_io is not None:
        elapsed = current_time - _prev_net_time
        if elapsed > 0:
            sent_rate = (current.bytes_sent - _prev_net_io.bytes_sent) / elapsed
            recv_rate = (current.bytes_recv - _prev_net_io.bytes_recv) / elapsed

    _prev_net_io = current
    _prev_net_time = current_time

    return {
        "bytes_sent_total": current.bytes_sent,
        "bytes_recv_total": current.bytes_recv,
        "bytes_sent_rate": max(0.0, sent_rate),
        "bytes_recv_rate": max(0.0, recv_rate),
    }


def get_processes():
    total = 0
    zombie_count = 0
    for proc in psutil.process_iter(["status"]):
        try:
            total += 1
            if proc.info["status"] == psutil.STATUS_ZOMBIE:
                zombie_count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {
        "total": total,
        "zombie_count": zombie_count,
    }


def get_docker_containers(ignore=None):
    ignore_set = set(ignore or [])
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            if c.name in ignore_set:
                continue
            health = c.attrs.get("State", {}).get("Health") or {}
            result.append({
                "name": c.name,
                "status": c.status,  # running, exited, paused, restarting, ...
                "health": health.get("Status", "none"),  # healthy, unhealthy, starting, none
            })
        return result
    except Exception as e:
        logger.warning(f"Erro ao ligar ao Docker: {e}")
        return []


def get_ssh_failures(hours=1):
    """Conta tentativas SSH falhadas na última hora via auth.log."""
    log_paths = ["/var/log/auth.log", "/var/log/secure"]
    count = 0
    now = datetime.now()
    cutoff = now - timedelta(hours=hours)
    current_year = now.year

    for log_path in log_paths:
        if not Path(log_path).exists():
            continue
        try:
            # Lê apenas os últimos 200KB para eficiência
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 200_000))
                content = f.read().decode("utf-8", errors="ignore")

            for line in content.splitlines():
                if "Failed password" not in line and "Invalid user" not in line:
                    continue
                try:
                    # Formato: "Mar 11 10:23:45 ..."
                    ts_str = " ".join(line.split()[:3])
                    ts = datetime.strptime(f"{current_year} {ts_str}", "%Y %b %d %H:%M:%S")
                    # Corrição de viragem de ano
                    if ts > now:
                        ts = ts.replace(year=current_year - 1)
                    if ts >= cutoff:
                        count += 1
                except ValueError:
                    pass
            break  # usou o primeiro ficheiro encontrado
        except Exception as e:
            logger.warning(f"Erro a ler {log_path}: {e}")

    return {"failures_last_hour": count}


def get_uptime():
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time
    return {
        "boot_time": boot_time,
        "uptime_seconds": uptime_seconds,
        "uptime_human": _format_uptime(uptime_seconds),
    }


def _format_uptime(seconds):
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def collect_all(config):
    disk_paths = config.get("disk", {}).get("check_paths", ["/rootfs"])
    ignore_containers = config.get("docker", {}).get("ignore_containers", [])
    return {
        "cpu": get_cpu(),
        "memory": get_memory(),
        "disk": get_disk(disk_paths),
        "network": get_network(),
        "processes": get_processes(),
        "docker": get_docker_containers(ignore=ignore_containers),
        "ssh": get_ssh_failures(),
        "uptime": get_uptime(),
        "timestamp": datetime.now(),
    }
