import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers.ai_handlers import confirm_ai_logic
from handlers.auth import requires_auth
from handlers.commands import view_request_handler
from utils.helpers import build_bid_card, sync_bid_to_discussion

logger = logging.getLogger(__name__)

from database import db

def _safe_int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


@requires_auth
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data

    if data == "confirm_ai":
        await confirm_ai_logic(update, context)
        return
    if data == "cancel_ai":
        await db.clear_ai_context(update.effective_user.id)
        context.user_data.pop("ai_parsed", None)
        await query.answer("Отменено")
        await query.edit_message_text("❌ Создание заявки отменено. Черновик очищен.")
        return
    if data == "more_ai":
        await query.answer("Продолжайте писать или говорить...")
        return
    if data.startswith("view_"):
        await view_request_handler(update, context)
        return

    if data.startswith("fbk_"):
        parts = data.split("_", 2)
        action = parts[1]
        req_id = int(parts[2])
        
        if action in ["wait", "other"]:
            context.user_data['awaiting_feedback'] = {
                'req_id': req_id,
                'action': action,
            }
            prompt = "Укажите до какой даты ждём:" if action == 'wait' else "Напишите подробности:"
            await query.edit_message_text(f"📝 Опрос по заявке #{req_id:05d}\n\n{prompt}\n(Напишите ответ следующим сообщением)")
            return
            
        labels = {
            "confirm": "Подтвердили",
            "not_interested": "Клиент не заинтересован",
            "new_bid": "Нужна другая ставка"
        }
        text_val = labels.get(action, "Другое")
        await db.add_comment(req_id, update.effective_user.id, update.effective_user.first_name, f"Фидбэк: {text_val}", type="feedback")
        await query.edit_message_text(f"✅ Фидбэк сохранён: {text_val}")
        return

    if data.startswith("bids_history_"):
        req_id = int(data.split("_")[2])
        bids = await db.get_bids(req_id)
        if not bids:
            await query.answer("По этой заявке ставок пока нет", show_alert=True)
            return

        text = f"📊 *История ставок по заявке #{req_id:05d}*\n\n"
        for b in bids:
            dt = b['created_at'].strftime("%d.%m %H:%M") if b.get('created_at') else "—"
            manager = b.get('manager_name') or 'Неизвестно'
            amount = b.get('amount', '?')
            curr = b.get('currency', '')
            pay_terms = b.get('payment_terms') or 'Не указано'
            text += f"👤 {manager}  💰 {amount} {curr}  📅 {dt}\n📋 {pay_terms}\n\n"
        
        await query.message.reply_text(text, parse_mode="Markdown")
        await query.answer()
        return

    if data.startswith("comments_"):
        req_id = _safe_int(data.split("_", 1)[1])
        if not req_id:
            await query.answer("Некорректный ID", show_alert=True)
            return
        await query.answer()
        comments = await db.get_comments(req_id)
        if not comments:
            await query.edit_message_text(
                f"💬 Комментарии по заявке #{req_id:05d}\n\nПока комментариев нет.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("← Назад к заявке", callback_data=f"view_{req_id}")]
                ])
            )
            return
        lines = [f"💬 Комментарии по заявке #{req_id:05d}\n"]
        for c in comments[-15:]:  # Last 15 comments
            badge = "💬" if c.get("type") == "discussion" else ("🤖" if c.get("type") == "ai" else "👤")
            name = c.get("user_name") or "Система"
            text_preview = (c.get("text") or "")[:200]
            lines.append(f"{badge} {name}: {text_preview}")
        lines_text = "\n".join(lines)
        if len(lines_text) > 4000:
            lines_text = lines_text[:4000] + "..."
        await query.edit_message_text(
            lines_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("← Назад к заявке", callback_data=f"view_{req_id}")]
            ])
        )
        return

    if data.startswith("recall_"):
        req_id = _safe_int(data.split("_")[1])
        if req_id:
            req = await db.get_request(req_id)
            if req:
                # Map DB fields back to AI draft format
                new_draft = {
                    "regions": req.get("regions"),
                    "transport_cat": req.get("transport_cat"),
                    "route_from": req.get("route_from"),
                    "route_to": req.get("route_to"),
                    "cargo_name": req.get("cargo_name"),
                    "hs_code": req.get("hs_code"),
                    "cargo_value": req.get("cargo_value"),
                    "cargo_weight": req.get("cargo_weight"),
                    "cargo_places": req.get("cargo_places"),
                    "recall_source_id": req_id
                }
                await db.save_ai_context(update.effective_user.id, new_draft)
                from ai_assistant import ai_assistant
                preview = ai_assistant.build_preview(new_draft)
                await query.answer("Заявка подгружена!")
                await query.edit_message_text(
                    f"🔄 Данные заявки #{req_id:05d} подгружены в черновик.\n\n{preview}\n\nЧто нужно изменить или добавить?",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Опубликовать как новую", callback_data="confirm_ai")],
                        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_ai")]
                    ])
                )
        return

    if data.startswith("aibid_"):
        parts = data.split("_")
        if len(parts) < 4:
            await query.answer("Некорректные данные ставки", show_alert=True)
            return
        req_id = _safe_int(parts[1])
        amount = parts[2]
        currency = parts[3]
        if req_id is None or not amount or not currency:
            await query.answer("Некорректные данные ставки", show_alert=True)
            return
        profile = context.user_data.get("profile", {})
        manager_name = profile.get("name", "ИИ Логист")
        bid_data = {
            "amount": amount,
            "currency": currency,
            "payment_method": "-",
            "validity": "",
            "loading_hours": "",
            "demurrage": "",
            "comment": "Создано через ИИ",
        }
        await db.upsert_bid(req_id, update.effective_user.id, manager_name, bid_data)
        await db.log_activity(req_id, update.effective_user.id, manager_name,
                              "bid_submitted", {"amount": amount})

        # Add internal comment
        bid_card_data = {**bid_data, "request_id": req_id, "manager_name": manager_name}
        bid_card_text = build_bid_card(bid_card_data)
        await db.add_comment(req_id, update.effective_user.id, manager_name, bid_card_text, "bid")

        # Send bid to discussion group (same logic as MiniApp bids)
        import os
        settings = await db.get_settings()
        discussion_id = settings.get("discussion_id") or os.getenv("DISCUSSION_GROUP_ID")
        channel_id = settings.get("channel_id") or os.getenv("CHANNEL_ID")
        req = await db.get_request(req_id)
        
        if discussion_id and req and channel_id:
            msg_id = req.get("channel_msg_id")
            if msg_id:
                plain_card = bid_card_text.replace("**", "")
                await sync_bid_to_discussion(context.bot, discussion_id, channel_id, msg_id, plain_card)
            else:
                plain_card = bid_card_text.replace("**", "")
                await context.bot.send_message(chat_id=discussion_id, text=plain_card)

        # Notify creator
        creator_id = req.get("creator_id") if req else None
        if creator_id and int(creator_id) != update.effective_user.id:
            try:
                notify_text = (
                    f"💰 <b>Новая ставка по вашей заявке #{req_id:05d}</b>\n"
                    f"📦 Груз: {req.get('cargo_name', '-')}\n"
                    f"📍 Маршрут: {req.get('route_from', '-')} → {req.get('route_to', '-')}\n\n"
                    f"💵 Сумма: <b>{amount} {currency}</b>\n"
                    f"👤 От: {manager_name}\n\n"
                    f"Посмотреть подробности можно в Mini App."
                )
                await context.bot.send_message(chat_id=int(creator_id), text=notify_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to notify creator {creator_id}: {e}")

        await query.answer("Ставка принята!")
        await query.edit_message_text(
            f"✅ Ставка **{amount} {currency}** успешно добавлена к заявке #{req_id:05d}!",
            parse_mode="Markdown",
        )
        return

    if data.startswith("remind_later_") or data.startswith("remind_mute_") or data.startswith("ping_logistics_"):
        # All three encode the request_id as the trailing _<int>; parse defensively.
        parts = data.rsplit("_", 1)
        req_id = _safe_int(parts[-1]) if len(parts) == 2 else None
        if req_id is None:
            await query.answer("Некорректный ID заявки", show_alert=True)
            return

        if data.startswith("remind_later_"):
            await query.answer("Напомним завтра")
            await query.edit_message_text(f"⏳ Напоминание по заявке #{req_id:05d} отложено на 24 часа.")
            return

        if data.startswith("remind_mute_"):
            await db.update_request(req_id, {"mute_reminders": True})
            await query.answer("Уведомления отключены")
            await query.edit_message_text(f"🔇 Уведомления по заявке #{req_id:05d} отключены.")
            return

        # ping_logistics_
        req = await db.get_request(req_id)
        if not (req and req.get("channel_msg_id")):
            await query.answer("Заявка не найдена в канале")
            return
        settings = await db.get_settings()
        channel_id = settings.get("channel_id")
        if not channel_id:
            await query.answer("Канал не настроен")
            return
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                reply_to_message_id=int(req["channel_msg_id"]),
                text=f"‼️ Уважаемые коллеги, заявка #{req['id']:05d} ({req['route_from']} ➔ {req['route_to']}) всё ещё актуальна! Ждём ваших ставок.",
            )
            await query.answer("Напоминание отправлено!")
            await query.edit_message_text(f"🔔 Вы напомнили логистам о заявке #{req_id:05d}.")
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            await query.answer("Ошибка при отправке")
        return
    
    if data.startswith("urg_remind_channel_"):
        parts = data.split("_")
        req_id = int(parts[3])
        req = await db.get_request(req_id)
        if not (req and req.get("channel_msg_id")):
            await query.answer("Заявка не найдена в канале", show_alert=True)
            return
        settings = await db.get_settings()
        channel_id = settings.get("channel_id")
        if not channel_id:
            await query.answer("Канал не настроен", show_alert=True)
            return
        try:
            text = (
                f"‼️ Уважаемые коллеги, заявка #{req['id']:05d} ({req['route_from']} ➔ {req['route_to']}) "
                f"всё ещё актуальна! 🔥 <b>ГРУЗ СРОЧНЫЙ!</b> 🔥\nЖдём ваших ставок."
            )
            await context.bot.send_message(
                chat_id=channel_id,
                reply_to_message_id=int(req["channel_msg_id"]),
                text=text,
                parse_mode="HTML"
            )
            await query.answer("Напоминание отправлено!")
            await query.edit_message_text(f"🔔 Вы напомнили коллегам в канале о срочной заявке #{req_id:05d}.")
        except Exception as e:
            logger.error(f"Urgent ping failed: {e}")
            await query.answer("Ошибка при отправке")
        return

    if data.startswith("urg_mute_"):
        req_id = int(data.split("_")[2])
        await db.update_request(req_id, {"urgent_reminder_sent": True})
        await query.answer("Напоминания отключены")
        await query.edit_message_text(f"🔇 Напоминания по срочной заявке #{req_id:05d} отключены.")
        return

    logger.warning(f"Unknown callback: {data}")
    await query.answer()
