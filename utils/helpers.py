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
    reg_emoji = "🌍"
    if reg == "Европа": reg_emoji = "🇪🇺"
    elif reg == "Китай": reg_emoji = "🇨🇳"
    elif reg == "СНГ": reg_emoji = "🗺️"

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
    """Build a unified, professional card for a bid/rate."""
    lines = [
        "💰 СТАВКА",
        "",
        f"📦 По заявке: #{bid.get('request_id', 0):04d}",
        "",
        f"💵 Стоимость: {bid.get('amount')} {bid.get('currency')}",
        f"📅 Валидность ставки: {bid.get('validity') or '-'}",
        f"👤 Кто дал ставку: {bid.get('manager_name') or '-'}",
        "",
        "📄 Условия:",
        f"- Погрузка/Выгрузка: {bid.get('loading_hours') or '24ч'}",
        f"- Простой: {bid.get('demurrage') or '-'}",
        f"- Оплата: {bid.get('payment') or bid.get('payment_method') or bid.get('payment_terms') or '-'}",
        "",
        "📝 Комментарий:",
        f"{bid.get('comment') or '-'}",
        "",
        "#ставка"
    ]
    return "\n".join(lines)
