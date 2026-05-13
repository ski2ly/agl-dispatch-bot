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
        lines.append(f"Класс ADR: {adr if adr else 'не указан'}")
    elif v('dangerous_cargo') and v('dangerous_cargo') != 'Нет':
        lines.append(f"Опасный: {v('dangerous_cargo')}")
    lines.append("")
    if v('cargo_weight'):
        w = v('cargo_weight')
        if 'кг' not in w.lower(): w = f"{w} кг"
        lines.append(f"Вес: {w}")
    if v('cargo_places'): lines.append(f"Мест: {v('cargo_places')}")
    if v('cargo_volume'):
        vol = v('cargo_volume')
        if 'м' not in vol.lower() and 'm' not in vol.lower(): vol = f"{vol} м³"
        lines.append(f"Объем: {vol}")
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
    """Build a unified, professional card for a bid/rate."""
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
    """Sends a bid card to the discussion group as a proper comment."""
    import logging
    log = logging.getLogger(__name__)
    from telegram import ReplyParameters
    from telegram.error import BadRequest
    
    def to_int(val):
        if val is None: return None
        try:
            s = str(val).strip()
            return int(s) if s else None
        except: return None

    target_chat = to_int(channel_id)
    target_discussion = to_int(discussion_id)

    # Strategy A: Try direct reply
    if target_discussion and channel_msg_id:
        try:
            await bot.send_message(
                chat_id=target_discussion,
                text=bid_card_text,
                reply_parameters=ReplyParameters(message_id=int(channel_msg_id)),
                parse_mode="HTML"
            )
            return True
        except Exception: pass

    # Strategy B: Resolve via getDiscussionMessage
    target_disc_id, target_msg_id = target_discussion, None
    if target_chat and channel_msg_id:
        try:
            if hasattr(bot, 'get_discussion_message'):
                m = await bot.get_discussion_message(chat_id=target_chat, message_id=int(channel_msg_id))
                target_disc_id, target_msg_id = m.chat_id, m.message_id
            else:
                import aiohttp
                url = f"https://api.telegram.org/bot{bot.token}/getDiscussionMessage"
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json={"chat_id": str(target_chat), "message_id": int(channel_msg_id)}) as r:
                        res = await r.json()
                        if res.get("ok"):
                            target_disc_id = res["result"]["chat"]["id"]
                            target_msg_id = res["result"]["message_id"]
        except Exception as e: log.warning(f"Resolution failed: {e}")

    # Send resolved reply
    if target_msg_id and target_disc_id:
        try:
            await bot.send_message(
                chat_id=target_disc_id,
                text=bid_card_text,
                reply_parameters=ReplyParameters(message_id=target_msg_id, allow_sending_without_reply=False),
                parse_mode="HTML"
            )
            return True
        except BadRequest as e:
            if "thread not found" in str(e).lower() or "topic" in str(e).lower():
                try:
                    await bot.send_message(
                        chat_id=target_disc_id,
                        text=bid_card_text,
                        reply_parameters=ReplyParameters(message_id=target_msg_id),
                        message_thread_id=target_msg_id,
                        parse_mode="HTML"
                    )
                    return True
                except: pass

    # Fallback
    if target_discussion:
        try:
            await bot.send_message(chat_id=target_discussion, text=bid_card_text, parse_mode="HTML")
            return True
        except: pass
    return False

def calculate_deletion_time(now_dt: datetime, duration_hours: int = 2) -> datetime:
    """Calculates deletion time respecting business hours (09:30 - 19:00)."""
    from datetime import timedelta
    if now_dt.tzinfo is None: now_dt = TZ.localize(now_dt)
    else: now_dt = now_dt.astimezone(TZ)
    
    def get_bounds(dt):
        return dt.replace(hour=9, minute=30, second=0, microsecond=0), dt.replace(hour=19, minute=0, second=0, microsecond=0)

    current, rem = now_dt, duration_hours * 60
    while rem > 0:
        s, e = get_bounds(current)
        if current < s: current = s; continue
        if current >= e: current = s + timedelta(days=1); continue
        avail = (e - current).total_seconds() / 60
        if avail >= rem: current += timedelta(minutes=rem); rem = 0
        else: rem -= avail; current = s + timedelta(days=1)
    return current
