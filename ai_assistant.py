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

        return f"""Ты — Эксперт-Логист AGL. Твоя задача: идеально извлечь данные.

### ГЕОГРАФИЯ (СТРОГО):
- ИНДИЯ/ЮВА: Индия, Филиппины (Philippines), Вьетнам, Малайзия, Таиланд, Индонезия.
- КИТАЙ: Только материковый Китай. ПРИОРИТЕТ: Если есть Китай — Китай.
- ЕВРОПА: ЕС.
- ОАЭ: Эмираты.

### ПРАВИЛА ВЕСА И ОБЪЕМА:
- В cargo_weight, cargo_volume ПИШИ ТОЛЬКО ЧИСЛА.
- Если в тексте "20 тонн" -> пиши "20000".
- Если "50 кубов" -> пиши "50".
- Убирай любые слова (кг, тонны, м3, фут).

### ДОПОЛНИТЕЛЬНО (extra_info):
- Обязательно пиши сюда специфику погрузки: слип-шиты, ручная перегрузка, паллеты, навалом. Это ВАЖНО.

### ФОРМАТ JSON:
{{
  "regions": "Индия/ЮВА",
  "transport_cat": "Контейнер",
  "transport_sub": "20DC",
  "route_from": "General Santos, Филиппины", "route_to": "Ташкент, Узбекистан",
  "cargo_name": "Консервированные ананасы", "cargo_weight": "20000", "hs_code": "2008207900",
  "extra_info": "Погрузка слип-шитами, перегрузка ручная.", 
  "missing_fields": ["Стоимость"],
  "ready_to_publish": false,
  "next_question": "Уточните стоимость груза и валюту?"
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
            response = await self.client.chat.completions.create(model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.0)
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
        reg = draft.get("regions", "Другое")
        t_cat = draft.get("transport_cat", "Авто")
        lines.append(f"Направление: <b>{html.escape(str(reg))}</b>")
        lines.append(f"Тип перевозки: <b>{html.escape(str(t_cat))}</b>")
        if draft.get("transport_sub"):
            lines.append(f"Вид: <b>{html.escape(str(draft.get('transport_sub')))}</b>")
        
        lines.append(f"Источник: <b>{html.escape(str(draft.get('source', 'Не указан')))}</b>")
        if draft.get("client_company"):
            lines.append(f"Заказчик: <b>{html.escape(str(draft.get('client_company')))}</b>")
        lines.append("")
        
        r_from = draft.get("route_from", "?")
        r_to = draft.get("route_to", "?")
        lines.append(f"<b>{html.escape(str(r_from))} ➔ {html.escape(str(r_to))}</b>")
        
        for key, label in [("loading_address", "Погрузка"), ("customs_address", "Затаможка"), 
                           ("clearance_address", "Растаможка"), ("unloading_address", "Выгрузка")]:
            val = draft.get(key)
            if val and str(val).strip() not in ("-", "", "None", "null"):
                lines.append(f"{label}: <b>{html.escape(str(val))}</b>")
        
        lines.append("")
        lines.append(f"Груз: <b>{html.escape(str(draft.get('cargo_name', 'не указан')))}</b>")
        if draft.get("hs_code"):
            lines.append(f"ТН ВЭД: <b>{html.escape(str(draft.get('hs_code')))}</b>")
        if draft.get("temp_control") == "Да":
            lines.append(f"Температурный режим: <b>{html.escape(str(draft.get('temp_range', 'да')))}</b>")
            
        lines.append("")
        if draft.get("cargo_weight"):
            w = str(draft.get("cargo_weight"))
            # CLEANUP: Remove any text from weight if it was leaked by AI
            import re
            nums = re.findall(r'\d+', w)
            if nums:
                val_num = int(nums[0])
                lines.append(f"Вес: <b>{val_num} кг</b>")
            else:
                lines.append(f"Вес: <b>{html.escape(w)}</b>")
                
        if draft.get("cargo_places"):
            lines.append(f"Мест: <b>{html.escape(str(draft.get('cargo_places')))}</b>")
        if draft.get("cargo_volume"):
            vol = str(draft.get("cargo_volume"))
            import re
            nums = re.findall(r'\d+', vol)
            if nums:
                val_num = int(nums[0])
                lines.append(f"Объем: <b>{val_num} м³</b>")
            else:
                lines.append(f"Объем: <b>{html.escape(vol)}</b>")
            
        lines.append("")
        val = draft.get("cargo_value")
        if val:
            curr = draft.get("cargo_currency") or "USD"
            lines.append(f"Стоимость: <b>{html.escape(str(val))} {html.escape(str(curr))}</b>")
        else:
            lines.append("Стоимость: <b>НЕ УКАЗАНА</b>")
        lines.append(f"Срочность: <b>{html.escape(str(draft.get('urgency_type', 'Стандарт')))}</b>")
        
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
            lines.append(f"\n⚠️ Не хватает: {safe_missing}")
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
            "cargo_name": "cargo_name", "hs_code": "hs_code", "client_company": "client_company",
            "cargo_value": "cargo_value", "cargo_weight": "cargo_weight",
            "cargo_places": "cargo_places", "cargo_volume": "cargo_volume", "urgency_type": "urgency_type",
            "extra_info": "message_text", "transport_sub": "transport_sub", 
            "temp_control": "temp_control", "temp_range": "temp_range",
            "adr_class": "adr_class", "transit_info": "transit_rf_allowed",
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
