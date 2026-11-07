"""GarminBot integration: /server_status and /container_disk command handlers.

Usage in GarminBot's main.py (after app = tg_bot.build_application()):

    import os
    from monitor.bot_handler import register_server_status_handler, register_container_disk_handler
    register_server_status_handler(
        app,
        config_path=os.environ.get("HETZNERCHECK_CONFIG_PATH", "/hetznercheck/config.yml"),
    )
    register_container_disk_handler(app)

Snapshot mode (no docker / host access needed in the bot container): pass
status_dir=os.environ.get("HETZNERCHECK_STATUS_DIR") to both register functions. The
handlers then read metrics.json / container_disk.json written by hetzner-monitor.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

STALE_AFTER_MINUTES = 15


def _age_lines(collected_at: datetime) -> str:
    """Footer with snapshot age, plus a warning if the data is stale."""
    from .snapshot import age_minutes

    age = age_minutes(collected_at)
    footer = f"\n\n🕒 dados de há {age} min"
    if age > STALE_AFTER_MINUTES:
        footer += f"\n⚠️ dados desatualizados (> {STALE_AFTER_MINUTES} min) — o monitor pode estar parado"
    return footer


def _make_snapshot_handler(status_dir: str):
    """Factory: /server_status handler that reads metrics.json (no docker, no /rootfs)."""
    from .snapshot import read_snapshot
    from .telegram import _format_summary

    async def server_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        try:
            snap = await asyncio.to_thread(read_snapshot, os.path.join(status_dir, "metrics.json"))
            if snap is None:
                await update.effective_message.reply_text(
                    "❌ Sem dados do monitor (metrics.json em falta ou inválido)."
                )
                return
            metrics, collected_at = snap
            metrics["timestamp"] = datetime.fromisoformat(metrics["timestamp"])
            text = _format_summary(metrics).replace(
                "📊 <b>Resumo Diário</b>",
                "📊 <b>Estado do Servidor</b>",
            ) + _age_lines(collected_at)
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("server_status (snapshot) failed: %s", exc, exc_info=True)
            await update.effective_message.reply_text("❌ Erro ao obter estado do servidor.")

    return server_status_command


def _make_handler(config_path: str):
    """Factory: creates the /server_status handler closed over its dependencies."""
    from .main import load_config
    from .collectors import collect_all
    from .telegram import _format_summary

    config = load_config(config_path)

    async def server_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        try:
            metrics = await asyncio.to_thread(collect_all, config)
            text = _format_summary(metrics).replace(
                "📊 <b>Resumo Diário</b>",
                "📊 <b>Estado do Servidor</b>",
            )
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("server_status failed: %s", exc, exc_info=True)
            await update.effective_message.reply_text("❌ Erro ao obter estado do servidor.")

    return server_status_command


def register_server_status_handler(
    app: Application, config_path: str | None = None, status_dir: str | None = None
) -> None:
    """Register the /server_status command on an existing Application instance.

    With status_dir, reads the monitor's snapshot; otherwise collects live from config_path.
    """
    if status_dir:
        handler_fn = _make_snapshot_handler(status_dir)
        logger.info("HetznerCheck /server_status handler registered (status_dir=%s)", status_dir)
    else:
        handler_fn = _make_handler(config_path)
        logger.info("HetznerCheck /server_status handler registered (config=%s)", config_path)
    app.add_handler(CommandHandler("server_status", handler_fn))


def _make_container_disk_snapshot_handler(status_dir: str):
    """Factory: /container_disk handler that reads container_disk.json."""
    from .container_disk import format_container_disk
    from .snapshot import read_snapshot

    async def container_disk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        try:
            snap = await asyncio.to_thread(
                read_snapshot, os.path.join(status_dir, "container_disk.json")
            )
            if snap is None:
                await update.effective_message.reply_text(
                    "❌ Sem dados do monitor (container_disk.json em falta ou inválido)."
                )
                return
            data, collected_at = snap
            text = format_container_disk(data["containers"])
            # keep the footer inside Telegram's 4096-char limit
            footer = _age_lines(collected_at)
            text = text[: 4096 - len(footer)] + footer
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("container_disk (snapshot) failed: %s", exc, exc_info=True)
            await update.effective_message.reply_text("❌ Erro ao obter uso de disco dos containers.")

    return container_disk_command


def register_container_disk_handler(app: Application, status_dir: str | None = None) -> None:
    """Register the /container_disk command on an existing Application instance.

    With status_dir, reads the monitor's snapshot; otherwise scans live (needs docker + /rootfs).
    """
    if status_dir:
        app.add_handler(CommandHandler("container_disk", _make_container_disk_snapshot_handler(status_dir)))
        logger.info("HetznerCheck /container_disk handler registered (status_dir=%s)", status_dir)
        return

    from .container_disk import collect_container_disk, format_container_disk

    async def container_disk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        try:
            msg = await update.effective_message.reply_text(
                "⏳ A calcular uso de disco... pode demorar alguns segundos."
            )
            results = await asyncio.to_thread(collect_container_disk)
            text = format_container_disk(results)
            await msg.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            logger.error("container_disk failed: %s", exc, exc_info=True)
            await update.effective_message.reply_text("❌ Erro ao obter uso de disco dos containers.")

    app.add_handler(CommandHandler("container_disk", container_disk_command))
    logger.info("HetznerCheck /container_disk handler registered")
