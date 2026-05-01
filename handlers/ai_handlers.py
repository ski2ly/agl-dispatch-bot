import os
import logging
import asyncio
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from database import db
from ai_assistant import ai_assistant
from utils.helpers import build_card
from handlers.auth import requires_auth

logger = logging.getLogger(__name__)
CHANNEL_ID = os.getenv("CHANNEL_ID")
DATA_DIR = os.getenv("DATA_DIR", "data")
MAX_AI_TEXT = 4000  # Hard cap on user text sent to OpenAI to limit cost & DoS surface.
AI_KEYWORDS = ["заявка", "перевозка", "груз", "везти", "маршрут", "отправить", "машина", "контейнер"]

async def process_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, info_prefix=""):
    if not ai_assistant.enabled: return
    user_id = update.effective_user.id
    
    if text and len(text) > MAX_AI_TEXT:
        text = text[:MAX_AI_TEXT]
        info_prefix = (info_prefix + "\n").lstrip() + "⚠️ Текст обрезан."

    try:
        if update.effective_chat:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            
        # Get persistent context from DB
        old_draft = await db.get_ai_context(user_id)
        
        # Smart routing
        intent_res = await ai_assistant.process_intent(text)
        if intent_res.get("error"):
            await update.message.reply_text(f"❌ Ошибка ИИ: {intent_res['error']}")
            return

        intent = intent_res.get("intent")
        args = intent_res.get("args", {})
        
        if intent == "cancel_request":
            if args.get("confirmed"):
                await db.clear_ai_context(user_id)
                await update.message.reply_text("❌ Создание заявки отменено. Черновик удален.")
            else:
                await update.message.reply_text("❓ Вы уверены, что хотите отменить создание этой заявки и очистить данные?")
            return

        elif intent == "recall_request":
            search_query = args.get("query", "")
            await update.message.reply_text(f"🔍 Ищу в базе: '{search_query}'...")
            found = await db.list_requests(limit=3, search=search_query)
            if not found:
                await update.message.reply_text("😔 Ничего не нашел по этому описанию.")
                return
            
            keyboard = []
            for r in found:
                keyboard.append([InlineKeyboardButton(f"#{r['id']:04d} | {r['route_from']} ➔ {r['route_to']}", callback_data=f"recall_{r['id']}")])
            
            await update.message.reply_text(
                "📍 Какую заявку вы имели в виду? (Я подгружу её данные в черновик)",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        elif intent == "create_bid":
            # ... (keep existing bid logic)
            search_str = args.get("route_search", "")
            amount = args.get("amount")
            currency = args.get("currency", "USD")
            open_reqs = await db.list_requests(limit=3, status="Открыта", search=search_str)
            if not open_reqs:
                await update.message.reply_text(f"❌ Не нашел открытых заявок по запросу '{search_str}'.")
                return
            keyboard = [[InlineKeyboardButton(f"#{r['id']:04d} | {r['route_from']} ➔ {r['route_to']}", callback_data=f"aibid_{r['id']}_{amount}_{currency}")] for r in open_reqs]
            await update.message.reply_text(f"💰 Ставка {amount} {currency}. К какой заявке прикрепить?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        elif intent == "query_database":
            await update.message.reply_text("📊 Ищу информацию...")
            answer = await ai_assistant.answer_db_query(text, db)
            await update.message.reply_text(answer, parse_mode="Markdown")
            return
            
        elif intent == "chat":
            await update.message.reply_text(intent_res.get("text", "Не понял вас."))
            return

        # Create/Update request logic
        templates = await db.list_requests(limit=3, search=text[:30])
        template_data = [{"id": t["id"], "route_from": t["route_from"], "route_to": t["route_to"], "cargo_name": t["cargo_name"]} for t in templates]

        parsed = await ai_assistant.parse_request(text, current_draft=old_draft, templates=template_data)
        if "error" in parsed:
            await update.message.reply_text(f"❌ Ошибка ИИ: {parsed['error']}")
            return

        if parsed.get("not_logistics"):
            if info_prefix: await update.message.reply_text(f"{info_prefix}\n🤖 Это не похоже на логистику.")
            return

        merged = ai_assistant.merge_parsed_data(old_draft, parsed)
        await db.save_ai_context(user_id, merged) # Persistent save

        preview = ai_assistant.build_preview(merged)
        
        is_ready = merged.get("ready_to_publish")
        if is_ready:
            keyboard = [
                [InlineKeyboardButton("🚀 Опубликовать в канал", callback_data="confirm_ai")],
                [InlineKeyboardButton("📝 Добавить детали", callback_data="more_ai")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_ai")]
            ]
            status_text = "✨ Готово к публикации!"
        else:
            keyboard = [
                [InlineKeyboardButton("📝 Дополнить данные", callback_data="more_ai")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_ai")]
            ]
            status_text = "📋 Черновик (недостаточно данных):"
        
        # Cleanup old bot message
        old_msg_id = context.user_data.get("last_ai_msg_id")
        if old_msg_id:
            try: await context.bot.delete_message(update.effective_chat.id, old_msg_id)
            except: pass
            
        sent_msg = await update.message.reply_text(f"{info_prefix}\n{status_text}\n\n{preview}\n\nВсе верно?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        context.user_data["last_ai_msg_id"] = sent_msg.message_id
        
    except Exception as e:
        logger.error(f"process_ai_message error: {e}", exc_info=True)

@requires_auth
async def handle_text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    low_text = text.lower().strip()
    if low_text in ["отмена", "/cancel"]:
        await db.clear_ai_context(update.effective_user.id)
        context.user_data.pop("last_ai_msg_id", None)
        await update.message.reply_text("❌ Создание заявки отменено. Черновик очищен.")
        return

    # In private chat, the bot treats all messages as conversation with the AI.
    content = text
    if text.startswith("/ai"):
        content = text[3:].strip()
        if not content:
            await update.message.reply_text("🤖 Я слушаю! Напишите подробности заявки или отправьте голосовое.")
            return

    await process_ai_message(update, context, content)

@requires_auth
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    wait_msg = await update.message.reply_text("🎤 Расшифровка аудио...")

    os.makedirs(DATA_DIR, exist_ok=True)
    # Per-message tempfile so concurrent voices from one user don't overwrite each other.
    tmp = tempfile.NamedTemporaryFile(prefix="voice_", suffix=".ogg", dir=DATA_DIR, delete=False)
    file_path = tmp.name
    tmp.close()

    text = None
    try:
        file = await voice.get_file()
        await file.download_to_drive(file_path)
        text = await ai_assistant.transcribe_audio(file_path)
    except Exception as e:
        logger.error(f"Voice processing failed: {e}", exc_info=True)
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

    if not text:
        await wait_msg.edit_text("❌ Не удалось расшифровать аудио.")
        return

    await wait_msg.delete()
    await process_ai_message(update, context, text, info_prefix=f"🎤 _«{text}»_")

async def confirm_ai_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        user_id = update.effective_user.id
        profile = context.user_data.get("profile", {})
        
        parsed = await db.get_ai_context(user_id)
        if not parsed:
            await query.edit_message_text("❌ Черновик не найден или уже опубликован.")
            return
        
        fields = ai_assistant.to_request_fields(parsed)
        fields.update({
            "creator_id": user_id, 
            "creator_name": profile.get("name"),
            "responsible": profile.get("name"), 
            "status": "Открыта"
        })
        
        req = await db.create_request(fields)
        req_id = req["id"]
        
        # Log and Comment
        await db.add_comment(req_id, user_id, profile.get("name"), "Заявка создана через AI", "system")
        await db.log_activity(req_id, user_id, profile.get("name"), "created_by_ai")
        
        # Channel notification
        if CHANNEL_ID:
            card = build_card(req)
            msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=card)
            await db.update_request(req_id, {"channel_msg_id": msg.message_id})
        
        # Cleanup
        await db.clear_ai_context(user_id)
        context.user_data.pop("last_ai_msg_id", None)
            
        await query.edit_message_text(f"✅ Готово! Заявка #{req_id:04d} создана и отправлена в канал.")
        
    except Exception as e:
        logger.error(f"confirm_ai_logic error: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Ошибка при создании: {e}")

@requires_auth
async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo or document attachments."""
    msg = update.effective_message
    if not msg: return
    
    # Check if we are in a dialogue for a new request
    if "ai_parsed" not in context.user_data:
        # Just acknowledge but don't do much if not in a request flow
        # Optional: AI can try to parse the file name or OCR
        return

    # In a real TMS we would save these to a cloud storage (S3/Telegraph/Direct link)
    # For now, we just log that an attachment was received
    await update.message.reply_text("📎 Вложение получено и будет прикреплено к заявке при сохранении.")
