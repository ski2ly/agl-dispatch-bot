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
    # 1. Charter to UAE – urgency, client, extra context
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
            "cargo_name": "Груз для выставки IDEX 2025",
            "cargo_weight": None, "cargo_volume": None,
            "cargo_places": None, "hs_code": None, "adr_class": None,
            "extra_info": (
                "Дата отгрузки 14-15 мая. "
                "Раньше возили на собственном ИЛ76, сейчас груз туда не вмещается. "
                "Вопрос скорости, не денег."
            ),
            "missing_fields": ["Вес", "Объем", "Стоимость", "ТН ВЭД"],
            "next_question": "Уточните вес и объем груза для подбора чартера?"
        }, ensure_ascii=False),
    },
    # 2. Philippines container – HS code, slip-sheets, manual reload
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
            "cargo_name": "Консервированные ананасы в железных баночках",
            "cargo_weight": "20000", "cargo_volume": None,
            "cargo_places": None,
            "hs_code": "2008207900", "adr_class": None,
            "extra_info": (
                "Контейнер без режима. "
                "Грузят мягкими слип-шитами, поэтому перегрузка ручная будет."
            ),
            "missing_fields": ["Заказчик", "Стоимость"],
            "next_question": "Кто заказчик и какова стоимость груза?"
        }, ensure_ascii=False),
    },
    # 3. EU road via Turkey – EXW, HS, route info
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
            "cargo_name": "Novoflow 165 (non DG chemicals) liquid",
            "cargo_weight": "20000", "cargo_volume": None,
            "cargo_places": "20 pcs",
            "hs_code": "3402901000", "adr_class": None,
            "extra_info": (
                "Упаковка: IBC container. Погрузка: Литва, Вильнюс. "
                "Доставка: Ташкент. Маршрут через Турцию. "
                "Дата погрузки 22/10/2025. Груз произведен в Нидерландах. "
                "Нужны: ставка, маршрут, транзитное время, EX1."
            ),
            "missing_fields": ["Заказчик", "Объем", "Стоимость"],
            "next_question": "Уточните заказчика и объем груза?"
        }, ensure_ascii=False),
    },
    # 4. China pallets – pallet dimensions, stacking, supplier contact
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
            "cargo_name": "Бритвы Schick и бритвенные картриджи",
            "cargo_weight": "3500", "cargo_volume": "86",
            "cargo_places": "29 паллет",
            "hs_code": "8212101000", "adr_class": None,
            "extra_info": (
                "Адрес доставки: ул. Уйсозлар, 41, Ташкент. "
                "Размер паллеты 1100x1100x990 мм. "
                "При штабелировании высота ~2000 мм (15 паллет 1100x1100x2000). "
                "Необходимо связаться с поставщиками и согласовать дату отгрузки. "
                "Примерная дата: 25.11."
            ),
            "missing_fields": ["Заказчик", "Стоимость"],
            "next_question": "Кто заказчик и какова стоимость груза?"
        }, ensure_ascii=False),
    },
    # 5. CIS road – ADR, tent, ammonium nitrate
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
            "cargo_name": "Аммиачная селитра",
            "cargo_weight": None, "cargo_volume": "90",
            "cargo_places": None,
            "hs_code": None,
            "adr_class": "3",
            "extra_info": "Груз готов к отправке.",
            "missing_fields": ["Заказчик", "Вес", "Стоимость", "ТН ВЭД"],
            "next_question": "Уточните вес груза и ТН ВЭД?"
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
            "cargo_name": "Рефрижераторный груз",
            "cargo_weight": None, "cargo_volume": None,
            "cargo_places": None,
            "hs_code": None, "adr_class": None,
            "temp_control": "Да", "temp_range": "+15С",
            "extra_info": (
                "Маршрут через Терехова. "
                "Затаможка на месте. "
                "Стоимость груза (брутто): 4000 EUR."
            ),
            "cargo_value": "4000", "cargo_currency": "EUR",
            "missing_fields": ["Заказчик", "Вес", "Объем", "ТН ВЭД"],
            "next_question": "Уточните вес и объем груза?"
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

        return (
            "Ты — старший логист-диспетчер AGL.  "
            "Из текста клиента извлеки ВСЕ данные в JSON.\n\n"
            "РЕГИОНЫ (выбери один): " + regions_str + "\n"
            "Определи страну каждого города и сопоставь с регионом.\n\n"
            "ТРАНСПОРТ:\n"
            "- Тент/Фура/Мега -> transport_cat: Авто\n"
            "- Чартер/Борт/Самолет -> transport_cat: Авиа\n"
            "- Контейнер (20/40 фут) -> transport_cat: Контейнер\n"
            "- Реф -> transport_cat: Авто, transport_sub: Реф\n\n"
            "ОБЪЕМ по умолчанию: Тент=90, Мега=110 (только если объем НЕ указан явно).\n\n"
            "ПРАВИЛО ПЫЛЕСОСА (КРИТИЧНО):\n"
            "Всё, что не попало в конкретное поле (размеры паллет, инструкции "
            "связаться с поставщиком, даты погрузки, причины срочности, описание "
            "упаковки, слип-шиты, штабелирование) — ОБЯЗАТЕЛЬНО пиши в extra_info. "
            "Ничего не выбрасывай.\n\n"
            "ЧИСЛА:\n"
            "- cargo_weight: только число в кг (20 тонн -> 20000)\n"
            "- cargo_volume: только число в м3\n"
            "- cargo_places: текст (29 паллет, 20 pcs)\n\n"
            "СРОЧНОСТЬ: Стандарт. Ставь Срочно только если прямо написано "
            "\"срочно\", \"вопрос скорости\", \"горит\".\n\n"
            "missing_fields: список НА РУССКОМ из: Заказчик, Вес, Объем, "
            "Стоимость, ТН ВЭД, Мест.\n\n"
            "Сегодня: " + today
        )

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
        }
        for draft_key, db_key in field_map.items():
            val = draft.get(draft_key)
            if val and str(val).strip() not in ("", "-", "None", "null"):
                db_fields[db_key] = str(val).strip()
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
