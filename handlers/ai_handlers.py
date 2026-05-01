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
    # Cap input size — protects OpenAI bill and avoids huge prompts.
    if text and len(text) > MAX_AI_TEXT:
        text = text[:MAX_AI_TEXT]
        info_prefix = (info_prefix + "\n").lstrip() + "⚠️ Текст обрезан до 4000 символов."

    try:
        if update.effective_chat:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            
        # Smart routing
        intent_res = await ai_assistant.process_intent(text)
        if intent_res.get("error"):
            await update.message.reply_text(f"❌ Ошибка ИИ: {intent_res['error']}")
            return

        intent = intent_res.get("intent")
        
        if intent == "create_bid":
            args = intent_res.get("args", {})
            search_str = args.get("route_search", "")
            amount = args.get("amount")
            currency = args.get("currency", "USD")
            
            open_reqs = []
            
            # 1. Try to parse a direct request ID (e.g. "#0023", "23", "#23")
            import re
            id_match = re.search(r'#?(\d+)', search_str)
            if id_match:
                req_id = int(id_match.group(1))
                req = await db.get_request(req_id)
                if req and req["status"] == "Открыта":
                    open_reqs = [req]
            
            # 2. If nothing found by ID, check for "last/latest" keywords
            if not open_reqs and any(w in search_str.lower() for w in ["послед", "last", "latest", "свеж"]):
                all_reqs = await db.list_requests(limit=1, status="Открыта")
                open_reqs = all_reqs
            
            # 3. Fallback to route-based search
            if not open_reqs:
                search_term = search_str.split()[0] if search_str else ""
                reqs = await db.list_requests(limit=5, search=search_term)
                open_reqs = [r for r in reqs if r["status"] == "Открыта"]
            
            if not open_reqs:
                await update.message.reply_text(f"❌ Я не нашел открытых заявок по запросу '{search_str}'. Попробуйте указать ID заявки (например: /ai ставка #23 1000 USD).")
                return
            
            # If exactly one result — offer it directly
            keyboard = []
            for r in open_reqs:
                cb_data = f"aibid_{r['id']}_{amount}_{currency}"
                keyboard.append([InlineKeyboardButton(f"#{r['id']:04d} | {r['route_from']} ➔ {r['route_to']}", callback_data=cb_data)])
            
            await update.message.reply_text(
                f"💰 Вы хотите сделать ставку **{amount} {currency}**.\n\nК какой заявке ее прикрепить?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return

        elif intent == "query_database":
            await update.message.reply_text("📊 Ищу информацию в базе...")
            answer = await ai_assistant.answer_db_query(text, db)
            await update.message.reply_text(answer, parse_mode="Markdown")
            return
            
        elif intent == "chat":
            await update.message.reply_text(intent_res.get("text", "Не понял вас."))
            return

        # Intent is create_request -> continue to draft building
        old_draft = context.user_data.get("ai_parsed", {})
        
        # Search for similar templates in DB
        templates = await db.list_requests(limit=3, search=text[:30])
        template_data = []
        for t in templates:
            template_data.append({
                "id": t["id"], "route_from": t["route_from"], "route_to": t["route_to"],
                "cargo_name": t["cargo_name"]
            })

        parsed = await ai_assistant.parse_request(text, current_draft=old_draft, templates=template_data)
        if "error" in parsed:
            await update.message.reply_text(f"❌ <b>Ошибка ИИ:</b> {parsed['error']}", parse_mode="HTML")
            return

        if parsed.get("not_logistics"):
            if info_prefix: 
                await update.message.reply_text(f"{info_prefix}\n🤖 Это не похоже на логистический запрос.")
            return

        # Handle drafting
        old_draft = context.user_data.get("ai_parsed", {})
        merged = ai_assistant.merge_parsed_data(old_draft, parsed)
        context.user_data["ai_parsed"] = merged

        # If AI found a match, load full data from that request if not already merged
        if parsed.get("template_match"):
            try:
                raw = str(parsed["template_match"]).replace("#", "").strip()
                if raw.isdigit():
                    full_tmpl = await db.get_request(int(raw))
                    if full_tmpl:
                        for k in ["cargo_weight", "cargo_places", "packaging", "hs_code", "cargo_value", "delivery_terms", "route_type"]:
                            if not merged.get(k) or merged.get(k) == "-":
                                merged[k] = full_tmpl.get(k)
                        context.user_data["ai_parsed"] = merged
            except (ValueError, TypeError) as e:
                logger.debug(f"template_match parse failed: {e}")

        preview = ai_assistant.build_preview(merged)
        keyboard = [
            [InlineKeyboardButton("✅ Подтвердить и отправить", callback_data="confirm_ai")],
            [InlineKeyboardButton("📝 Добавить детали", callback_data="more_ai")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_ai")]
        ]
        
        if merged.get("ready_to_publish"):
            status_text = "✨ Заявка готова к публикации!"
        else:
            status_text = "📋 **Черновик заявки:** (нужны детали)"

        # Cleanup old bot message if exists (best-effort — message may already be gone)
        old_msg_id = context.user_data.get("last_ai_msg_id")
        if old_msg_id:
            try:
                await context.bot.delete_message(update.effective_chat.id, old_msg_id)
            except Exception:
                pass
            
        sent_msg = await update.message.reply_text(
            f"{info_prefix}\n{status_text}\n\n{preview}\n\nВсе верно?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        context.user_data["last_ai_msg_id"] = sent_msg.message_id
        
    except Exception as e:
        logger.error(f"process_ai_message error: {e}", exc_info=True)

@requires_auth
async def handle_text_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    low_text = text.lower().strip()
    if low_text in ["отмена", "/cancel"]:
        context.user_data.pop("ai_parsed", None)
        context.user_data.pop("last_ai_msg_id", None)
        await update.message.reply_text("❌ Создание заявки отменено.")
        return

    # Trigger AI only via /ai or if already in dialogue
    is_ai_cmd = text.startswith("/ai")
    in_dialogue = "ai_parsed" in context.user_data
    
    if is_ai_cmd or in_dialogue:
        # If it's a command, remove the prefix
        content = text
        if is_ai_cmd:
            content = text[3:].strip()
            if not content and not in_dialogue:
                await update.message.reply_text("🤖 Я слушаю! Напишите подробности заявки после команды /ai или отправьте голосовое.")
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
    
    parsed = context.user_data.get("ai_parsed")
    if not parsed: return
    
    try:
        profile = context.user_data.get("profile", {})
        user_id = update.effective_user.id
        
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
        for key in ["ai_parsed", "last_ai_msg_id"]:
            context.user_data.pop(key, None)
            
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
