import asyncio
import logging
from datetime import datetime
from database import db
from utils.helpers import TZ

logger = logging.getLogger(__name__)

async def reminder_cron(bot):
    """Periodic task to notify managers about stale requests.
    
    Runs as a single infinite loop; supervised by _supervised_cron in main.py
    which auto-restarts on unhandled errors.
    """
    logger.info("⏰ Reminder cron task started")
    await asyncio.sleep(60) # Wait for startup
    
    while True:
        stale = await db.get_stale_requests(no_bids_days=3, open_days=7)
        
        # 1. No bids for 3 days
        for req in stale.get("no_bids", []):
            # Throttle notifications: only once per 24 hours
            last_notified = req.get("last_notified_at")
            if last_notified:
                if (datetime.now(TZ) - last_notified).days < 1:
                    continue
            
            if req.get("mute_reminders"):
                continue
            
            cid = req.get("creator_id")
            if cid:
                try:
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    keyboard = [
                        [InlineKeyboardButton("🔔 Напомнить логистам", callback_data=f"ping_logistics_{req['id']}")],
                        [InlineKeyboardButton("💤 Напомнить позже", callback_data=f"remind_later_{req['id']}")],
                        [InlineKeyboardButton("🔇 Отключить", callback_data=f"remind_mute_{req['id']}")]
                    ]
                    await bot.send_message(
                        chat_id=cid,
                        text=f"⚠️ <b>Напоминание:</b> По вашей заявке #{req['id']:05d} ({req['route_from']} ➔ {req['route_to']}) всё ещё нет ставок. Проверьте актуальность.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    await db.update_request(req["id"], {"last_notified_at": datetime.now(TZ)})
                except Exception as e:
                    logger.warning(f"Failed to notify user {cid}: {e}")
        
        # 2. Open for 7 days (future: notify admins)
        for req in stale.get("old_open", []):
            pass

        await asyncio.sleep(6 * 3600) # Run every 6 hours

async def feedback_cron(bot):
    """Periodic task to ask managers for feedback after 24 hours."""
    logger.info("⏰ Feedback cron task started")
    await asyncio.sleep(120)  # offset from reminder
    
    while True:
        try:
            reqs = await db.get_requests_for_feedback(hours_old=24)
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            for req in reqs:
                cid = req.get("creator_id")
                req_id = req["id"]
                if cid:
                    keyboard = [
                        [InlineKeyboardButton("✅ Подтвердили", callback_data=f"fbk_confirm_{req_id}")],
                        [InlineKeyboardButton("❌ Клиент не заинтересован", callback_data=f"fbk_not_interested_{req_id}")],
                        [InlineKeyboardButton("⏳ Ждём до даты", callback_data=f"fbk_wait_{req_id}")],
                        [InlineKeyboardButton("🔄 Нужна другая ставка", callback_data=f"fbk_new_bid_{req_id}")],
                        [InlineKeyboardButton("💬 Другое", callback_data=f"fbk_other_{req_id}")]
                    ]
                    await bot.send_message(
                        chat_id=cid,
                        text=f"📊 <b>Опрос по заявке #{req_id:05d}</b>\n({req.get('route_from')} ➔ {req.get('route_to')})\n\nПрошло 24 часа. Что произошло с заявкой?",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                # Mark as requested so we don't ask again
                await db.update_request(req_id, {"feedback_requested": True})
        except Exception as e:
            logger.error(f"Feedback cron error: {e}")
        
        await asyncio.sleep(3600)  # check once an hour

async def deletion_cron(bot):
    """Periodic task to delete expired temporary messages."""
    logger.info("⏰ Deletion cron task started")
    await asyncio.sleep(30) # offset
    
    while True:
        try:
            expired = await db.get_expired_deletions()
            for task in expired:
                try:
                    await bot.delete_message(chat_id=task["chat_id"], message_id=task["message_id"])
                    logger.info(f"🗑 Deleted expired message {task['message_id']} in chat {task['chat_id']}")
                except Exception as e:
                    # Message might be already deleted or bot has no permission
                    logger.warning(f"Could not delete expired message {task['message_id']}: {e}")
                finally:
                    await db.remove_scheduled_deletion(task["id"])
        except Exception as e:
            logger.error(f"Deletion cron error: {e}")
        
        await asyncio.sleep(600) # check every 10 minutes
