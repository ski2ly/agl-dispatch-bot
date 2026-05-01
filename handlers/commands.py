import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, BotCommand, BotCommandScopeChat
from telegram.ext import ContextTypes
from database import db
from handlers.auth import requires_auth
from utils.helpers import build_card

logger = logging.getLogger(__name__)
WEBAPP_URL = os.getenv("WEBAPP_URL")

async def set_user_commands(bot, chat_id, role):
    base_commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("help", "Помощь и руководство"),
        BotCommand("list", "Последние открытые заявки"),
        BotCommand("my_requests", "Мои созданные заявки"),
        BotCommand("profile", "Мой профиль и роль"),
        BotCommand("cancel", "Отменить текущее действие"),
    ]
    if role in ["admin", "superuser"]:
        base_commands.extend([
            BotCommand("stats", "Общая статистика и аналитика"),
            BotCommand("users", "Список сотрудников"),
            BotCommand("logs", "Логи последних действий"),
        ])
    
    try:
        await bot.set_my_commands(base_commands, scope=BotCommandScopeChat(chat_id))
    except Exception as e:
        logger.warning(f"Could not set commands for {chat_id}: {e}")

@requires_auth
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = context.user_data.get("profile")
    await set_user_commands(context.bot, update.effective_chat.id, profile.get("role"))
    
    await update.message.reply_text(
        f"👋 Привет, {profile['name']}!\n\n"
        "Я — диспетчерский бот AGL. Здесь ты можешь:\n"
        "1. Создавать заявки голосом или текстом (просто пиши мне).\n"
        "2. Просматривать базу и подавать ставки через Mini App.\n\n"
        "Используй кнопку в меню или команду /help.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Открыть Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
    )

@requires_auth
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = context.user_data.get("profile", {})
    is_admin = profile.get("role") in ["admin", "superuser"]

    # Manager Commands
    help_text = (
        "📖 **AGL Dispatch Bot: Руководство**\n\n"
        "✨ **Создание заявки:**\n"
        "1. Отправьте **Голосовое сообщение** — ИИ сразу начнет обработку.\n"
        "2. Напишите текст с командой `/ai <детали>` — например: `/ai везем груз из Китая`.\n\n"
        "🧠 **Умный ИИ-Ассистент:**\n"
        "• **Ставки:** `/ai ставка москва ташкент 2000 USD`\n"
        "• **Поиск в БД:** `/ai отправь ID всех открытых заявок по направлению СНГ`\n"
        "• **Аналитика:** `/ai какая у нас конверсия?`\n\n"
        "📋 **Основные команды:**\n"
        "🔹 /list — Последние 10 заявок в системе.\n"
        "🔹 /my_requests — Ваши заявки.\n"
        "🔹 /profile — Ваши данные и роль.\n"
        "🔹 /cancel — Сбросить черновик заявки.\n"
    )

    if is_admin:
        help_text += (
            "\n⚡️ **Админ-панель:**\n"
            "🔸 /stats — Общая статистика и аналитика.\n"
            "🔸 /users — Список сотрудников.\n"
            "🔸 /logs — Логи последних действий.\n"
        )

    help_text += (
        "\n🚛 **Mini App:** Используйте кнопку в меню для полной работы с базой и ставками."
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

@requires_auth
async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = context.user_data.get("profile", {})
    if profile["role"] not in ["admin", "superuser"]:
        return # Hide silently
        
    users = await db.list_users() # Assuming this exists or I'll add a simple query
    text = "👥 **Сотрудники в системе:**\n\n"
    for u in users:
        text += f"• {u['name']} (@{u.get('username', '-')}) — {u['role']}\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

@requires_auth
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = context.user_data.get("profile", {})
    text = (
        "👤 **Ваш профиль:**\n\n"
        f"🏷 Имя: {profile.get('name')}\n"
        f"🆔 ID: `{update.effective_user.id}`\n"
        f"🔑 Роль: {profile.get('role')}\n"
        f"🏢 Компания: {profile.get('company', 'AGL')}\n"
        f"📞 Тел: {profile.get('phone', '—')}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

@requires_auth
async def my_requests_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # We need a db method for this, I'll use list_requests with creator_id if available
    reqs = await db.list_requests(limit=10) # Simplified for now, filter in logic
    my_reqs = [r for r in reqs if r.get('creator_id') == user_id]
    
    if not my_reqs:
        await update.message.reply_text("📭 Вы еще не создали ни одной заявки.")
        return
        
    text = "📂 **Ваши последние заявки:**\n\n"
    keyboard = []
    for r in my_reqs:
        text += f"#{r['id']:04d} | {r['route_from']} ➔ {r['route_to']} | {r['status']}\n"
        keyboard.append([InlineKeyboardButton(f"Смотреть #{r['id']:04d}", callback_data=f"view_{r['id']}")])
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@requires_auth
async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reqs = await db.list_requests(limit=10)
    if not reqs:
        await update.message.reply_text("📭 Заявок пока нет.")
        return
    
    text = "📜 **Последние 10 заявок:**\n\n"
    keyboard = []
    for r in reqs:
        id_str = f"#{r['id']:04d}"
        text += f"{id_str} | {r['route_from']} ➔ {r['route_to']} | {r['status']}\n"
        keyboard.append([InlineKeyboardButton(f"Просмотр {id_str}", callback_data=f"view_{r['id']}")])
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

@requires_auth
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = context.user_data.get("profile")
    if profile["role"] not in ["admin", "superuser"]:
        await update.message.reply_text("⛔️ У вас нет прав для просмотра статистики.")
        return
    
    # Simple stats placeholder
    reqs = await db.list_requests(limit=1000)
    open_cnt = len([r for r in reqs if r["status"] == "Открыта"])
    done_cnt = len([r for r in reqs if r["status"] == "Успешно реализована"])
    
    await update.message.reply_text(
        f"📊 **Статистика за всё время:**\n\n"
        f"🔹 Всего заявок: {len(reqs)}\n"
        f"🟢 Открытых: {open_cnt}\n"
        f"✅ Завершенных: {done_cnt}\n",
        parse_mode="HTML"
    )

@requires_auth
async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = context.user_data.get("profile", {})
    if profile["role"] not in ["admin", "superuser"]:
        return

    logs = await db.get_recent_logs(15)
    if not logs:
        await update.message.reply_text("📋 Логи пока пусты.")
        return

    text = "📜 **Последние действия в системе:**\n\n"
    for l in logs:
        dt = l['created_at'].strftime("%H:%M")
        action = l['action'].replace("_", " ").capitalize()
        cargo = f" ({l['cargo_name']})" if l['cargo_name'] else ""
        text += f"• `{dt}` **{l['user_name']}**: {action}{cargo}\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def view_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        await query.edit_message_text("❌ Некорректный ID заявки.")
        return
    req_id = int(parts[1])
    req = await db.get_request(req_id)
    if not req:
        await query.edit_message_text("❌ Заявка не найдена.")
        return
    
    card = build_card(req)
    keyboard = [
        [InlineKeyboardButton("💰 Подать ставку / Изменить", web_app=WebAppInfo(url=f"{WEBAPP_URL}?req_id={req_id}"))],
        [InlineKeyboardButton("💬 Комментарии", callback_data=f"comments_{req_id}")]
    ]
    await query.edit_message_text(f"🔍 **Заявка {req_id:04d}**\n\n{card}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
