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
        regions_list = settings.get("regions", []) if settings else []
        regions_str = "|".join([r["name"] if isinstance(r, dict) else str(r) for r in regions_list])

        return f"""Ты — Робот-Секретарь AGL. Твоя задача: идеально извлечь данные.

### ГЕОГРАФИЯ:
Направление из списка: [{regions_str}]
- ЕВРОПА: ЕС (Литва, Нидерланды и т.д.).
- ОАЭ: Дубай и др.
- ПРИОРИТЕТ: Если одна точка в Европе — регион ЕВРОПА.

### ИНКОТЕРМС И УСЛОВИЯ:
- Находи условия поставки (EXW, FCA, DAP, CIF, FOB и т.д.) и пиши в `delivery_terms`.
- Если указано EX1/T1 — это таможня.

### ПРАВИЛА:
1. ЦИФРЫ: В вес/объем/места пиши ТОЛЬКО ЧИСЛА (20 тонн -> 20000).
2. ТРАНЗИТ: Via... -> `transit_info`.
3. extra_info: Пиши сюда всё остальное (Netherlands origin, loading date и т.д.).

### ФОРМАТ JSON:
{{
  "regions": "Европа",
  "delivery_terms": "EXW",
  "transport_cat": "Авто",
  "route_from": "Вильнюс, Литва", "route_to": "Ташкент, Узбекистан",
  "cargo_name": "Novoflow 165", "cargo_weight": "20000", "cargo_places": "20",
  "transit_info": "Турция",
  "extra_info": "Netherlands origin, EX1 needed, loading 22.10.2025", 
  "missing_fields": ["Стоимость", "Объем"],
  "ready_to_publish": false,
  "next_question": "Уточните, пожалуйста, объем и стоимость груза?"
}}

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
            messages.append({"role": "user", "content": text})
            response = await self.client.chat.completions.create(model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.1)
            return json.loads(response.choices[0].message.content)
        except Exception as e: return {"error": str(e)}

    async def process_intent(self, text: str):
        if not self.enabled: return {"error": "AI Assistant disabled"}
        try:
            messages = [{"role": "system", "content": "Классифицируй намерение."}, {"role": "user", "content": text}]
            response = await self.client.chat.completions.create(model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.0)
            return json.loads(response.choices[0].message.content)
        except: return {"intent": "create_request", "args": {}}

    def build_preview(self, draft: dict) -> str:
        import html
        lines = []
        field_labels = {
            "regions": "🌍 Направление", "transport_cat": "🚛 Транспорт",
            "delivery_terms": "📦 Инкотермс",
            "route_from": "📍 Откуда", "route_to": "📍 Куда",
            "cargo_name": "📦 Груз", "cargo_weight": "⚖️ Вес",
            "cargo_places": "📏 Места", "cargo_volume": "📦 Объем", "cargo_value": "💰 Стоимость",
            "hs_code": "📝 ТН ВЭД", "customs_address": "🏛 Затаможка",
            "clearance_address": "🏛 Растаможка", "loading_address": "📍 Погрузка",
            "unloading_address": "📍 Выгрузка", "urgency_type": "🕒 Срочность",
            "transit_info": "🛣 Транзит", "extra_info": "📝 Доп. инфо", 
            "transport_sub": "🚛 Вид авто", "temp_control": "🌡 Темп. режим", "adr_class": "🔥 ADR класс",
            "border_crossing_cn": "🌉 Погранпереход"
        }
        for key, label in field_labels.items():
            val = draft.get(key)
            if val is None or str(val).strip().lower() in ("", "-", "none", "false", "null"):
                continue
            
            if key == "cargo_weight":
                try:
                    num = float(str(val).replace(",", ".").split()[0])
                    val = f"{int(num)} кг" if num > 0 else val
                except: pass
            
            safe_val = html.escape(str(val))
            lines.append(f"{label}: <b>{safe_val}</b>")
            
        missing = draft.get("missing_fields", [])
        if missing:
            safe_missing = html.escape(", ".join(missing))
            lines.append(f"\n⚠️ <b>Не хватает:</b> {safe_missing}")
        question = draft.get("next_question")
        if question:
            safe_question = html.escape(str(question))
            lines.append(f"\n🤖 {safe_question}")
        return "\n".join(lines) if lines else "📋 Черновик пуст"

    def merge_parsed_data(self, old_draft: dict, new_data: dict) -> dict:
        merged = dict(old_draft) if old_draft else {}
        skip_keys = {"not_logistics", "error", "next_question", "missing_fields", "ready_to_publish"}
        for k, v in new_data.items():
            if k in skip_keys: continue
            if v in (None, "null", "", "-", "None"):
                if k in merged: del merged[k]
                continue
            merged[k] = v
        merged["ready_to_publish"] = new_data.get("ready_to_publish", False)
        merged["next_question"] = new_data.get("next_question")
        merged["missing_fields"] = new_data.get("missing_fields", [])
        return merged

    def to_request_fields(self, draft: dict) -> dict:
        db_fields = {}
        field_map = {
            "regions": "regions", "transport_cat": "transport_cat",
            "delivery_terms": "delivery_terms",
            "route_from": "route_from", "route_to": "route_to",
            "loading_address": "loading_address", "customs_address": "customs_address",
            "clearance_address": "clearance_address", "unloading_address": "unloading_address",
            "cargo_name": "cargo_name", "hs_code": "hs_code",
            "cargo_value": "cargo_value", "cargo_weight": "cargo_weight",
            "cargo_places": "cargo_places", "cargo_volume": "cargo_volume", "urgency_type": "urgency_type",
            "extra_info": "message_text", "transport_sub": "transport_sub", 
            "temp_control": "temp_control", "adr_class": "adr_class", "transit_info": "transit_rf_allowed",
            "border_crossing_cn": "border_crossing_cn"
        }
        for draft_key, db_key in field_map.items():
            val = draft.get(draft_key)
            if val and str(val).strip() not in ("", "-", "None", "null"):
                db_fields[db_key] = str(val).strip()
        return db_fields

    async def answer_db_query(self, question: str, db_module) -> str:
        try:
            messages = [{"role": "system", "content": "Ты аналитик AGL."}, {"role": "user", "content": question}]
            response = await self.client.chat.completions.create(model=self.model, messages=messages, temperature=0.0)
            return response.choices[0].message.content
        except: return "Ошибка"

    async def transcribe_audio(self, file_path: str):
        try:
            with open(file_path, "rb") as audio_file:
                transcript = await self.client.audio.transcriptions.create(model="whisper-1", file=audio_file)
                return transcript.text
        except: return None

ai_assistant = AIAssistant()
