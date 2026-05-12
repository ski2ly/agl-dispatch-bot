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
        try:
            settings = await db.get_settings()
            poll_interval_hours = settings.get("feedback_poll_hours", 24)
            
            async with db._pool.acquire() as conn:
                from datetime import timedelta
                cutoff = datetime.now(TZ) - timedelta(hours=poll_interval_hours)

                # Task 5: Feedback poll logic for managers
                reqs_to_poll = await conn.fetch(f"""
                    SELECT id, creator_id, route_from, route_to
                    FROM requests
                    WHERE status = 'Открыта'
                      AND created_at <= $1
                      AND mute_reminders = FALSE
                      AND id NOT IN (SELECT request_id FROM activity_log WHERE action = 'feedback_polled')
                """, cutoff)

                for r in reqs_to_poll:
                    req_id = r["id"]
                    creator_id = r["creator_id"]

                    if creator_id:
                        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                        keyboard = [
                            [InlineKeyboardButton("✅ Подтвердили", callback_data=f"fb_{req_id}_confirmed")],
                            [InlineKeyboardButton("❌ Клиент не заинтересован", callback_data=f"fb_{req_id}_not_interested")],
                            [InlineKeyboardButton("⏳ Ждём до даты", callback_data=f"fb_{req_id}_wait_date")],
                            [InlineKeyboardButton("🔄 Нужна другая ставка", callback_data=f"fb_{req_id}_need_rate")],
                            [InlineKeyboardButton("📝 Другое", callback_data=f"fb_{req_id}_other")]
                        ]
                        text = f"Что произошло с заявкой #{req_id:04d} ({r['route_from']} ➔ {r['route_to']})?"

                        try:
                            await bot.send_message(
                                chat_id=creator_id,
                                text=text,
                                reply_markup=InlineKeyboardMarkup(keyboard)
                            )
                            await db.log_activity(req_id, creator_id, "System", "feedback_polled", {"hours": poll_interval_hours})
                        except Exception as e:
                            logger.error(f"Failed to send feedback poll to {creator_id} for req {req_id}: {e}")

            # Existing stale request logic
            stale = await db.get_stale_requests(no_bids_days=3, open_days=7)
            for req in stale.get("no_bids", []):
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
                            [InlineKeyboardButton("🔔 Напомнить логистам", callback_data=f"ping_logistics_conf_{req['id']}")],
                            [InlineKeyboardButton("💤 Напомнить позже", callback_data=f"remind_later_{req['id']}")],
                            [InlineKeyboardButton("🔇 Отключить", callback_data=f"remind_mute_{req['id']}")]
                        ]
                        await bot.send_message(
                            chat_id=cid,
                            text=f"⚠️ <b>Напоминание:</b> По вашей заявке #{req['id']:04d} ({req['route_from']} ➔ {req['route_to']}) всё ещё нет ставок. Проверьте актуальность.",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        await db.update_request(req["id"], {"last_notified_at": datetime.now(TZ)})
                    except Exception as e:
                        logger.warning(f"Failed to notify user {cid}: {e}")
            
        except Exception as outer_e:
            logger.error(f"Cron loop error: {outer_e}")

        await asyncio.sleep(60 * 60) # Run every hour instead of 6 hours
