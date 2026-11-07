import logging
import os
import sys
import time
import yaml

from .collectors import collect_all
from .container_disk import collect_container_disk, container_disk_to_dicts
from .snapshot import write_snapshot
from .checker import ThresholdChecker
from .telegram import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yml") -> dict:
    with open(path) as f:
        config = yaml.safe_load(f)

    # Variáveis de ambiente sobrepõem-se ao config.yml (para segredos)
    tg = config.setdefault("telegram", {})
    tg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token", "")
    tg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or tg.get("chat_id", "")
    return config


CONTAINER_DISK_INTERVAL = 3600  # seconds between container_disk.json refreshes


class SnapshotWriter:
    """Writes metrics.json every cycle and container_disk.json at most hourly."""

    def __init__(self, status_dir: str):
        self.status_dir = status_dir
        self._last_disk: float | None = None

    def write(self, metrics: dict) -> None:
        try:
            write_snapshot(os.path.join(self.status_dir, "metrics.json"), metrics)
        except Exception as e:
            logger.error(f"Falha ao escrever metrics.json: {e}", exc_info=True)

        now = time.monotonic()
        if self._last_disk is not None and now - self._last_disk < CONTAINER_DISK_INTERVAL:
            return
        self._last_disk = now  # throttle failures too: the scan is expensive
        try:
            containers = container_disk_to_dicts(collect_container_disk())
            write_snapshot(os.path.join(self.status_dir, "container_disk.json"),
                           {"containers": containers})
        except Exception as e:
            logger.error(f"Falha ao escrever container_disk.json: {e}", exc_info=True)


def run():
    config = load_config()

    tg = config["telegram"]
    if not tg.get("bot_token") or not tg.get("chat_id"):
        logger.error(
            "TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID são obrigatórios. "
            "Define-os no .env ou no config.yml."
        )
        sys.exit(1)

    notifier = TelegramNotifier(tg["bot_token"], tg["chat_id"])
    checker = ThresholdChecker(config)

    schedule = config.get("schedule", {})
    check_interval = schedule.get("check_interval_seconds", 300)

    status_dir = os.environ.get("STATUS_DIR")
    snapshots = SnapshotWriter(status_dir) if status_dir else None

    logger.info(f"Monitor iniciado — intervalo: {check_interval}s")
    notifier.send("✅ <b>Monitor iniciado</b>\nHetzner VM monitoring ativo.")

    while True:
        cycle_start = time.monotonic()
        try:
            metrics = collect_all(config)
            if snapshots:
                snapshots.write(metrics)

            # Verificar thresholds e enviar alertas
            alerts = checker.check(metrics)
            for alert in alerts:
                notifier.send_alert(alert)

            # Verificar heartbeats dos bots
            heartbeat_alerts = checker.check_heartbeats()
            for alert in heartbeat_alerts:
                notifier.send_alert(alert)

        except Exception as e:
            logger.error(f"Erro no ciclo de monitorização: {e}", exc_info=True)

        # Aguarda até ao próximo ciclo (desconta o tempo gasto)
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, check_interval - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
