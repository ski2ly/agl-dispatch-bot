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

        return f"""Ты — Старший Логист-Аналитик AGL. Твоя задача: идеально извлечь структурированные данные.

ИНСТРУКЦИЯ ПО АНАЛИЗУ:
1. РЕГИОН: Определи страну города и выбери из списка: [{regions_str}].
2. ТРАНСПОРТ: "Тент/Фура" -> Авто, "Чартер/Борт" -> Авиа, "Контейнер" -> Контейнер.
3. МЕСТА (cargo_places): Кол-во паллет, коробок, ящиков (например, "29 паллет").
4. ПРАВИЛО ПЫЛЕСОСА: ВСЁ описание (размеры паллет, штабелирование, инструкции связаться с кем-то, даты погрузки) ЗАПИСЫВАЙ в `extra_info`.
5. ИНКОТЕРМС: EXW, FCA, CPT и т.д. -> delivery_terms.

СТРУКТУРА JSON (используй null если данных нет):
{{
  "regions": "...",
  "client_company": "Заказчик",
  "urgency_type": "Стандарт/Срочно",
  "transport_cat": "Авиа/Авто/Контейнер/ЖД/Сборный",
  "transport_sub": "Вид (Тент, 20 фут, Чартер и т.д.)",
  "delivery_terms": "...",
  "route_from": "Город, Страна",
  "route_to": "Город, Страна",
  "cargo_name": "...",
  "cargo_weight": "число в кг",
  "cargo_volume": "число в м3",
  "cargo_places": "...",
  "hs_code": "...",
  "extra_info": "ВСЯ специфика здесь",
  "missing_fields": ["Заказчик", "Стоимость", "Вес", "Объем", "ТН ВЭД"],
  "next_question": "..."
}}

Сегодня: {today}
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
        import re
        
        def v(key, default=None):
            val = draft.get(key)
            if val is not None and str(val).strip() not in ("-", "", "None", "null", "не указано", "False", "false"):
                return str(val).strip()
            return default

        lines = []
        
        # Title synced with helpers.py
        title = "[НОВАЯ ЗАЯВКА #00000]"
        if v('urgency_type') == "Срочно":
            title += " - СРОЧНО"
        lines.append(title)
        lines.append("")

        reg = v("regions", "Другое")
        t_cat = v("transport_cat", "Авто")
        lines.append(f"Направление: {reg}")
        lines.append(f"Тип перевозки: {t_cat}")
        
        t_sub = v("transport_sub")
        if t_sub:
            lines.append(f"Вид: {t_sub}")
        
        lines.append(f"Источник: {v('source', 'Не указан')}")
        lines.append("")
        
        r_from = v("route_from", "?")
        r_to = v("route_to", "?")
        lines.append(f"{r_from} ➔ {r_to}")
        
        for key, label in [("loading_address", "Погрузка"), ("customs_address", "Затаможка"), 
                           ("clearance_address", "Растаможка"), ("unloading_address", "Выгрузка")]:
            val = v(key)
            if val: lines.append(f"{label}: {val}")
        
        lines.append("")
        lines.append(f"Груз: {v('cargo_name', 'не указан')}")
        if v("hs_code"): lines.append(f"ТН ВЭД: {v('hs_code')}")
        if v("adr_class"): lines.append(f"Класс ADR: {v('adr_class')}")
        if v("temp_control") == "Да":
            lines.append(f"Температурный режим: {v('temp_range', 'да')}")
            
        lines.append("")
        if v("cargo_weight"):
            w = v("cargo_weight")
            nums = re.findall(r'\d+', w)
            if nums:
                lines.append(f"Вес: {int(nums[0])} кг")
            else:
                lines.append(f"Вес: {w}")
        if v("cargo_places"):
            lines.append(f"Мест: {v('cargo_places')}")
        if v("cargo_volume"):
            vol = v("cargo_volume")
            nums = re.findall(r'\d+', vol)
            if nums:
                lines.append(f"Объем: {int(nums[0])} м³")
            else:
                lines.append(f"Объем: {vol}")
            
        lines.append("")
        val = v("cargo_value")
        if val:
            curr = v("cargo_currency") or "USD"
            lines.append(f"Стоимость: {val} {curr}")
        else:
            lines.append("Стоимость: НЕ УКАЗАНА")
            
        lines.append(f"Срочность: {v('urgency_type', 'Стандарт')}")
        
        spec_fields = []
        for k, label in [("border_crossing_cn", "Погранпереход"), ("transit_info", "Транзит"), ("delivery_terms", "Инкотермс")]:
            val = v(k)
            if val: spec_fields.append(f"• {label}: {val}")
        if spec_fields:
            lines.append("")
            lines.append("Специфика:")
            lines.extend(spec_fields)
            
        extra = v("extra_info")
        if extra:
            lines.extend(["", "Дополнительно:", extra])
            
        lines.extend(["", "Менеджер: Admin", "#заявка"])
        
        # Missing fields and bot question at the very end
        missing = draft.get("missing_fields", [])
        if missing:
            translations = {
                "client_company": "Заказчик", "cargo_weight": "Вес", "cargo_volume": "Объем",
                "cargo_value": "Стоимость", "hs_code": "ТН ВЭД", "transport_sub": "Вид",
                "route_from": "Откуда", "route_to": "Куда"
            }
            mapped_missing = [translations.get(m, m) for m in missing]
            lines.append(f"\n⚠️ Не хватает: {', '.join(mapped_missing)}")
            
        question = draft.get("next_question")
        if question:
            lines.append(f"\n🤖 {question}")
            
        return "\n".join(lines) if lines else "📋 Черновик пуст"

    def merge_parsed_data(self, old_draft: dict, new_data: dict) -> dict:
        merged = dict(old_draft) if old_draft else {}
        skip_keys = {"not_logistics", "error", "next_question", "missing_fields", "ready_to_publish", "_reasoning"}
        for k, v in new_data.items():
            if k in skip_keys: continue
            if v in (None, "null", "", "-", "None"):
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
