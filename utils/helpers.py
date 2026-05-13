import pytz
from datetime import datetime

TZ = pytz.timezone("Asia/Tashkent")

def get_now_str():
    return datetime.now(TZ).isoformat()

def format_datetime(iso_str):
    try:
        if not iso_str: return "неизвестно"
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = TZ.localize(dt)
        else:
            dt = dt.astimezone(TZ)
        return dt.strftime("%d.%m.%Y в %H:%M (UZT)")
    except Exception:
        return "неизвестно"

def parse_val(value):
    if not value or str(value).strip() == "-" or str(value).strip() == "" or str(value).lower() == "не указано":
        return None
    return str(value).strip()

def build_card(req: dict) -> str:
    """Build a professional, structured card for the Telegram channel."""
    def v(key, default=None):
        """Return value only if meaningful, else None."""
        val = req.get(key)
        if val is not None and str(val).strip() not in ("-", "", "None", "не указано", "False", "false"):
            return str(val).strip()
        return default

    req_id = req.get("id", 0)
    urgency = v('urgency_type', '')
    title = f"[НОВАЯ ЗАЯВКА #{req_id:05d}]"
    if urgency == "Срочно" or urgency == "🔥 СРОЧНО":
        title += " - СРОЧНАЯ 🔥"

    t_cat = str(req.get("transport_cat", ""))
    t_sub = v("transport_sub")
    reg = req.get("regions", "Другое")

    lines = [
        title,
        "",
        f"Направление: {reg}",
        f"Тип перевозки: {t_cat}",
    ]
    if t_sub:
        lines.append(f"Вид: {t_sub}")
    
    lines.append(f"Источник: {v('source', 'Не указан')}")
    lines.append("")
    lines.append(f"{v('route_from', '?')} ➔ {v('route_to', '?')}")

    # Optional address lines — only show if filled
    for key, label in [("loading_address", "Погрузка"), ("customs_address", "Затаможка"), ("clearance_address", "Растаможка"), ("unloading_address", "Выгрузка")]:
        val = v(key)
        if val: lines.append(f"{label}: {val}")

    lines.append("")
    lines.append(f"Груз: {v('cargo_name', '?')}")
    if v('hs_code'): lines.append(f"ТН ВЭД: {v('hs_code')}")
    if v('dangerous_cargo') and v('dangerous_cargo') == 'Да':
        adr = v('adr_class')
        lines.append(f"Опасный: Да (ADR {adr if adr else 'не указан'})")
    elif v('dangerous_cargo') and v('dangerous_cargo') != 'Нет':
        lines.append(f"Опасный: {v('dangerous_cargo')}")
    lines.append("")
    if v('cargo_weight'): lines.append(f"Вес: {v('cargo_weight')} кг")
    if v('cargo_places'): lines.append(f"Мест: {v('cargo_places')}")
    if v('cargo_volume'): lines.append(f"Объем: {v('cargo_volume')} м³")
    if v('packaging'): lines.append(f"Упаковка: {v('packaging')}")
    if v('stackable') == 'Да': lines.append("Штабелируемый: Да")
    elif v('stackable') == 'Нет': lines.append("Штабелируемый: Нет")
    
    if v('cargo_oversized') == 'Да':
        lines.append(f"Негабаритный: Да ({v('cargo_dimensions', 'размеры не указаны')})")
    
    if v('temp_control') == 'Да':
        lines.append(f"Температурный режим: {v('temp_range', 'не указан')}")
    
    lines.append("")
    val = v('cargo_value')
    if val:
        curr = v('cargo_currency') or 'USD'
        lines.append(f"Стоимость: {val} {curr}")
    else:
        lines.append(f"Стоимость: НЕ УКАЗАНА")
    lines.append(f"Срочность: {v('urgency_type') or v('urgency_days') or 'Стандарт'}")

    # Specific fields
    spec_map = {
        "delivery_terms_eu": "Условия", "route_type": "Маршрут", "export_decl": "Экспортная", 
        "origin_cert": "Сертификат", "road_type_cn": "Тип фуры", "border_crossing_cn": "Погранпереход",
        "container_owner": "Контейнер", "glonass_seal": "Пломба", "days_loading": "Дней на погр. (ПРР+Там)",
        "days_unloading": "Дней на выгрузке (ПРР+Там)", "flight_type": "Рейс", "ports_list": "Порт"
    }
    spec_fields = []
    for k, label in spec_map.items():
        val = v(k)
        if val:
            display_label = label
            if "," in val:
                if label == "Погранпереход": display_label = "Погранпереходы"
                elif label == "Порт": display_label = "Порты"
                elif label == "Маршрут": display_label = "Маршруты"
            spec_fields.append(f"• {display_label}: {val}")
    
    if spec_fields:
        lines.append("")
        lines.append("Специфика:")
        lines.extend(spec_fields)
    
    if v('message_text'):
        lines.extend(["", "Дополнительно:", v('message_text')])
    
    lines.extend(["", f"Менеджер: {v('responsible') or '—'}", "#заявка"])
    return "\n".join(lines)

def build_bid_card(bid: dict) -> str:
    """Build a unified, professional card for a bid/rate.

    Uses plain text (no Markdown/HTML markers) so it can be sent with any
    parse_mode or none at all. The caller can wrap in <b> if needed.
    """
    lines = [
        f"НОВАЯ СТАВКА",
        f"По заявке: #{int(bid.get('request_id', 0)):05d}",
        "",
        f"СУММА: {bid.get('amount')} {bid.get('currency')}",
        f"Менеджер: {bid.get('manager_name') or '-'}",
        f"Валидность: {bid.get('validity') or '-'}",
        "",
        "УСЛОВИЯ:",
        f"П/В: {bid.get('loading_hours') or '24ч'}",
        f"Простой: {bid.get('demurrage') or '-'}",
        f"Оплата: {bid.get('payment') or bid.get('payment_method') or bid.get('payment_terms') or '-'}",
        "",
        "КОММЕНТАРИЙ:",
        f"{bid.get('comment') or '-'}",
        "",
        "#ставка"
    ]
    return "\n".join(lines)

async def sync_bid_to_discussion(bot, discussion_id, channel_id, channel_msg_id, bid_card_text):
    """
    Sends a bid card to the discussion group as a proper comment.
    Uses get_discussion_message to find the correct thread ID in the group.
    """
    import logging
    log = logging.getLogger(__name__)
    
    if not channel_id or not channel_msg_id:
        log.warning(f"Sync failed: channel_id={channel_id}, channel_msg_id={channel_msg_id}")
        return False
        
    try:
        # Normalize channel_id
        target_chat = channel_id
        if isinstance(target_chat, str) and (target_chat.startswith("-") or target_chat.isdigit()):
            try:
                target_chat = int(target_chat)
            except:
                pass
                
        # If discussion_id is missing, try to detect it from the channel
        target_discussion = discussion_id
        if not target_discussion:
            try:
                log.info(f"Detecting linked chat for {target_chat}")
                chat_info = await bot.get_chat(chat_id=target_chat)
                target_discussion = chat_info.linked_chat_id
                log.info(f"Detected linked_chat_id: {target_discussion}")
            except Exception as e:
                log.error(f"Failed to detect linked chat: {e}")

        if not target_discussion:
            log.warning("No discussion_id and linked_chat_id detection failed.")
            return False

        # Normalize target_discussion
        if isinstance(target_discussion, str) and (target_discussion.startswith("-") or target_discussion.isdigit()):
            try:
                target_discussion = int(target_discussion)
            except:
                pass

        log.info(f"Attempting getDiscussionMessage via direct API call: chat={target_chat}, msg={channel_msg_id}")
        
        # 1. Get Chat info to check if it's a forum and see linked_chat
        try:
            chat_info = await bot.get_chat(chat_id=target_chat)
            log.info(f"Channel info: linked_chat={chat_info.linked_chat_id}")
        except Exception as e:
            log.warning(f"Could not get channel info: {e}")

        # 2. Try direct API call
        import aiohttp
        api_url = f"https://api.telegram.org/bot{bot.token}/getDiscussionMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json={"chat_id": str(target_chat), "message_id": int(channel_msg_id)}) as resp:
                res = await resp.json()
                if res.get("ok"):
                    disc_data = res["result"]
                    target_disc_id = disc_data["chat"]["id"]
                    target_msg_id = disc_data["message_id"]
                    
                    log.info(f"Discussion msg found via API: {target_msg_id} in chat {target_disc_id}. Replying.")
                    await bot.send_message(
                        chat_id=target_disc_id,
                        text=bid_card_text,
                        reply_to_message_id=target_msg_id,
                        message_thread_id=target_msg_id, # Ensure it's treated as a comment/thread
                        parse_mode="HTML"
                    )
                    return True
                else:
                    log.error(f"API getDiscussionMessage failed: {res}")
                    
                    # 3. DIRECT THREAD FALLBACK: Use channel_msg_id as thread_id
                    if target_discussion:
                        log.info(f"Using direct thread ID fallback: chat={target_discussion}, thread={channel_msg_id}")
                        try:
                            await bot.send_message(
                                chat_id=target_discussion,
                                text=bid_card_text,
                                message_thread_id=int(channel_msg_id),
                                reply_to_message_id=int(channel_msg_id),
                                parse_mode="HTML"
                            )
                            return True
                        except Exception as e_final:
                            log.error(f"Thread fallback failed: {e_final}")
                            # Last ditch: send as regular message to group
                            await bot.send_message(chat_id=target_discussion, text=bid_card_text, parse_mode="HTML")
                            return True
                    
                    raise Exception(f"API error: {res.get('description')}")
        
        return True
    except Exception as e:
        log.error(f"Sync logic failed: {e}")
        # Fallback to top-level message if anything fails
        if target_discussion:
            try:
                # Ensure target_discussion is cleaned for fallback
                if isinstance(target_discussion, str):
                    target_discussion = target_discussion.strip()
                    if target_discussion.startswith("-") or target_discussion.isdigit():
                        target_discussion = int(target_discussion)
                
                log.info(f"Fallback: sending top-level message to {target_discussion}")
                await bot.send_message(chat_id=target_discussion, text=bid_card_text, parse_mode="HTML")
                return True
            except Exception as e2:
                log.error(f"Fallback failed: {e2}")
        return False

def calculate_deletion_time(now_dt: datetime, duration_hours: int = 2) -> datetime:
    """Calculates deletion time respecting business hours (09:30 - 19:00).
    
    If event happens at night (19:00 - 09:30), the timer starts at 09:30.
    If event happens late in the day, the remaining time carries over to next morning.
    """
    from datetime import timedelta
    
    # Ensure UZT
    if now_dt.tzinfo is None:
        now_dt = TZ.localize(now_dt)
    else:
        now_dt = now_dt.astimezone(TZ)
    
    def get_bounds(dt):
        start = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        end = dt.replace(hour=19, minute=0, second=0, microsecond=0)
        return start, end

    current = now_dt
    remaining_minutes = duration_hours * 60
    
    while remaining_minutes > 0:
        work_start, work_end = get_bounds(current)
        
        if current < work_start:
            # Shift to start of work day
            current = work_start
            continue
            
        if current >= work_end:
            # Shift to start of next work day
            current = work_start + timedelta(days=1)
            continue
            
        # We are inside work hours
        minutes_till_end = (work_end - current).total_seconds() / 60
        
        if minutes_till_end >= remaining_minutes:
            # We can finish today
            current += timedelta(minutes=remaining_minutes)
            remaining_minutes = 0
        else:
            # Use up today's time and move to next day
            remaining_minutes -= minutes_till_end
            current = work_start + timedelta(days=1)
            
    return current
