import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from .utils import bytes_human as _bytes_human

logger = logging.getLogger(__name__)


@dataclass
class AlertState:
    last_alert_time: Optional[datetime] = None
    active: bool = False


class ThresholdChecker:
    def __init__(self, config: dict):
        thr = config.get("thresholds", {})
        self.cpu_percent = thr.get("cpu_percent", 85)
        self.cpu_load_mult = thr.get("cpu_load_multiplier", 2.0)
        self.mem_percent = thr.get("memory_percent", 90)
        self.swap_percent = thr.get("swap_percent", 50)
        self.disk_percent = thr.get("disk_percent", 85)
        self.ssh_failures = thr.get("ssh_failures_per_hour", 20)
        self.zombie_count = thr.get("zombie_count", 5)

        cooldown_min = config.get("alerts", {}).get("cooldown_minutes", 60)
        self.cooldown = timedelta(minutes=cooldown_min)

        self._states: Dict[str, AlertState] = {}
        self._prev_boot_time: Optional[float] = None

    def check(self, metrics: dict) -> list:
        alerts = []

        # --- CPU ---
        cpu = metrics["cpu"]
        if cpu["percent"] > self.cpu_percent:
            msg = f"CPU alto: {cpu['percent']:.1f}% (limite: {self.cpu_percent}%)"
            self._maybe_alert(alerts, "cpu_percent", "warning", msg)
        else:
            self._clear("cpu_percent")

        load_limit = cpu["cpu_count"] * self.cpu_load_mult
        if cpu["load_1"] > load_limit:
            msg = (
                f"Load alto: {cpu['load_1']:.2f} "
                f"(CPUs: {cpu['cpu_count']}, limite: {load_limit:.1f})"
            )
            self._maybe_alert(alerts, "cpu_load", "warning", msg)
        else:
            self._clear("cpu_load")

        # --- Memória ---
        mem = metrics["memory"]
        if mem["percent"] > self.mem_percent:
            msg = (
                f"RAM alta: {mem['percent']:.1f}% "
                f"(limite: {self.mem_percent}%, "
                f"disponível: {_bytes_human(mem['available'])})"
            )
            self._maybe_alert(alerts, "memory", "warning", msg)
        else:
            self._clear("memory")

        if mem["swap_total"] > 0 and mem["swap_percent"] > self.swap_percent:
            msg = f"Swap alto: {mem['swap_percent']:.1f}% (limite: {self.swap_percent}%)"
            self._maybe_alert(alerts, "swap", "warning", msg)
        else:
            self._clear("swap")

        # --- Disco ---
        for mountpoint, disk in metrics["disk"].items():
            key = f"disk_{mountpoint}"
            if disk["percent"] > self.disk_percent:
                msg = (
                    f"Disco {mountpoint}: {disk['percent']:.1f}% usado "
                    f"(limite: {self.disk_percent}%, livre: {_bytes_human(disk['free'])})"
                )
                self._maybe_alert(alerts, key, "warning", msg)
            else:
                self._clear(key)

        # --- SSH ---
        ssh_count = metrics["ssh"]["failures_last_hour"]
        if ssh_count >= self.ssh_failures:
            msg = (
                f"SSH: {ssh_count} tentativas falhadas na última hora "
                f"(limite: {self.ssh_failures})"
            )
            self._maybe_alert(alerts, "ssh_failures", "critical", msg)
        else:
            self._clear("ssh_failures")

        # --- Docker ---
        for container in metrics["docker"]:
            name = container["name"]

            if container["status"] not in ("running",):
                key = f"docker_{name}_down"
                msg = f"Container '{name}' está {container['status']}"
                self._maybe_alert(alerts, key, "critical", msg)
            else:
                self._clear(f"docker_{name}_down")

            if container["health"] == "unhealthy":
                key = f"docker_{name}_unhealthy"
                msg = f"Container '{name}' unhealthy (healthcheck a falhar)"
                self._maybe_alert(alerts, key, "critical", msg)
            else:
                self._clear(f"docker_{name}_unhealthy")

        # --- Reinício inesperado ---
        current_boot = metrics["uptime"]["boot_time"]
        if self._prev_boot_time is not None and current_boot != self._prev_boot_time:
            alerts.append({
                "type": "reboot",
                "level": "critical",
                "message": (
                    f"Servidor reiniciou! "
                    f"Uptime atual: {metrics['uptime']['uptime_human']}"
                ),
            })
        self._prev_boot_time = current_boot

        # --- Processos zombie ---
        zombies = metrics["processes"]["zombie_count"]
        if zombies >= self.zombie_count:
            msg = f"{zombies} processos zombie detetados (limite: {self.zombie_count})"
            self._maybe_alert(alerts, "zombies", "warning", msg)
        else:
            self._clear("zombies")

        return alerts

    # --- helpers internos ---

    def _maybe_alert(self, alerts: list, key: str, level: str, message: str):
        state = self._states.setdefault(key, AlertState())
        if not state.active or self._cooldown_elapsed(state):
            alerts.append({"type": key, "level": level, "message": message})
            state.last_alert_time = datetime.now()
            state.active = True
            logger.warning(f"[ALERT] {message}")

    def _clear(self, key: str):
        if key in self._states:
            self._states[key].active = False

    def _cooldown_elapsed(self, state: AlertState) -> bool:
        if state.last_alert_time is None:
            return True
        return datetime.now() - state.last_alert_time >= self.cooldown


