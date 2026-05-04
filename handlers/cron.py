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
                        text=f"⚠️ <b>Напоминание:</b> По вашей заявке #{req['id']:04d} ({req['route_from']} ➔ {req['route_to']}) всё ещё нет ставок. Проверьте актуальность.",
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
