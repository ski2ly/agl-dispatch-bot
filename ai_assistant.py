import os
import json
import logging
import asyncio
from datetime import datetime
from openai import AsyncOpenAI
import pytz

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Asia/Tashkent")


# ──────────────────────────────────────────────────────────
# Few-Shot examples: Real requests the user tested with,
# paired with the IDEAL JSON output.  gpt-4o-mini learns
# the pattern from these concrete examples far better than
# from abstract rules.
# ──────────────────────────────────────────────────────────
FEW_SHOT = [
    # 1. Charter to UAE
    {
        "user": (
            "Ташкент - Абудаби\n"
            "Заказчик: агентство военной промышленности при мин обороне РУз\n"
            "Требуется чартер\n"
            "Дата отгрузки 14-15 мая\n"
            "Везут груз на выставку IDEX 2025\n"
            "Раньше возили на собственном ИЛ76, сейчас груз туда не вмещается. "
            "Ищут решение, срочное.\n"
            "Вопрос скорости, не денег"
        ),
        "assistant": json.dumps({
            "regions": "ОАЭ",
            "client_company": "Агентство военной промышленности при МО РУз",
            "urgency_type": "Срочно",
            "transport_cat": "Авиа",
            "transport_sub": "Чартер",
            "delivery_terms": None,
            "route_from": "Ташкент, Узбекистан",
            "route_to": "Абу-Даби, ОАЭ",
            "via": None,
            "loading_address": None,
            "unloading_address": None,
            "customs_address": None,
            "clearance_address": None,
            "cargo_name": "Груз для выставки IDEX 2025",
            "hs_code": None,
            "adr_class": None,
            "cargo_weight": None,
            "cargo_volume": None,
            "cargo_places": None,
            "cargo_value": None,
            "cargo_currency": None,
            "packing_type": None,
            "loading_type": None,
            "special_conditions": None,
            "temp_control": None,
            "temp_range": None,
            "cargo_readiness": "14-15 мая",
            "target": None,
            "extra_info": "Раньше возили на собственном ИЛ76, сейчас груз туда не вмещается. Вопрос скорости, не денег.",
            "missing_fields": ["Вес", "Объем", "Стоимость", "ТН ВЭД"],
            "next_question": "Уточните вес, объем, стоимость груза и ТН ВЭД?"
        }, ensure_ascii=False),
    },
    # 2. Philippines container (Pineapples)
    {
        "user": (
            "Порт General Santos (Филиппины)\n"
            "До Ташкента\n"
            "20 тонн\n"
            "Контейнер без режима нужен 20 фут контейнер\n"
            "Код - 2008207900\n"
            "Груз: консервированные ананасы в железных баночках\n"
            "Грузят мягкими слип-шитами, поэтому перегрузка ручная будет."
        ),
        "assistant": json.dumps({
            "regions": "Индия/ЮВА",
            "client_company": None,
            "urgency_type": "Стандарт",
            "transport_cat": "Контейнер",
            "transport_sub": "20 фут",
            "delivery_terms": None,
            "route_from": "General Santos, Филиппины",
            "route_to": "Ташкент, Узбекистан",
            "via": None,
            "loading_address": None,
            "unloading_address": None,
            "customs_address": None,
            "clearance_address": None,
            "cargo_name": "Консервированные ананасы в железных баночках",
            "hs_code": "2008207900",
            "adr_class": None,
            "cargo_weight": "20000",
            "cargo_volume": None,
            "cargo_places": None,
            "cargo_value": None,
            "cargo_currency": None,
            "packing_type": "Железные банки, слип-шиты мягкие",
            "loading_type": None,
            "special_conditions": "Ручная перегрузка (погрузка слип-шитами)",
            "temp_control": None,
            "temp_range": None,
            "cargo_readiness": None,
            "target": None,
            "extra_info": "Контейнер без температурного режима.",
            "missing_fields": ["Заказчик", "Стоимость", "Мест", "Готовность"],
            "next_question": "Уточните заказчика, стоимость груза, количество мест и готовность?"
        }, ensure_ascii=False),
    },
    # 3. EU road via Turkey
    {
        "user": (
            "EXW Vilnius - Fergana\n"
            "loading: Lithuania, Vilnius\n"
            "delivery address: Tashkent\n"
            "route: Via Turkiye\n"
            "cargo: Novoflow 165 (non DG chemicals) liquid\n"
            "packing: ibc container\n"
            "hs code: 3402901000\n"
            "qty: 20 pcs\n"
            "GW: 20 tons\n"
            "loading: 22/10/2025\n"
            "cargo is made in Netherlands\n"
            "need: rate, route, transit time, EX1"
        ),
        "assistant": json.dumps({
            "regions": "Европа",
            "client_company": None,
            "urgency_type": "Стандарт",
            "transport_cat": "Авто",
            "transport_sub": None,
            "delivery_terms": "EXW",
            "route_from": "Вильнюс, Литва",
            "route_to": "Фергана, Узбекистан",
            "via": "Турция",
            "loading_address": "Lithuania, Vilnius",
            "unloading_address": "Tashkent",
            "customs_address": None,
            "clearance_address": None,
            "cargo_name": "Novoflow 165 (non DG chemicals) liquid",
            "hs_code": "3402901000",
            "adr_class": None,
            "cargo_weight": "20000",
            "cargo_volume": None,
            "cargo_places": "20 pcs",
            "cargo_value": None,
            "cargo_currency": None,
            "packing_type": "ibc container",
            "loading_type": None,
            "special_conditions": None,
            "temp_control": None,
            "temp_range": None,
            "cargo_readiness": "22.10.2025",
            "target": None,
            "extra_info": "Груз произведен в Нидерландах. Нужно: ставка, маршрут, транзитное время, EX1.",
            "missing_fields": ["Заказчик", "Объем", "Стоимость"],
            "next_question": "Уточните заказчика, объем и стоимость груза?"
        }, ensure_ascii=False),
    },
    # 4. China pallets
    {
        "user": (
            "Китай, провинция Гуандун, город Дунгуань\n"
            "Узбекистан, Ташкент, улица Уйсозлар, 41\n"
            "Объем / Вес: 86 м3 / 3500 кг\n"
            "Тент\n"
            "Груз состоит из бритв Schick и бритвенных картриджей. "
            "Код ТН ВЭД: 8212101000. Общее количество паллет: 29. "
            "Размер каждой паллеты составляет 1100 x 1100 x 990 мм. "
            "При штабелировании высота составит около 2000 мм "
            "(получается 15 паллет -1100х1100х2000 мм). "
            "Вес брутто: около 3,5 тонн. "
            "вам также необходимо будет напрямую связаться с поставщиками "
            "и согласовать дату отгрузки\n"
            "Примерная дата запланирована на 25.11"
        ),
        "assistant": json.dumps({
            "regions": "Китай",
            "client_company": None,
            "urgency_type": "Стандарт",
            "transport_cat": "Авто",
            "transport_sub": "Тент",
            "delivery_terms": None,
            "route_from": "Дунгуань, Китай",
            "route_to": "Ташкент, Узбекистан",
            "via": None,
            "loading_address": None,
            "unloading_address": "ул. Уйсозлар, 41, Ташкент",
            "customs_address": None,
            "clearance_address": None,
            "cargo_name": "Бритвы Schick и бритвенные картриджи",
            "hs_code": "8212101000",
            "adr_class": None,
            "cargo_weight": "3500",
            "cargo_volume": "86",
            "cargo_places": "29 паллет",
            "cargo_value": None,
            "cargo_currency": None,
            "packing_type": "Паллеты 1100x1100x990 мм",
            "loading_type": None,
            "special_conditions": "Штабелирование (15 паллет 1100x1100x2000 мм)",
            "temp_control": None,
            "temp_range": None,
            "cargo_readiness": "25.11",
            "target": None,
            "extra_info": "Необходимо напрямую связаться с поставщиками и согласовать дату отгрузки.",
            "missing_fields": ["Заказчик", "Стоимость"],
            "next_question": "Кто заказчик и какова стоимость груза?"
        }, ensure_ascii=False),
    },
    # 5. CIS road
    {
        "user": (
            "Навои - Клайпеда\n"
            "груз - Аммиачная селитра\n"
            "АДР - 3 класс\n"
            "1 тент, груз готов"
        ),
        "assistant": json.dumps({
            "regions": "Европа",
            "client_company": None,
            "urgency_type": "Стандарт",
            "transport_cat": "Авто",
            "transport_sub": "Тент",
            "delivery_terms": None,
            "route_from": "Навои, Узбекистан",
            "route_to": "Клайпеда, Литва",
            "via": None,
            "loading_address": None,
            "unloading_address": None,
            "customs_address": None,
            "clearance_address": None,
            "cargo_name": "Аммиачная селитра",
            "hs_code": None,
            "adr_class": "3",
            "cargo_weight": None,
            "cargo_volume": "90",
            "cargo_places": None,
            "cargo_value": None,
            "cargo_currency": None,
            "packing_type": None,
            "loading_type": None,
            "special_conditions": None,
            "temp_control": None,
            "temp_range": None,
            "cargo_readiness": "Груз готов",
            "target": None,
            "extra_info": None,
            "missing_fields": ["Заказчик", "Вес", "Стоимость", "ТН ВЭД"],
            "next_question": "Уточните заказчика, вес и стоимость груза?"
        }, ensure_ascii=False),
    },
    # 6. Reefer – temperature, customs
    {
        "user": (
            "Вильнюс - Ташкент через Терехова реф +15С\n"
            "затаможка на месте\n"
            "Брутто - 4000 евро"
        ),
        "assistant": json.dumps({
            "regions": "Европа",
            "client_company": None,
            "urgency_type": "Стандарт",
            "transport_cat": "Авто",
            "transport_sub": "Реф",
            "delivery_terms": None,
            "route_from": "Вильнюс, Литва",
            "route_to": "Ташкент, Узбекистан",
            "via": "Терехова",
            "loading_address": None,
            "unloading_address": None,
            "customs_address": "на месте",
            "clearance_address": None,
            "cargo_name": "Рефрижераторный груз",
            "hs_code": None,
            "adr_class": None,
            "cargo_weight": None,
            "cargo_volume": None,
            "cargo_places": None,
            "cargo_value": "4000",
            "cargo_currency": "EUR",
            "packing_type": None,
            "loading_type": None,
            "special_conditions": None,
            "temp_control": "Да",
            "temp_range": "+15С",
            "cargo_readiness": None,
            "target": None,
            "extra_info": None,
            "missing_fields": ["Заказчик", "Вес", "Объем", "ТН ВЭД", "Готовность"],
            "next_question": "Уточните заказчика, вес, объем и готовность груза?"
        }, ensure_ascii=False),
    },
]


class AIAssistant:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.enabled = bool(api_key)
        self.client = AsyncOpenAI(api_key=api_key) if self.enabled else None
        if self.enabled:
            logger.info(f"AI Assistant enabled (model: {self.model})")

    def _get_system_prompt(self, settings=None):
        today = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
        regions_list = settings.get("regions", []) if settings else []
        regions_str = ", ".join(
            [r["name"] if isinstance(r, dict) else str(r) for r in regions_list]
        )

        return f"""Ты — старший логист-диспетчер AGL. Твоя задача: из текста клиента собрать
МАКСИМАЛЬНО ПОЛНУЮ карточку заявки в JSON. Ни одно слово клиента не должно
быть потеряно.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 1 — ПРАВИЛО «ПЫЛЕСОСА» (ВЫПОЛНЯЙ ПЕРВЫМ)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Прочитай весь текст клиента. Мысленно пройдись по КАЖДОМУ предложению
и спроси: «Эта информация поможет агенту быстрее и точнее дать цену?»
Если ДА — она ОБЯЗАНА попасть в карточку: либо в конкретное поле,
либо в extra_info. Выбрасывать нельзя ничего.

Примеры деталей которые ОБЯЗАТЕЛЬНО сохранять:
- тип упаковки: паллеты, слип-шиты, IBC, железные банки, пачки
- условия погрузки/выгрузки: ручная перегрузка, слип-шиты → ручная перегрузка
- хрупкость, штабелирование, спецтребования к машине
- маршрутные особенности: через какую страну, пограничный переход
- причина срочности
- контакт с поставщиком нужен или нет
- что нужно от перевозчика (ставка, маршрут, транзитное время, EX1 и т.д.)
- дата погрузки / готовность груза
- страна производства груза

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 2 — ЗАПОЛНИ ПОЛЯ JSON
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Верни ТОЛЬКО валидный JSON, без пояснений, без markdown-блоков.

{{
  "regions":             // Один из: Европа, СНГ, Китай, Индия/ЮВА, ОАЭ, Другое
  "client_company":      // Название компании-заказчика или null
  "urgency_type":        // "Стандарт" или "Срочно"
                         // Срочно — ТОЛЬКО если клиент написал "срочно", "горит",
                         // "вопрос скорости", "срочная" или аналог

  "transport_cat":       // "Авто" | "Авиа" | "Контейнер" | "Море" | "ЖД"
  "transport_sub":       // "Тент" | "Реф" | "Мега" | "Чартер" | "20 фут" | "40 фут" | null

  "delivery_terms":      // EXW / FOB / CIF и т.д. или null
  "route_from":          // Город, Страна (полностью)
  "route_to":            // Город, Страна (полностью)
  "via":                 // Маршрут через: страна/переход, или null

  "loading_address":     // Точный адрес погрузки если указан, или null
  "unloading_address":   // Точный адрес выгрузки если указан, или null
  "customs_address":     // Место затаможки если указано, или null
  "clearance_address":   // Место растаможки если указано, или null

  "cargo_name":          // Полное название груза
  "hs_code":             // ТН ВЭД / HS code или null
  "adr_class":           // Класс опасности ADR или null

  "cargo_weight":        // Только число в кг (20 тонн → "20000") или null
  "cargo_volume":        // Только число в м³ или null
                         // АВТОЗАПОЛНЕНИЕ: Тент=90, Мега=110 — ТОЛЬКО если объём
                         // не указан явно И тип транспорта известен
  "cargo_places":        // Текстом: "29 паллет", "20 pcs", "15 мест" или null
  "cargo_value":         // Число (стоимость груза) или null
  "cargo_currency":      // "USD" / "EUR" / "UZS" и т.д. или null

  "packing_type":        // Тип упаковки: "паллеты", "IBC", "слип-шиты", "пачки",
                         // "железные банки", "мешки" и т.д. или null

  "loading_type":        // Вид погрузки: "Задняя", "Боковая", "Верхняя",
                         // "Полная растентовка" — через запятую если несколько, или null

  "special_conditions":  // Особые условия перевозки одной строкой:
                         // "Ручная перегрузка", "Хрупкий", "Штабелирование 2 яруса",
                         // "Не кантовать" и т.д. или null
                         // ← ИМЕННО СЮДА идут слип-шиты → ручная перегрузка

  "temp_control":        // "Да" если реф/температурный режим, иначе null
  "temp_range":          // "+15С", "-18С", "от 0 до +5С" и т.д. или null

  "cargo_readiness":     // "Груз готов" / "Запрос ставки" / конкретная дата "15.05.2026"
  "target":              // Целевая ставка если указана: "$1500", "1200 USD" или null

  "extra_info":          // ВСЁ что не вошло в поля выше, одним абзацем.
                         // Размеры паллет, инструкции по контакту с поставщиком,
                         // что нужно от перевозчика (ставка/маршрут/транзитное время),
                         // страна производства, причина срочности, любые детали.
                         // Если нечего писать — null.

  "missing_fields":      // Список на русском из того что НЕ указано клиентом:
                         // ["Заказчик", "Вес", "Объем", "Стоимость", "ТН ВЭД",
                         //  "Мест", "Таргет", "Готовность"]
                         // Вес и Объем — обязательные поля для авто/контейнер.
                         // Стоимость — обязательное поле всегда.

  "next_question":       // Один вопрос со списком ВСЕГО недостающего сразу.
                         // Пример: "Уточните заказчика, вес груза и стоимость?"
                         // Не задавай вопрос если missing_fields пустой.
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 3 — ПРОВЕРКА ПЕРЕД ОТПРАВКОЙ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Перечитай исходный текст клиента ещё раз.
Найди любое предложение которое НЕ отражено в JSON.
Если нашёл — добавь в extra_info или в нужное поле.
Только после этого верни ответ.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПРАВИЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Числа: только цифры без единиц (кг, м³ писать нельзя в числовых полях)
• Null вместо пустой строки, "None", "-"
• Регион: определи страну каждого города и сопоставь с регионом
• Сегодня: {today}
• Регионы системы: {regions_str}"""

    def _build_messages(self, text, settings, current_draft, history):
        """Build the full message list: system + few-shot + context + user."""
        messages = [{"role": "system", "content": self._get_system_prompt(settings)}]

        # Few-shot examples
        for ex in FEW_SHOT:
            messages.append({"role": "user", "content": ex["user"]})
            messages.append({"role": "assistant", "content": ex["assistant"]})

        # Conversation history
        if history:
            for h in history:
                role = "user" if h.get("is_user") else "assistant"
                messages.append({"role": role, "content": h.get("text", "")})

        # Current draft context
        if current_draft:
            messages.append({
                "role": "system",
                "content": f"Текущий черновик: {json.dumps(current_draft, ensure_ascii=False)}"
            })

        # User's new message
        messages.append({"role": "user", "content": text})
        return messages

    async def parse_request(self, text, current_draft=None, templates=None, history=None):
        if not self.enabled:
            return {"error": "AI Assistant disabled"}
        try:
            from database import db
            settings = await db.get_settings()
            messages = self._build_messages(text, settings, current_draft, history)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"parse_request error: {e}", exc_info=True)
            return {"error": str(e)}

    async def process_intent(self, text: str):
        if not self.enabled:
            return {"error": "AI Assistant disabled"}
        try:
            messages = [
                {"role": "system", "content": "Классифицируй намерение."},
                {"role": "user", "content": text},
            ]
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            return json.loads(response.choices[0].message.content)
        except Exception:
            return {"intent": "create_request", "args": {}}

    # build_preview kept for backward compatibility (used in confirm_ai_logic
    # validation branch), but the main preview path uses build_card from helpers.
    def build_preview(self, draft: dict) -> str:
        from utils.helpers import build_card
        return build_card(draft)

    def merge_parsed_data(self, old_draft: dict, new_data: dict) -> dict:
        merged = dict(old_draft) if old_draft else {}
        skip_keys = {
            "not_logistics", "error", "next_question",
            "missing_fields", "ready_to_publish", "_reasoning",
        }
        for k, v in new_data.items():
            if k in skip_keys:
                continue
            if v in (None, "null", "", "-", "None"):
                # Don't overwrite existing data with empty values
                continue
            merged[k] = v
        merged["ready_to_publish"] = new_data.get("ready_to_publish", False)
        merged["next_question"] = new_data.get("next_question")
        merged["missing_fields"] = new_data.get("missing_fields", [])
        return merged

    def to_request_fields(self, draft: dict) -> dict:
        db_fields = {}
        field_map = {
            "regions": "regions",
            "transport_cat": "transport_cat",
            "transport_sub": "transport_sub",
            "delivery_terms": "delivery_terms",
            "route_from": "route_from",
            "route_to": "route_to",
            "loading_address": "loading_address",
            "customs_address": "customs_address",
            "clearance_address": "clearance_address",
            "unloading_address": "unloading_address",
            "cargo_name": "cargo_name",
            "hs_code": "hs_code",
            "client_company": "client_company",
            "cargo_value": "cargo_value",
            "cargo_currency": "cargo_currency",
            "cargo_weight": "cargo_weight",
            "cargo_places": "cargo_places",
            "cargo_volume": "cargo_volume",
            "urgency_type": "urgency_type",
            "extra_info": "message_text",
            "temp_control": "temp_control",
            "temp_range": "temp_range",
            "adr_class": "adr_class",
            "transit_info": "transit_rf_allowed",
            "border_crossing_cn": "border_crossing_cn",
            "target": "target",
            "cargo_readiness": "cargo_readiness",
            "packing_type": "packing_type",
            "loading_type": "loading_type",
            "special_conditions": "special_conditions",
            "via": "via",
        }
        for draft_key, db_key in field_map.items():
            val = draft.get(draft_key)
            if val and str(val).strip() not in ("", "-", "None", "null"):
                db_fields[db_key] = str(val).strip()
        
        if db_fields.get("adr_class"):
            db_fields["dangerous_cargo"] = "Да"
            
        return db_fields

    async def answer_db_query(self, question: str, db_module) -> str:
        try:
            messages = [
                {"role": "system", "content": "Ты аналитик AGL."},
                {"role": "user", "content": question},
            ]
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0.0
            )
            return response.choices[0].message.content
        except Exception:
            return "Ошибка"

    async def transcribe_audio(self, file_path: str):
        try:
            with open(file_path, "rb") as audio_file:
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1", file=audio_file
                )
                return transcript.text
        except Exception:
            return None


ai_assistant = AIAssistant()
