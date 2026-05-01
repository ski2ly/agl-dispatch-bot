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
            logger.info(f"🤖 AI Assistant enabled (model: {self.model})")

    def _get_system_prompt(self, settings=None):
        today = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
        extra = settings.get("ai_prompt_extra", "") if settings else ""
        strictness = settings.get("ai_strictness", "medium") if settings else "medium"
        
        strict_note = "БУДЬ ОЧЕНЬ СТРОГИМ." if strictness == "high" else ""

        return f"""Ты — профессиональный логистический координатор компании AGL.
Твоя цель — собрать данные для транспортной заявки. ТЫ ДОЛЖЕН БЫТЬ УМНЫМ И ПОНИМАТЬ СЛЕНГ.

{strict_note}
{extra}

ПРАВИЛА ПОНИМАНИЯ ДАННЫХ:
- "Затаможка на месте" или "Там же" — адрес затаможки СОВПАДАЕТ с адресом погрузки.
- "Растаможка на месте" или "Там же" — адрес растаможки совпадает с адресом выгрузки.
- "20ка", "сорокафутовый", "реф" — это типы транспорта.
- "цена 2000", "за две тысячи" — это cargo_value.

ОБЯЗАТЕЛЬНЫЕ ПОЛЯ:
1. 🌍 Регион (regions): СНГ, Европа, Китай, Турция, Индия/ЮВА, Другое.
2. 🚛 Транспорт (transport_cat): Авто, Контейнер, Ж/Д Вагон, Авиа, Мультимодальная.
3. 📍 Откуда/Куда (route_from/route_to).
4. 📍 Затаможка/Растаможка (customs_address/clearance_address) — ОБЯЗАТЕЛЬНО для всех, КРОМЕ СНГ.
5. 💰 Стоимость (cargo_value) и 📝 ТН ВЭД (hs_code) — ОБЯЗАТЕЛЬНО.

ФОРМАТ ОТВЕТА (JSON):
{{
  "regions": "...", "transport_cat": "...",
  "route_from": "...", "route_to": "...",
  "loading_address": "...", "customs_address": "...", "clearance_address": "...", "unloading_address": "...",
  "cargo_name": "...", "hs_code": "...", "cargo_value": "...", "cargo_weight": "...", "cargo_places": "...",
  "missing_fields": ["поля, которых не хватает"],
  "next_question": "вежливый вопрос",
  "ready_to_publish": false,
  "not_logistics": false
}}

Сегодняшняя дата: {today}
"""

    async def parse_request(self, text, current_draft=None, templates=None):
        if not self.enabled: return {"error": "AI Assistant disabled"}
        try:
            from database import db
            settings = await db.get_settings()
            messages = [{"role": "system", "content": self._get_system_prompt(settings)}]
            if current_draft:
                messages.append({"role": "system", "content": f"Текущий черновик: {json.dumps(current_draft, ensure_ascii=False)}"})
            if templates:
                messages.append({"role": "system", "content": f"Прошлые заявки: {json.dumps(templates, ensure_ascii=False)}"})
            
            messages.append({"role": "user", "content": text})
            response = await self.client.chat.completions.create(
                model=self.model, messages=messages, response_format={"type": "json_object"}, temperature=0.1, timeout=15.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"AI Parse error: {e}")
            return {"error": str(e)}

    async def transcribe_audio(self, file_path: str):
        if not self.enabled: return None
        try:
            with open(file_path, "rb") as audio:
                transcript = await self.client.audio.transcriptions.create(model="whisper-1", file=audio)
                return transcript.text
        except Exception as e:
            logger.error(f"AI Transcribe error: {e}"); return None

    async def process_intent(self, text):
        if not self.enabled: return {"error": "AI disabled"}
        system_msg = """Ты умный диспетчер. Пойми намерение пользователя.
- Хочет создать/дополнить заявку -> create_request.
- Хочет найти старую заявку ('помнишь...', 'найти...') -> recall_request.
- Хочет отменить текущее действие -> cancel_request.
- Логист дает ставку -> create_bid.
- Вопрос по базе/статистике -> query_database.
- Болтовня -> chat."""

        tools = [
            {"type": "function", "function": {"name": "create_request", "description": "Создание/правка заявки", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "recall_request", "description": "Найти старую заявку", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "cancel_request", "description": "Отмена черновика", "parameters": {"type": "object", "properties": {"confirmed": {"type": "boolean"}}, "required": ["confirmed"]}}},
            {"type": "function", "function": {"name": "create_bid", "description": "Подать ставку", "parameters": {"type": "object", "properties": {"route_search": {"type": "string"}, "amount": {"type": "number"}, "currency": {"type": "string"}}, "required": ["route_search", "amount", "currency"]}}},
            {"type": "function", "function": {"name": "query_database", "description": "Поиск/статистика", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
        ]

        try:
            res = await self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": text}],
                tools=tools, temperature=0.1, timeout=15.0
            )
            msg = res.choices[0].message
            if msg.tool_calls:
                call = msg.tool_calls[0]
                return {"intent": call.function.name, "args": json.loads(call.function.arguments)}
            return {"intent": "chat", "text": msg.content}
        except Exception as e:
            logger.error(f"Intent router error: {e}"); return {"error": str(e)}

    async def answer_db_query(self, text, db):
        if not self.enabled: return "ИИ отключен."
        open_reqs = await db.list_requests(status="Открыта", limit=10)
        stats = await db.get_stats()
        context = f"Статистика: {json.dumps(stats, ensure_ascii=False)}\nЗаявки: {json.dumps(open_reqs, ensure_ascii=False)}"
        try:
            res = await self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": f"Ты аналитик AGL. Данные: {context}"}, {"role": "user", "content": text}],
                temperature=0.3, timeout=15.0
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"DB Query error: {e}"); return "Ошибка при запросе к базе."

    def merge_parsed_data(self, old: dict, new: dict):
        merged = old.copy()
        for k, v in new.items():
            if k in ["missing_fields", "next_question", "not_logistics", "ready_to_publish"]:
                merged[k] = v
                continue
            if v and str(v).strip() not in ("", "-", "не указано", "None"):
                merged[k] = v
        return merged

    def to_request_fields(self, parsed: dict):
        SKIP = {"missing_fields", "next_question", "not_logistics", "ready_to_publish"}
        return {k: str(v) if v is not None else "-" for k, v in parsed.items() if k not in SKIP}

    def build_preview(self, parsed: dict):
        lines = [
            f"🌍 **Направление:** {parsed.get('regions', '-')}",
            f"🚛 **Транспорт:** {parsed.get('transport_cat', '-')}",
            f"📍 **Откуда:** {parsed.get('route_from', '-')} | **Куда:** {parsed.get('route_to', '-')}",
            f"💰 **Стоимость:** {parsed.get('cargo_value') or '⚠️ НЕ УКАЗАНА'}",
            f"📦 **Груз:** {parsed.get('cargo_name', '-')}",
            f"⚖️ **Вес:** {parsed.get('cargo_weight', '-')}"
        ]
        if parsed.get("next_question"):
            lines.append(f"\n❓ {parsed['next_question']}")
        return "\n".join(lines)

ai_assistant = AIAssistant()
