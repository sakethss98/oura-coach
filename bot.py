"""Telegram glue: receive messages from my chat only, hand them to chat.Coach, send the reply.

Usage: python bot.py [--fixtures]
Slow work (Oura, calendar, OpenAI, SQLite) runs in asyncio.to_thread so the bot stays responsive.
"""
import argparse
import asyncio
import logging
import sqlite3
import traceback

from langgraph.checkpoint.sqlite import SqliteSaver
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import db
from chat import HELP, Coach
from config import CHECKPOINT_DB_PATH, DEMO_CHECKPOINT_DB_PATH, DEMO_DB_PATH, env, redact
from graph import build_graph

COMMANDS = ["checkin", "plan", "food", "note", "undo", "today", "why", "help", "start"]
TELEGRAM_MAX_CHARS = 4000

logger = logging.getLogger("oura-coach")


class RedactingFormatter(logging.Formatter):
    """Formats as usual, then removes every secret (the bot token is in Telegram API URLs)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


async def reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text[:TELEGRAM_MAX_CHARS])


def make_handlers(coach: Coach):
    async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        command = message.text.split()[0].lstrip("/").split("@")[0].lower()
        if command in {"help", "start"}:
            await reply(update, HELP)
            return
        args = " ".join(context.args or [])
        logger.info("command /%s", command)
        await reply(update, await asyncio.to_thread(coach.handle_command, command, args, message.date))

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        logger.info("message received (%d chars)", len(message.text))
        await reply(update, await asyncio.to_thread(coach.handle_text, message.text, message.date))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        logger.error("handler failed:\n%s",
                     "".join(traceback.format_exception(type(error), error, error.__traceback__)))
        if isinstance(update, Update) and update.effective_message:
            await reply(update, f"Sorry, that failed ({type(error).__name__}). Details are in the bot log.")

    return on_command, on_text, on_error


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Telegram coach.")
    parser.add_argument("--fixtures", action="store_true",
                        help="use fixtures/ instead of live Oura and Calendar; writes to demo.db")
    args = parser.parse_args()
    setup_logging()

    if args.fixtures:
        db.set_db_path(DEMO_DB_PATH)
    db.init_db()
    checkpoint_path = DEMO_CHECKPOINT_DB_PATH if args.fixtures else CHECKPOINT_DB_PATH
    # One connection for the checkpointer, shared across worker threads; SqliteSaver locks around it.
    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    coach = Coach(build_graph(SqliteSaver(conn)), use_fixtures=args.fixtures)

    on_command, on_text, on_error = make_handlers(coach)
    my_chat = filters.Chat(chat_id=int(env("TELEGRAM_CHAT_ID")))
    application = Application.builder().token(env("TELEGRAM_BOT_TOKEN")).build()
    application.add_handler(CommandHandler(COMMANDS, on_command, filters=my_chat))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & my_chat, on_text))
    application.add_error_handler(on_error)

    logger.info("bot started (%s)", "fixtures / demo.db" if args.fixtures else "live")
    try:
        application.run_polling()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
