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
            "regions": "Направление", 
            "transport_cat": "Тип перевозки",
            "transport_sub": "Вид",
            "source": "Источник",
            "route_from": "Маршрут_Откуда", # Helper for split display
            "route_to": "Маршрут_Куда",
            "loading_address": "Погрузка",
            "customs_address": "Затаможка",
            "clearance_address": "Растаможка",
            "unloading_address": "Выгрузка",
            "cargo_name": "Груз",
            "hs_code": "ТН ВЭД",
            "adr_class": "Класс ADR",
            "cargo_weight": "Вес",
            "cargo_places": "Мест",
            "cargo_volume": "Объем",
            "packaging": "Упаковка",
            "cargo_value": "Стоимость",
            "urgency_type": "Срочность",
            "border_crossing_cn": "Погранпереход",
            "transit_info": "Транзит",
            "delivery_terms": "Инкотермс",
            "extra_info": "Дополнительно"
        }
        
        # Build preview in the SAME order as helpers.build_card
        # 1. Header
        reg = draft.get("regions", "Другое")
        t_cat = draft.get("transport_cat", "Авто")
        lines.append(f"Направление: <b>{html.escape(str(reg))}</b>")
        lines.append(f"Тип перевозки: <b>{html.escape(str(t_cat))}</b>")
        
        if draft.get("transport_sub"):
            lines.append(f"Вид: <b>{html.escape(str(draft.get('transport_sub')))}</b>")
        
        lines.append(f"Источник: <b>{html.escape(str(draft.get('source', 'Не указан')))}</b>")
        lines.append("")
        
        # 2. Route
        r_from = draft.get("route_from", "?")
        r_to = draft.get("route_to", "?")
        lines.append(f"<b>{html.escape(str(r_from))} ➔ {html.escape(str(r_to))}</b>")
        
        for key, label in [("loading_address", "Погрузка"), ("customs_address", "Затаможка"), 
                           ("clearance_address", "Растаможка"), ("unloading_address", "Выгрузка")]:
            val = draft.get(key)
            if val and str(val).strip() not in ("-", "", "None", "null"):
                lines.append(f"{label}: <b>{html.escape(str(val))}</b>")
        
        lines.append("")
        
        # 3. Cargo
        lines.append(f"Груз: <b>{html.escape(str(draft.get('cargo_name', '?')))}</b>")
        if draft.get("hs_code"):
            lines.append(f"ТН ВЭД: <b>{html.escape(str(draft.get('hs_code')))}</b>")
        
        if draft.get("adr_class"):
            lines.append(f"Класс ADR: <b>{html.escape(str(draft.get('adr_class')))}</b>")
            
        lines.append("")
        
        # 4. Units
        if draft.get("cargo_weight"):
            w = str(draft.get("cargo_weight"))
            if "кг" not in w.lower(): w = f"{w} кг"
            lines.append(f"Вес: <b>{html.escape(w)}</b>")
            
        if draft.get("cargo_places"):
            lines.append(f"Мест: <b>{html.escape(str(draft.get('cargo_places')))}</b>")
            
        if draft.get("cargo_volume"):
            vol = str(draft.get("cargo_volume"))
            if "м" not in vol.lower() and "m" not in vol.lower(): vol = f"{vol} м³"
            lines.append(f"Объем: <b>{html.escape(vol)}</b>")
            
        if draft.get("packaging"):
            lines.append(f"Упаковка: <b>{html.escape(str(draft.get('packaging')))}</b>")
            
        lines.append("")
        
        # 5. Money & Urgency
        val = draft.get("cargo_value")
        if val:
            curr = draft.get("cargo_currency") or "USD"
            lines.append(f"Стоимость: <b>{html.escape(str(val))} {html.escape(str(curr))}</b>")
        else:
            lines.append("Стоимость: <b>НЕ УКАЗАНА</b>")
            
        lines.append(f"Срочность: <b>{html.escape(str(draft.get('urgency_type', 'Стандарт')))}</b>")
        
        # 6. Specifics
        spec_fields = []
        for k, label in [("border_crossing_cn", "Погранпереход"), ("transit_info", "Транзит"), ("delivery_terms", "Инкотермс")]:
            val = draft.get(k)
            if val and str(val).strip() not in ("-", "", "None", "null"):
                spec_fields.append(f"• {label}: {html.escape(str(val))}")
        
        if spec_fields:
            lines.append("\nСпецифика:")
            lines.extend(spec_fields)
            
        if draft.get("extra_info"):
            lines.append(f"\nДополнительно:\n<b>{html.escape(str(draft.get('extra_info')))}</b>")
            
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
