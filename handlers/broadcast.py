import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from handlers.auth import requires_auth

logger = logging.getLogger(__name__)

# States for the broadcast conversation
WAITING_TEXT, WAITING_FILE, WAITING_TARGET, WAITING_USER_ID, WAITING_CONFIRM = range(5)

@requires_auth
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for the broadcast flow."""
    profile = context.user_data.get("profile")
    if profile["role"] not in ["admin", "superuser"]:
        await update.message.reply_text("⛔️ Доступ запрещен. Только для администраторов.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📢 **Создание новой рассылки**\n\n"
        "Введите текст уведомления.\n"
        "Примечание: Бот автоматически добавит префикс «Уважаемые коллеги, ...»\n\n"
        "Отправьте /cancel для отмены.",
        parse_mode="Markdown"
    )
    return WAITING_TEXT

async def broadcast_get_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store the message text and ask for a file."""
    context.user_data["bc_text"] = update.message.text
    
    keyboard = [[InlineKeyboardButton("Без файла ⏩", callback_data="skip_file")]]
    await update.message.reply_text(
        "📎 **Прикрепите файл (фото или документ)**\n\n"
        "Если файл не нужен, нажмите кнопку ниже.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return WAITING_FILE

async def broadcast_get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store file info (photo or document)."""
    if update.message.photo:
        context.user_data["bc_file_id"] = update.message.photo[-1].file_id
        context.user_data["bc_file_type"] = "photo"
    elif update.message.document:
        context.user_data["bc_file_id"] = update.message.document.file_id
        context.user_data["bc_file_type"] = "document"
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте фото или документ, либо нажмите «Без файла».")
        return WAITING_FILE

    return await ask_target(update, context)

async def broadcast_skip_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the 'No file' option."""
    query = update.callback_query
    await query.answer()
    context.user_data["bc_file_id"] = None
    context.user_data["bc_file_type"] = None
    return await ask_target(query, context)

async def ask_target(update_or_query, context):
    """Helper to ask for the target audience."""
    text = "🎯 **Выберите получателей**"
    keyboard = [
        [InlineKeyboardButton("👥 Всем пользователям", callback_data="target_all")],
        [InlineKeyboardButton("👤 Конкретному пользователю", callback_data="target_single")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    
    return WAITING_TARGET

async def broadcast_target_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle target selection (All vs Single)."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "target_all":
        context.user_data["bc_target"] = "all"
        return await show_summary(query, context)
    else:
        await query.edit_message_text("Введите Telegram ID пользователя:")
        return WAITING_USER_ID

async def broadcast_get_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get the specific user ID for targeted broadcast."""
    user_id_str = update.message.text
    if not user_id_str.isdigit():
        await update.message.reply_text("❌ Ошибка: Введите корректный числовой Telegram ID.")
        return WAITING_USER_ID
    
    context.user_data["bc_target"] = int(user_id_str)
    return await show_summary(update, context)

async def show_summary(update_or_query, context):
    """Show the final preview before sending."""
    text = context.user_data["bc_text"]
    target = context.user_data["bc_target"]
    target_str = "Все сотрудники" if target == "all" else f"Пользователь {target}"
    has_file = "Да" if context.user_data["bc_file_id"] else "Нет"

    summary = (
        "📊 **Предпросмотр рассылки**\n\n"
        f"📝 Текст: `Уважаемые коллеги, {text}`\n"
        f"📎 Файл: {has_file}\n"
        f"🎯 Получатели: {target_str}\n\n"
        "Отправить рассылку прямо сейчас?"
    )
    
    keyboard = [
        [InlineKeyboardButton("🚀 ОТПРАВИТЬ", callback_data="confirm_send")],
        [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel_bc")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(summary, reply_markup=markup, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(summary, reply_markup=markup, parse_mode="Markdown")
    
    return WAITING_CONFIRM

async def broadcast_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform the actual broadcast."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_bc":
        await query.edit_message_text("❌ Рассылка отменена.")
        return ConversationHandler.END

    await query.edit_message_text("⏳ Рассылка запущена... Пожалуйста, подождите.")
    
    text = f"Уважаемые коллеги, {context.user_data['bc_text']}"
    file_id = context.user_data.get("bc_file_id")
    file_type = context.user_data.get("bc_file_type")
    target = context.user_data["bc_target"]
    
    # Get recipients
    if target == "all":
        all_users = await db.list_users()
        recipients = [u["telegram_id"] for u in all_users if u.get("telegram_id")]
    else:
        recipients = [target]

    success_count = 0
    fail_count = 0
    
    for uid in recipients:
        try:
            if file_id:
                if file_type == "photo":
                    await context.bot.send_photo(chat_id=uid, photo=file_id, caption=text)
                else:
                    await context.bot.send_document(chat_id=uid, document=file_id, caption=text)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            success_count += 1
        except Exception as e:
            logger.warning(f"Could not send broadcast to {uid}: {e}")
            fail_count += 1

    await query.message.reply_text(
        f"✅ **Рассылка завершена!**\n\n"
        f"📬 Доставлено: {success_count}\n"
        f"❌ Ошибок: {fail_count}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation."""
    await update.message.reply_text("❌ Действие отменено.")
    return ConversationHandler.END

# The ConversationHandler to be registered in main.py
broadcast_handler = ConversationHandler(
    entry_points=[CommandHandler("broadcast", broadcast_start)],
    states={
        WAITING_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_get_text)],
        WAITING_FILE: [
            MessageHandler(filters.PHOTO | filters.Document.ALL, broadcast_get_file),
            CallbackQueryHandler(broadcast_skip_file, pattern="^skip_file$")
        ],
        WAITING_TARGET: [CallbackQueryHandler(broadcast_target_callback, pattern="^target_")],
        WAITING_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_get_user_id)],
        WAITING_CONFIRM: [CallbackQueryHandler(broadcast_confirm_callback, pattern="^(confirm_send|cancel_bc)$")],
    },
    fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    persistent=True,
    name="broadcast_conv"
)
