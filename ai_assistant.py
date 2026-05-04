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
        
        # Define region mapping logic for the AI
ПРАВИЛА ОПРЕДЕЛЕНИЯ РЕГИОНА:
Используй свои знания географии. Ты должен отнести перевозку к одному из регионов: {regions_str}.
- Если маршрут связан с Китаем — Китай.
- Если маршрут связан с Европой — Европа.
- Если маршрут связан с Турцией (не транзит) — Турция.
- Если маршрут связан с Индией или ЮВА — Индия/ЮВА.
- Если ОБЕ точки внутри СНГ — СНГ.
- В остальных случаях — Другое.
Будь умным: Вильнюс — это Литва, Литва — это Европа.

        # Dynamic transport types from settings
        transport_types = settings.get("transport_types", []) if settings else []
        if transport_types and isinstance(transport_types, list):
            transport_str = "|".join(str(t) for t in transport_types)
        else:
            transport_str = "Авто|Контейнер|Ж/Д Вагон|Авиа|Мультимодальная"

        return f"""You are an expert Logistics Coordinator for AGL. Your job is to help users create cargo requests through natural conversation.

CORE PHILOSOPHY:
- THINK LIKE A HUMAN LOGISTICIAN: Don't just follow scripts. Use your vast knowledge of geography, world maps, shipping terms, and logistics jargon.
- FACTUAL INTEGRITY: NEVER, UNDER ANY CIRCUMSTANCES, INVENT DATA. If the user didn't mention a transport type, container size, or weight — leave it empty. guessing is a CRITICAL ERROR.
- BE PROACTIVE: If you see a weight/volume mismatch or a logical error, ask about it.
- COMPREHENSIVE DATA: Capture every single detail mentioned (packaging, temperature, transit through X, certificates, requirements). If it fits a specific field — put it there. If not — put it in `extra_info`.
- SMART EDITING: If a user says "remove X" or "change Y", do exactly that to the draft. Don't restart or cancel unless they explicitly say "forget it" or "stop".

GEOGRAPHY & REGIONS:
Classify the route into one of these regions: {regions_str}. Use your head.
- China: Route involves China.
- Europe: Route involves any European country (EU, Baltics, Balkans). Vilnius is Europe.
- Turkey: Route involves Turkey (but not just transit).
- India/SEA: Route involves India or South East Asia.
- CIS (СНГ): Route is entirely within CIS countries.
- Other: Anything else.

LOGISTICS JARGON:
You understand "20ka", "ref", "tent", "LTL", "EX1", "COO", "customs on-site", and all other industry slang. Map them correctly to the structured data.

JSON OUTPUT FORMAT:
{{
  "regions": "one from the list",
  "transport_cat": "{transport_str}",
  "route_from": "City, Country", "route_to": "City, Country",
  "loading_address": "exact spot if known", "customs_address": "exact spot", "clearance_address": "exact spot", "unloading_address": "exact spot",
  "cargo_name": "...", "hs_code": "...", "cargo_value": "...", "cargo_weight": "...", "cargo_places": "...",
  "transit_info": "...", "packaging": "...", "dangerous_cargo": "...",
  "loading_date": "...", "requirements": "...",
  "delivery_terms": "Incoterms", "container_type": "...", "road_type": "...",
  "export_decl": "...", "origin_cert": "...", "stackable": "...",
  "extra_info": "ANY other details from the text",
  "missing_fields": ["short labels of missing core data"],
  "next_question": "Your professional response to the user",
  "ready_to_publish": boolean,
  "not_logistics": boolean
}}

CRITICAL: If a value is unknown, leave it empty or null. DO NOT guess. If user says "non-dangerous", write "Not dangerous". If "no EX1 needed", write "Not needed".

Today's date: {today}
"""

    async def parse_request(self, text, current_draft=None, templates=None, history=None):
        if not self.enabled: return {"error": "AI Assistant disabled"}
        try:
            from database import db
            settings = await db.get_settings()
            messages = [{"role": "system", "content": self._get_system_prompt(settings)}]
            
            if history:
                for h in history:
                    role = "user" if h.get("is_user") else "assistant"
                    messages.append({"role": role, "content": h.get("text", "")})

            if current_draft:
                messages.append({"role": "system", "content": f"Current draft: {json.dumps(current_draft, ensure_ascii=False)}"})
            if templates:
                messages.append({"role": "system", "content": f"Past requests: {json.dumps(templates, ensure_ascii=False)}"})
            
            messages.append({"role": "user", "content": text})
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.1, timeout=30.0
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
- "create_request" — пользователь хочет создать заявку, ДОБАВИТЬ информацию, ИЗМЕНИТЬ данные или УДАЛИТЬ ЧАСТЬ данных (например, "удали вес", "убери инфо про СТ-1").
- "cancel_request" — пользователь хочет ПОЛНОСТЬЮ ПРЕКРАТИТЬ работу над текущей заявкой и УДАЛИТЬ ВЕСЬ ЧЕРНОВИК (например, "забудь", "отмена", "стоп"). Если пользователь просит удалить только ОДНО ПОЛЕ — это НЕ cancel_request!
- "query_database" — user asks about stats, reports, or internal data from DB
- "chat" — ONLY for general greetings, non-logistics talk, or tests.

For create_bid, also extract: route_search (text to search for the request), amount (number), currency (default USD).
For recall_request, extract: query (search text).
For cancel_request, extract: confirmed (true if explicit).
For chat, include a brief text response.

Respond in JSON: {"intent": "...", "args": {...}, "text": "..."} """},
                {"role": "user", "content": text}
            ]
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.1, timeout=30.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Intent classification error: {e}")
            return {"intent": "create_request", "args": {}}

    def build_preview(self, draft: dict) -> str:
        """Build a human-readable preview of the current draft for the user."""
        import html
        lines = []
        field_labels = {
            "regions": "🌍 Направление", "transport_cat": "🚛 Транспорт",
            "route_from": "📍 Откуда", "route_to": "📍 Куда",
            "cargo_name": "📦 Груз", "cargo_weight": "⚖️ Вес",
            "cargo_places": "📏 Места/Объем", "cargo_value": "💰 Стоимость",
            "hs_code": "📝 ТН ВЭД", "customs_address": "🏛 Затаможка",
            "clearance_address": "🏛 Растаможка", "loading_address": "📍 Погрузка",
            "unloading_address": "📍 Выгрузка", "urgency_type": "🕒 Срочность",
            "transit_info": "🛣 Транзит", "packaging": "📦 Упаковка",
            "dangerous_cargo": "⚠️ Опасность", "extra_info": "📝 Доп. инфо",
            "loading_date": "📅 Дата готовности", "requirements": "🎯 Требуется",
            "delivery_terms": "📦 Инкотермс", "container_type": "🏗 Контейнер",
            "road_type": "🚛 Тип авто", "export_decl": "📄 EX1",
            "origin_cert": "📜 Сертификат", "stackable": "🔝 Штабель",
        }
        for key, label in field_labels.items():
            val = draft.get(key)
            # Skip empty, false, null, or placeholder values
            if val is None or str(val).strip().lower() in ("", "-", "none", "false", "null"):
                continue
            
            safe_val = html.escape(str(val))
            lines.append(f"{label}: <b>{safe_val}</b>")

        missing = draft.get("missing_fields", [])
        if missing:
            safe_missing = html.escape(", ".join(missing))
            lines.append(f"\n⚠️ Не хватает: {safe_missing}")

        question = draft.get("next_question")
        if question:
            safe_question = html.escape(str(question))
            lines.append(f"\n🤖 {safe_question}")

        return "\n".join(lines) if lines else "📋 Черновик пуст"

    def merge_parsed_data(self, old_draft: dict, new_data: dict) -> dict:
        """Merge newly parsed data into the existing draft. New values overwrite old ones."""
        merged = dict(old_draft) if old_draft else {}
        skip_keys = {"not_logistics", "error", "next_question", "missing_fields", "ready_to_publish"}
        
        for k, v in new_data.items():
            if k in skip_keys:
                continue
            
            # Explicit deletion/clearing
            if v in (None, "null", "", "-", "None"):
                if k in merged:
                    del merged[k]
                continue
                
            merged[k] = v
        
        # Meta fields are not merged, they come from the latest parse
        merged["ready_to_publish"] = new_data.get("ready_to_publish", False)
        merged["next_question"] = new_data.get("next_question")
        merged["missing_fields"] = new_data.get("missing_fields", [])
        
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
            "transit_info": "transit_rf_allowed", "packaging": "packaging",
            "dangerous_cargo": "dangerous_cargo", "extra_info": "message_text",
            "loading_date": "loading_days", "requirements": "target",
            "delivery_terms": "delivery_terms", "container_type": "container_type",
            "road_type": "road_type", "export_decl": "export_decl",
            "origin_cert": "origin_cert", "stackable": "stackable",
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
