"""GarminBot integration: /server_status and /container_disk command handlers.

Usage in GarminBot's main.py (after app = tg_bot.build_application()):

    import os
    from monitor.bot_handler import register_server_status_handler, register_container_disk_handler
    register_server_status_handler(
        app,
        config_path=os.environ.get("HETZNERCHECK_CONFIG_PATH", "/hetznercheck/config.yml"),
    )
    register_container_disk_handler(app)
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


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


def register_server_status_handler(app: Application, config_path: str) -> None:
    """Register the /server_status command on an existing Application instance."""
    handler_fn = _make_handler(config_path)
    app.add_handler(CommandHandler("server_status", handler_fn))
    logger.info("HetznerCheck /server_status handler registered (config=%s)", config_path)


def register_container_disk_handler(app: Application) -> None:
    """Register the /container_disk command on an existing Application instance."""
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
