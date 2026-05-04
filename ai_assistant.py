import os
import json
import logging
import asyncio
from datetime import datetime
from openai import AsyncOpenAI
import pytz

logger = logging.getLogger(__name__)
TZ = pytz.timezone("Asia/Tashkent")

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
        extra = settings.get("ai_prompt_extra", "") if settings else ""
        strictness = settings.get("ai_strictness", "medium") if settings else "medium"
        
        strict_note = "BE VERY STRICT." if strictness == "high" else ""
        
        # Dynamic regions from settings
        regions_list = settings.get("regions", []) if settings else []
        if regions_list and isinstance(regions_list, list):
            region_names = [r["name"] if isinstance(r, dict) else str(r) for r in regions_list]
            regions_str = "|".join(region_names)
        else:
            regions_str = "СНГ|Европа|Китай|Турция|Индия/ЮВА|Другое"

        # Dynamic transport types from settings
        transport_types = settings.get("transport_types", []) if settings else []
        if transport_types and isinstance(transport_types, list):
            transport_str = "|".join(str(t) for t in transport_types)
        else:
            transport_str = "Авто|Контейнер|Ж/Д Вагон|Авиа|Мультимодальная"

        return f"""You are a professional logistics coordinator for AGL.
Your goal is to collect data for a transport request. YOU MUST BE SMART AND UNDERSTAND SLANG.

{strict_note}
{extra}

ПРАВИЛА ПОНИМАНИЯ ДАННЫХ (ОЧЕНЬ ВАЖНО):
- Если клиент пишет "затаможка на месте", "затаможка там же", "ТТ на месте" — ты ОБЯЗАН скопировать Город/Адрес погрузки в поле `customs_address`!
- Если клиент пишет "растаможка на месте", "растаможка там же", "РТ на месте" — ты ОБЯЗАН скопировать Город/Адрес выгрузки в поле `clearance_address`!
- Термины: "20ка", "сорокафутовый", "реф" — это типы транспорта (Контейнер/Авто).
- Маршруты: "Т1", "Т3", "БТК", "через КЗ", "LTL" (сборка).
- Инкотермс: "EXW", "FCA", "DAP", "CIF", "FOB".
- Стоимость: "цена 2000", "за две тысячи" — это cargo_value (обязательно добавь валюту, например "2000 USD").
- Срочность: "горим", "ASAP", "вчера", "срочно" — ставь urgency_type = "Срочно".
- Ты должен быть умным: если пишут "Груз: яблоки, 20 тонн", ты понимаешь что это `cargo_name` and `cargo_weight`.

ДОСТУПНЫЕ РЕГИОНЫ: {regions_str}
Ты ОБЯЗАН выбрать regions ТОЛЬКО из списка выше. Если маршрут не подходит ни к одному — ставь "Другое".

ДОСТУПНЫЕ ТИПЫ ТРАНСПОРТА: {transport_str}
Ты ОБЯЗАН выбрать transport_cat ТОЛЬКО из списка выше.

ОБЯЗАТЕЛЬНЫЕ ПОЛЯ ДЛЯ `ready_to_publish: true`:
1. 🚛 Транспорт (transport_cat).
2. 📍 Откуда/Куда (route_from/route_to).
3. 📍 Затаможка/Растаможка (customs_address/clearance_address) — ОБЯЗАТЕЛЬНО для всех, КРОМЕ СНГ. Если не указаны — ставь false.
4. 💰 Стоимость (cargo_value) и 📝 ТН ВЭД (hs_code) — ОБЯЗАТЕЛЬНО. Если не указаны — ставь false.
5. ⚖️ Вес (cargo_weight) and 📦 Места (cargo_places) — ОБЯЗАТЕЛЬНО.

ФОРМАТ ОТВЕТА (JSON):
{{
  "regions": "одно значение из списка выше",
  "transport_cat": "Авто|Контейнер|Ж/Д Вагон|Авиа|Мультимодальная",
  "route_from": "...", "route_to": "...",
  "loading_address": "...", "customs_address": "...", "clearance_address": "...", "unloading_address": "...",
  "cargo_name": "...", "hs_code": "...", "cargo_value": "...", "cargo_weight": "...", "cargo_places": "...",
  "missing_fields": ["поля, которых не хватает"],
  "next_question": "Твой вежливый вопрос для уточнения деталей",
  "ready_to_publish": boolean,
  "not_logistics": boolean
}}

Если `ready_to_publish` = false, в `next_question` ты должен спросить именно те поля, которых не хватает.
Если пользователь попросил "укажи отдельно текстом что затаможка на месте", добавь это в `next_question` или просто заполни поля адресов.
Сегодняшняя дата: {today}
"""

    async def parse_request(self, text, current_draft=None, templates=None):
        if not self.enabled: return {"error": "AI Assistant disabled"}
        try:
            from database import db
            settings = await db.get_settings()
            messages = [{"role": "system", "content": self._get_system_prompt(settings)}]
            if current_draft:
                messages.append({"role": "system", "content": f"Current draft: {json.dumps(current_draft, ensure_ascii=False)}"})
            if templates:
                messages.append({"role": "system", "content": f"Past requests: {json.dumps(templates, ensure_ascii=False)}"})
            
            messages.append({"role": "user", "content": text})
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.1, timeout=15.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"AI Parse error: {e}")
            return {"error": str(e)}

    async def process_intent(self, text: str):
        """Smart intent routing — determines what the user wants to do."""
        if not self.enabled:
            return {"error": "AI Assistant disabled"}
        try:
            messages = [
                {"role": "system", "content": """You are an intent classifier for a logistics company AGL.
Classify the user's message into one of these intents:
- "create_request" — user wants to create a new transport request or add details to an existing draft
- "create_bid" — user wants to place a bid/rate on a request (look for words like "ставка", "предложение", amount + route)
- "recall_request" — user wants to find and reuse an old request
- "cancel_request" — user wants to cancel the current draft
- "query_database" — user asks about stats, reports, or wants to find specific data
- "chat" — general conversation, greetings, or non-logistics talk

For create_bid, also extract: route_search (text to search for the request), amount (number), currency (default USD).
For recall_request, extract: query (search text).
For cancel_request, extract: confirmed (true if explicit).
For chat, include a brief text response.

Respond in JSON: {"intent": "...", "args": {...}, "text": "..."} """},
                {"role": "user", "content": text}
            ]
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.1, timeout=10.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Intent classification error: {e}")
            return {"intent": "create_request", "args": {}}

    def build_preview(self, draft: dict) -> str:
        """Build a human-readable preview of the current draft for the user."""
        lines = []
        field_labels = {
            "regions": "🌍 Направление", "transport_cat": "🚛 Транспорт",
            "route_from": "📍 Откуда", "route_to": "📍 Куда",
            "cargo_name": "📦 Груз", "cargo_weight": "⚖️ Вес",
            "cargo_places": "📏 Места/Объем", "cargo_value": "💰 Стоимость",
            "hs_code": "📝 ТН ВЭД", "customs_address": "🏛 Затаможка",
            "clearance_address": "🏛 Растаможка", "loading_address": "📍 Погрузка",
            "unloading_address": "📍 Выгрузка", "urgency_type": "🕒 Срочность",
        }
        for key, label in field_labels.items():
            val = draft.get(key)
            if val and str(val).strip() not in ("", "-", "None", "False"):
                lines.append(f"{label}: *{val}*")

        missing = draft.get("missing_fields", [])
        if missing:
            lines.append(f"\n⚠️ Не хватает: {', '.join(missing)}")

        question = draft.get("next_question")
        if question:
            lines.append(f"\n🤖 {question}")

        return "\n".join(lines) if lines else "📋 Черновик пуст"

    def merge_parsed_data(self, old_draft: dict, new_data: dict) -> dict:
        """Merge newly parsed data into the existing draft. New values overwrite old ones."""
        merged = dict(old_draft) if old_draft else {}
        skip_keys = {"not_logistics", "error"}
        for k, v in new_data.items():
            if k in skip_keys:
                continue
            if v is not None and str(v).strip() not in ("", "-", "None", "null"):
                merged[k] = v
        return merged

    def to_request_fields(self, draft: dict) -> dict:
        """Convert AI draft to a dict suitable for db.create_request()."""
        db_fields = {}
        field_map = {
            "regions": "regions", "transport_cat": "transport_cat",
            "route_from": "route_from", "route_to": "route_to",
            "loading_address": "loading_address", "customs_address": "customs_address",
            "clearance_address": "clearance_address", "unloading_address": "unloading_address",
            "cargo_name": "cargo_name", "hs_code": "hs_code",
            "cargo_value": "cargo_value", "cargo_weight": "cargo_weight",
            "cargo_places": "cargo_places", "urgency_type": "urgency_type",
        }
        for draft_key, db_key in field_map.items():
            val = draft.get(draft_key)
            if val and str(val).strip() not in ("", "-", "None", "null"):
                db_fields[db_key] = str(val).strip()
        return db_fields

    async def answer_db_query(self, question: str, db_module) -> str:
        """Answer user questions about the database using AI + real data."""
        if not self.enabled:
            return "AI отключен"
        try:
            stats = await db_module.get_stats(days=0)
            recent = await db_module.list_requests(limit=5)
            context_data = {
                "stats": stats,
                "recent_requests": [{"id": r["id"], "route": f"{r.get('route_from')} → {r.get('route_to')}", "status": r.get("status"), "cargo": r.get("cargo_name")} for r in recent]
            }
            messages = [
                {"role": "system", "content": f"You are a data analyst for AGL logistics. Answer the user's question based on this data:\n{json.dumps(context_data, ensure_ascii=False, default=str)}\n\nBe concise. Use Russian. Format numbers clearly."},
                {"role": "user", "content": question}
            ]
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0.3, timeout=15.0
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"DB query AI error: {e}")
            return f"Ошибка при обработке запроса: {e}"

    async def transcribe_audio(self, file_path: str):
        if not self.enabled: return None
        try:
            with open(file_path, "rb") as audio_file:
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file
                )
                return transcript.text
        except Exception as e:
            logger.error(f"Whisper error: {e}")
            return None

ai_assistant = AIAssistant()
