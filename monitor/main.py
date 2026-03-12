import logging
import os
import sys
import time
import yaml
from datetime import datetime, timedelta

from .collectors import collect_all
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


def next_run_at(hour: int, minute: int) -> datetime:
    """Devolve o próximo datetime para o resumo diário."""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


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
    daily_time_str = schedule.get("daily_summary_time", "08:00")

    try:
        daily_hour, daily_minute = map(int, daily_time_str.split(":"))
    except ValueError:
        logger.error(f"Formato inválido para daily_summary_time: '{daily_time_str}'. Use HH:MM.")
        sys.exit(1)

    next_daily = next_run_at(daily_hour, daily_minute)

    logger.info(
        f"Monitor iniciado — intervalo: {check_interval}s, "
        f"resumo diário: {daily_time_str}, "
        f"próximo resumo: {next_daily.strftime('%Y-%m-%d %H:%M')}"
    )
    notifier.send("✅ <b>Monitor iniciado</b>\nHetzner VM monitoring ativo.")

    while True:
        cycle_start = time.monotonic()
        try:
            metrics = collect_all(config)

            # Verificar thresholds e enviar alertas
            alerts = checker.check(metrics)
            for alert in alerts:
                notifier.send_alert(alert)

            # Resumo diário agendado
            if datetime.now() >= next_daily:
                logger.info("A enviar resumo diário...")
                notifier.send_daily_summary(metrics)
                next_daily = next_run_at(daily_hour, daily_minute)

        except Exception as e:
            logger.error(f"Erro no ciclo de monitorização: {e}", exc_info=True)

        # Aguarda até ao próximo ciclo (desconta o tempo gasto)
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, check_interval - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
