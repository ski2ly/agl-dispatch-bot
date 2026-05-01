import os
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

# Load env before other imports
load_dotenv()

from database import db
from ai_assistant import ai_assistant
from api.server import setup_api, web
from handlers.commands import start_cmd, help_cmd, list_cmd, stats_cmd, profile_cmd, my_requests_cmd, users_cmd, logs_cmd
from handlers.ai_handlers import handle_text_msg, handle_voice, handle_attachment
from handlers.callbacks import handle_callbacks
from handlers.discussion import handle_discussion_forward
from handlers.cron import reminder_cron

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — log everything so a single failing handler doesn't kill the bot silently."""
    logger.error("Unhandled error in handler", exc_info=context.error)


async def _supervised_cron(coro_factory, name: str):
    """Run a long-lived async task and log if it ever crashes (otherwise it'd die silently)."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"{name} crashed, restarting in 60s: {e}", exc_info=True)
            await asyncio.sleep(60)


async def post_init(application: Application):
    """Run startup tasks after bot initialization."""
    # 1. Database
    await db.init_db()

    # 2. Set Bot Commands
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("help", "Помощь и руководство"),
        BotCommand("list", "Последние открытые заявки"),
        BotCommand("my_requests", "Мои созданные заявки"),
        BotCommand("profile", "Мой профиль и роль"),
        BotCommand("cancel", "Отменить текущее действие"),
        BotCommand("ai", "Создать заявку или задать вопрос ИИ")
    ]
    await application.bot.set_my_commands(commands)

    # 3. Start reminder cron under a supervisor so it auto-restarts on crash.
    bot = application.bot
    asyncio.create_task(_supervised_cron(lambda: reminder_cron(bot), "reminder_cron"))

    # 4. Start web server
    # client_max_size limits a single request body. Default in aiohttp is 1MB —
    # we tighten to 256KB since we never accept file uploads on JSON endpoints.
    app = web.Application(client_max_size=256 * 1024)
    app["bot"] = application.bot
    setup_api(app)

    port = int(os.getenv("PORT", 8000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"🚀 System initialized: DB, Cron, and API Server are live on port {port}.")

async def post_shutdown(application: Application):
    """Cleanup tasks after bot shutdown."""
    await db.close()
    logger.info("🛑 System shut down successfully.")

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not found in .env")
        return

    application = Application.builder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    application.add_error_handler(_error_handler)

    # Commands
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("profile", profile_cmd))
    application.add_handler(CommandHandler("my_requests", my_requests_cmd))
    application.add_handler(CommandHandler("users", users_cmd))
    application.add_handler(CommandHandler("logs", logs_cmd))
    application.add_handler(CommandHandler("ai", handle_text_msg))
    application.add_handler(CommandHandler("new", help_cmd)) # Alias for guide
    application.add_handler(CommandHandler("cancel", handle_text_msg)) # Handled in handle_text_msg logic

    # AI & Attachments
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_attachment))
    
    # General Text (AI Parsing) — only in private chats to avoid group interference
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_text_msg))

    # Callbacks
    application.add_handler(CallbackQueryHandler(handle_callbacks))

    # Discussion group
    application.add_handler(MessageHandler(filters.ChatType.GROUP | filters.ChatType.SUPERGROUP, handle_discussion_forward))

    # Run bot
    logger.info("🤖 Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
