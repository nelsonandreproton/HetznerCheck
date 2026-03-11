import logging
import threading
import requests
from datetime import datetime

from .utils import bytes_human as _bytes_human, rate_human as _rate_human

logger = logging.getLogger(__name__)

LEVEL_ICON = {
    "critical": "🔴",
    "warning": "🟡",
    "info": "🟢",
}

CONTAINER_STATUS_ICON = {
    "running": "🟢",
    "exited": "🔴",
    "paused": "🟡",
    "restarting": "🟡",
    "dead": "🔴",
}


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.chat_id = chat_id
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send(self, text: str) -> bool:
        try:
            resp = requests.post(
                self._url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if not resp.ok:
                logger.error(
                    f"Erro ao enviar mensagem Telegram: {resp.status_code} — {resp.json().get('description', resp.text)}"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem Telegram: {e}")
            return False

    def send_alert(self, alert: dict) -> bool:
        icon = LEVEL_ICON.get(alert["level"], "⚪")
        level_label = alert["level"].upper()
        text = f"{icon} <b>{level_label}</b>\n\n{alert['message']}"
        return self.send(text)

    def send_daily_summary(self, metrics: dict) -> bool:
        text = _format_summary(metrics)
        return self.send(text)


class CommandListener:
    """Escuta comandos Telegram via long-polling numa thread de fundo.

    Expõe ``status_requested`` (threading.Event) que é ativado quando
    o comando /server_status é recebido do chat_id autorizado.
    """

    COMMAND = "/server_status"

    def __init__(self, bot_token: str, authorized_chat_id: str):
        self._url_base = f"https://api.telegram.org/bot{bot_token}"
        self._authorized_chat_id = str(authorized_chat_id)
        self._offset = 0
        self.status_requested = threading.Event()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="CommandListener")

    def start(self):
        self._thread.start()
        logger.info("CommandListener iniciado — a aguardar /server_status")

    def stop(self):
        self._stop_event.set()

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                updates = self._get_updates(timeout=30)
                for update in updates:
                    self._offset = update["update_id"] + 1
                    self._handle(update)
            except Exception as e:
                logger.warning(f"CommandListener erro: {e}")
                self._stop_event.wait(5)  # pausa breve antes de tentar novamente

    def _get_updates(self, timeout: int) -> list:
        resp = requests.get(
            f"{self._url_base}/getUpdates",
            params={"offset": self._offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 5,
        )
        if not resp.ok:
            logger.warning(f"getUpdates falhou: {resp.status_code}")
            return []
        return resp.json().get("result", [])

    def _handle(self, update: dict):
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()

        if chat_id != self._authorized_chat_id:
            logger.warning(f"Comando recebido de chat_id não autorizado: {chat_id}")
            return

        if text == self.COMMAND or text.startswith(self.COMMAND + " ") or text.startswith(self.COMMAND + "@"):
            logger.info(f"Comando {self.COMMAND} recebido — a sinalizar envio imediato")
            self.status_requested.set()


def _format_summary(metrics: dict) -> str:
    now = metrics["timestamp"].strftime("%Y-%m-%d %H:%M")
    cpu = metrics["cpu"]
    mem = metrics["memory"]
    disk = metrics["disk"]
    net = metrics["network"]
    uptime = metrics["uptime"]
    docker_list = metrics["docker"]
    ssh = metrics["ssh"]
    procs = metrics["processes"]

    # Disco
    disk_lines = []
    for mountpoint, d in disk.items():
        icon = "🔴" if d["percent"] > 85 else "🟡" if d["percent"] > 70 else "🟢"
        disk_lines.append(
            f"  {icon} {mountpoint}: {d['percent']:.1f}%"
            f" ({_bytes_human(d['free'])} livre)"
        )

    # Docker
    running = [c for c in docker_list if c["status"] == "running"]
    stopped = [c for c in docker_list if c["status"] != "running"]
    unhealthy = [c for c in docker_list if c["health"] == "unhealthy"]
    docker_summary = f"{len(running)} a correr"
    if stopped:
        docker_summary += f", {len(stopped)} parado(s) ⚠️"
    if unhealthy:
        docker_summary += f", {len(unhealthy)} unhealthy 🔴"

    docker_detail = []
    for c in docker_list:
        icon = CONTAINER_STATUS_ICON.get(c["status"], "⚪")
        health = f" [{c['health']}]" if c["health"] not in ("none", "healthy") else ""
        docker_detail.append(f"  {icon} {c['name']}{health}")

    # SSH
    ssh_icon = "🔴" if ssh["failures_last_hour"] >= 20 else "🟡" if ssh["failures_last_hour"] >= 5 else "🟢"

    lines = [
        f"📊 <b>Resumo Diário</b> — {now}",
        "",
        f"🖥 <b>Sistema</b>",
        f"  Uptime: {uptime['uptime_human']}",
        f"  Processos: {procs['total']} total, {procs['zombie_count']} zombie",
        f"  {ssh_icon} SSH falhas (1h): {ssh['failures_last_hour']}",
        "",
        f"⚡ <b>CPU</b>",
        f"  Uso: {cpu['percent']:.1f}%",
        f"  Load avg: {cpu['load_1']:.2f} / {cpu['load_5']:.2f} / {cpu['load_15']:.2f}",
        f"  Núcleos: {cpu['cpu_count']}",
        "",
        f"🧠 <b>Memória</b>",
        f"  RAM: {mem['percent']:.1f}% ({_bytes_human(mem['available'])} disponível)",
        f"  Swap: {mem['swap_percent']:.1f}% ({_bytes_human(mem['swap_used'])} usado)",
        "",
        f"💾 <b>Disco</b>",
        *disk_lines,
        "",
        f"🌐 <b>Rede</b>",
        f"  ↑ {_rate_human(net['bytes_sent_rate'])}  ↓ {_rate_human(net['bytes_recv_rate'])}",
        "",
        f"🐳 <b>Docker</b> — {docker_summary}",
        *docker_detail,
    ]
    return "\n".join(lines)


