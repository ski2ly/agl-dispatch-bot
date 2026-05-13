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
        regions_str = "|".join([r["name"] if isinstance(r, dict) else str(r) for r in regions_list]) or "СНГ|Европа|Китай|Турция|Индия/ЮВА|Другое"
        
        transport_types = settings.get("transport_types", []) if settings else []
        transport_str = "|".join(str(t) for t in transport_types) or "Авто|Контейнер|Ж/Д Вагон|Авиа|Мультимодальная"

        return f"""Ты — строгий Робот-Секретарь логистической компании AGL. 
Твоя задача: быстро и точно заполнять карточку заявки, не допуская отсебятины.

### ПРАВИЛА РАБОТЫ (СКРИПТ):
1. НИКОГДА НЕ ПРИДУМЫВАЙ ДАННЫЕ. Если вес, объем или места не указаны — ставь строго null. 
2. СКРЫТИЕ ПУСТОТЫ: В итоговом JSON для пользователя отображай только то, что реально указано. 
3. СПРАВОЧНИК ТН ВЭД: Если пользователь просит "Найди код ТН ВЭД", используй свои знания и запиши наиболее подходящий код в поле "hs_code".
4. УДАЛЕНИЕ: Если просят "убрать" или "стереть" — ставь null.
5. ЯЗЫК: Весь диалог и список недостающих полей — СТРОГО НА РУССКОМ.

### СПИСОК ПОЛЕЙ (для "missing_fields" используй только РУССКИЕ названия):
- route_from, route_to -> "Маршрут (Откуда/Куда)"
- cargo_name -> "Название груза"
- cargo_weight -> "Вес"
- cargo_volume -> "Объем"
- cargo_places -> "Количество мест"
- cargo_value -> "Стоимость груза"
- hs_code -> "Код ТН ВЭД"
- transport_sub -> "Вид транспорта (Тент/Реф и т.д.)"
- customs_address -> "Адрес затаможки"
- clearance_address -> "Адрес растаможки"
- loading_address -> "Адрес погрузки"
- unloading_address -> "Адрес выгрузки"
- temp_control -> "Температурный режим"
- dangerous_cargo -> "Класс опасности (ADR)"

### ФОРМАТ JSON:
{{
  "regions": "{regions_str}",
  "transport_cat": "{transport_str}",
  "transport_sub": null,
  "route_from": null, "route_to": null,
  "cargo_name": null, "cargo_weight": null, "cargo_volume": null, "cargo_places": null, "cargo_value": null, "hs_code": null,
  "extra_info": null, 
  "missing_fields": ["Русское название 1", "Русское название 2"],
  "ready_to_publish": false,
  "next_question": "Короткий вопрос на русском о том, чего не хватает"
}}

### УСЛОВИЕ ПУБЛИКАЦИИ:
- ready_to_publish: true ТОЛЬКО если указаны: Откуда, Куда, Груз, Вес, Места, Объем. 
- Если это не СНГ — также обязательны адреса затаможки/растаможки.

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
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"AI Parse error: {e}")
            return {"error": str(e)}

    async def process_intent(self, text: str):
        if not self.enabled: return {"error": "AI Assistant disabled"}
        try:
            messages = [
                {"role": "system", "content": """Классифицируй намерение пользователя (create_request, finish_request, create_bid, cancel_request, chat)."""},
                {"role": "user", "content": text}
            ]
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            return {"intent": "create_request", "args": {}}

    def build_preview(self, draft: dict) -> str:
        import html
        lines = []
        field_labels = {
            "regions": "🌍 Направление", "transport_cat": "🚛 Транспорт",
            "route_from": "📍 Откуда", "route_to": "📍 Куда",
            "cargo_name": "📦 Груз", "cargo_weight": "⚖️ Вес",
            "cargo_places": "📏 Места", "cargo_volume": "📦 Объем", "cargo_value": "💰 Стоимость",
            "hs_code": "📝 ТН ВЭД", "customs_address": "🏛 Затаможка",
            "clearance_address": "🏛 Растаможка", "loading_address": "📍 Погрузка",
            "unloading_address": "📍 Выгрузка", "urgency_type": "🕒 Срочность",
            "extra_info": "📝 Доп. инфо", "transport_sub": "🚛 Вид авто",
            "temp_control": "🌡 Темп. режим", "adr_class": "🔥 ADR класс"
        }
        for key, label in field_labels.items():
            val = draft.get(key)
            if val is None or str(val).strip().lower() in ("", "-", "none", "false", "null"):
                continue
            safe_val = html.escape(str(val))
            lines.append(f"{label}: <b>{safe_val}</b>")

        # Missing fields in Russian (as returned by the AI now)
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
            "route_from": "route_from", "route_to": "route_to",
            "loading_address": "loading_address", "customs_address": "customs_address",
            "clearance_address": "clearance_address", "unloading_address": "unloading_address",
            "cargo_name": "cargo_name", "hs_code": "hs_code",
            "cargo_value": "cargo_value", "cargo_weight": "cargo_weight",
            "cargo_places": "cargo_places", "cargo_volume": "cargo_volume", "urgency_type": "urgency_type",
            "extra_info": "message_text", "transport_sub": "transport_sub", 
            "temp_control": "temp_control", "adr_class": "adr_class", "responsible": "responsible"
        }
        for draft_key, db_key in field_map.items():
            val = draft.get(draft_key)
            if val and str(val).strip() not in ("", "-", "None", "null"):
                db_fields[db_key] = str(val).strip()
        return db_fields

    async def answer_db_query(self, question: str, db_module) -> str:
        # Same as before
        try:
            stats = await db_module.get_stats(days=0)
            messages = [{"role": "system", "content": "Ты аналитик AGL."}, {"role": "user", "content": question}]
            response = await self.client.chat.completions.create(model=self.model, messages=messages, temperature=0.0)
            return response.choices[0].message.content
        except: return "Ошибка"

    async def transcribe_audio(self, file_path: str):
        if not self.enabled: return None
        try:
            with open(file_path, "rb") as audio_file:
                transcript = await self.client.audio.transcriptions.create(model="whisper-1", file=audio_file)
                return transcript.text
        except: return None

ai_assistant = AIAssistant()
