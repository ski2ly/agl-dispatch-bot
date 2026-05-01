import os
import logging
import time
from functools import wraps
from telegram import Update, BotCommand, BotCommandScopeChat
from telegram.ext import ContextTypes
from database import db

logger = logging.getLogger(__name__)
SUPERUSER_IDS = [int(x.strip()) for x in os.getenv("SUPERUSER_IDS", "2100694356").split(",") if x.strip()]

async def _get_profile(user_id, fallback_name=None):
    """Get user profile from DB or SUPERUSER fallback."""
    profile = await db.get_user(user_id)
    if not profile and int(user_id) in SUPERUSER_IDS:
        profile = {
            "name": fallback_name or "Admin", 
            "role": "superuser", 
            "telegram_id": user_id
        }
    return profile

async def _set_scoped_commands(bot, user_id, role):
    """Set custom menu commands based on user role."""
    ADMIN_CMDS = [
        BotCommand("list", "📜 Список заявок"),
        BotCommand("new", "🆕 Новая заявка"),
        BotCommand("stats", "📊 Статистика"),
        BotCommand("help", "❓ Справка"),
    ]
    MANAGER_CMDS = [
        BotCommand("list", "📜 Все заявки"),
        BotCommand("new", "🆕 Новая заявка"),
        BotCommand("help", "❓ Справка"),
    ]
    cmds = ADMIN_CMDS if role in ["admin", "superuser"] else MANAGER_CMDS
    try:
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=user_id))
    except Exception as e:
        logger.error(f"Failed to set scoped commands for {user_id}: {e}")

def requires_auth(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user: return
        user_id = update.effective_user.id
        
        # Profile TTL cache (1 hour)
        profile_age = time.time() - context.user_data.get("profile_updated_at", 0)
        if profile_age > 3600 or "profile" not in context.user_data:
            profile = await _get_profile(user_id, update.effective_user.first_name)
            if profile:
                context.user_data["profile"] = profile
                context.user_data["profile_updated_at"] = time.time()
                await _set_scoped_commands(context.bot, user_id, profile["role"])
            else:
                context.user_data["profile"] = None

        profile = context.user_data.get("profile")
        
        if not profile:
            # Check for login attempt — accept the typed key only if it looks like one.
            if update.message and update.message.text:
                text = update.message.text.strip()
                # Skip obviously non-key inputs (commands, chat) to avoid DB lookups on every message.
                if text and not text.startswith("/") and len(text) <= 64:
                    user_info = await db.link_telegram_to_key(text, user_id)
                    if user_info:
                        context.user_data.pop("profile_updated_at", None)  # Trigger reload
                        await update.message.reply_text(
                            f"✅ Авторизация успешна!\nДобро пожаловать, {user_info['name']}."
                        )
                        return

            msg_text = "⛔️ Доступ закрыт. Отправьте ваш ключ доступа для входа."
            if update.message:
                await update.message.reply_text(msg_text)
            elif update.callback_query:
                await update.callback_query.answer(msg_text, show_alert=True)
            return

        return await func(update, context)
    return wrapper
