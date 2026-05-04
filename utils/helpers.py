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
        if val and str(val).strip() not in ("-", "", "None", "не указано", "False", "false"):
            return str(val).strip()
        return default

    req_id = req.get("id", 0)
    t_cat = str(req.get("transport_cat", ""))
    t_emoji = "🚛"
    if "Авиа" in t_cat: t_emoji = "✈️"
    elif "Ж/Д" in t_cat or "Вагон" in t_cat: t_emoji = "🚆"
    elif "Мульти" in t_cat or "Мор" in t_cat: t_emoji = "🚢"
    
    reg = req.get("regions", "Другое")
    # Dynamic region emoji — try cached settings, fallback to hardcoded defaults
    _DEFAULT_EMOJI = {"Европа": "🇪🇺", "Китай": "🇨🇳", "СНГ": "🗺️", "Турция": "🇹🇷", "Индия/ЮВА": "🇮🇳"}
    reg_emoji = _DEFAULT_EMOJI.get(reg, "🌍")

    lines = [
        f"{t_emoji} НОВАЯ ЗАЯВКА #{req_id:04d}",
        "",
        f"🌍 Направление: {reg_emoji} {reg}",
        f"📦 Тип перевозки: {t_cat}",
        "",
        f"📍 Откуда: {v('route_from', '?')} ➔ Куда: {v('route_to', '?')}",
    ]

    # Optional address lines — only show if filled
    for key, label in [("loading_address", "Погрузка"), ("customs_address", "Затаможка"), ("clearance_address", "Растаможка"), ("unloading_address", "Выгрузка")]:
        val = v(key)
        if val: lines.append(f"📍 {label}: {val}")

    lines.append("")
    lines.append(f"📦 Груз: {v('cargo_name', '?')}")
    if v('hs_code'): lines.append(f"📦 ТН ВЭД: {v('hs_code')}")
    if v('dangerous_cargo') and v('dangerous_cargo') not in ('Нет',): lines.append(f"⚠️ Опасный: {v('dangerous_cargo')}")
    lines.append("")
    if v('cargo_weight'): lines.append(f"⚖️ Вес: {v('cargo_weight')}")
    if v('cargo_places'): lines.append(f"📏 Места/Объем: {v('cargo_places')}")
    if v('packaging'): lines.append(f"📦 Упаковка: {v('packaging')}")
    lines.append("")
    lines.append(f"💰 Стоимость: {v('cargo_value') or 'НЕ УКАЗАНА ⚠️'}")
    lines.append(f"🕒 Срочность: {v('urgency_type') or v('urgency_days') or 'Стандарт'}")

    # Specific fields
    spec_map = {
        "delivery_terms_eu": "Условия", "route_type": "Маршрут", "export_decl": "Экспортная", 
        "origin_cert": "Сертификат", "road_type_cn": "Тип фуры", "border_crossing_cn": "Погранпереход",
        "container_owner": "Контейнер", "glonass_seal": "Пломба", "loading_days": "Дней на погрузку",
        "customs_days": "Дней на затаможку", "stackable": "Штабелируемый", "flight_type": "Рейс", "ports_list": "Порт"
    }
    spec_fields = [f"• {label}: {v(k)}" for k, label in spec_map.items() if v(k)]
    
    if spec_fields:
        lines.append("")
        lines.append("📋 Специфика:")
        lines.extend(spec_fields)
    
    if v('message_text'):
        lines.extend(["", "📄 Дополнительно:", v('message_text')])
    
    lines.extend(["", f"👤 Менеджер: {v('responsible') or '—'}", "#заявка"])
    return "\n".join(lines)

def build_bid_card(bid: dict) -> str:
    """Build a unified, professional card for a bid/rate.

    Uses plain text (no Markdown/HTML markers) so it can be sent with any
    parse_mode or none at all. The caller can wrap in <b> if needed.
    """
    lines = [
        f"💰 НОВАЯ СТАВКА",
        f"📦 По заявке: #{int(bid.get('request_id', 0)):05d}",
        "",
        f"💵 СУММА: {bid.get('amount')} {bid.get('currency')}",
        f"👤 Менеджер: {bid.get('manager_name') or '-'}",
        f"📅 Валидность: {bid.get('validity') or '-'}",
        "",
        "ℹ️ УСЛОВИЯ:",
        f"⏳ П/В: {bid.get('loading_hours') or '24ч'}",
        f"⏳ Простой: {bid.get('demurrage') or '-'}",
        f"💳 Оплата: {bid.get('payment') or bid.get('payment_method') or bid.get('payment_terms') or '-'}",
        "",
        "📝 КОММЕНТАРИЙ:",
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

        log.info(f"Attempting get_discussion_message: chat={target_chat}, msg={channel_msg_id}")
        # This call finds the forwarded message in the group
        discussion_msg = await bot.get_discussion_message(chat_id=target_chat, message_id=int(channel_msg_id))
        
        # Using explicit bot.send_message with the chat_id from the message itself is the most rock-solid method
        log.info(f"Discussion msg found: {discussion_msg.message_id}. Sending explicit reply to {discussion_msg.chat_id}")
        
        await bot.send_message(
            chat_id=discussion_msg.chat_id,
            text=bid_card_text,
            reply_to_message_id=discussion_msg.message_id,
            parse_mode="HTML"
        )
        return True
    except Exception as e:
        log.error(f"get_discussion_message failed: {e}")
        # Fallback to top-level message if get_discussion_message fails
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
