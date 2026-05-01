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

        return f"""You are a professional logistics coordinator for AGL.
Your goal is to collect data for a transport request. YOU MUST BE SMART AND UNDERSTAND SLANG.

{strict_note}
{extra}

DATA UNDERSTANDING RULES (VERY IMPORTANT):
- If the client writes "customs on site", "customs there", "TT on site" - you MUST copy the Loading City/Address into the `customs_address` field!
- If the client writes "clearance on site", "clearance there", "RT on site" - you MUST copy the Unloading City/Address into the `clearance_address` field!
- "20ka", "40ft", "ref" - these are transport types (Container/Auto).
- "price 2000", "for two thousand" - this is cargo_value (be sure to add currency, e.g. "2000 USD").

MANDATORY FIELDS FOR `ready_to_publish: true`:
1. Transport (transport_cat).
2. From/To (route_from/route_to).
3. Customs/Clearance (customs_address/clearance_address) - MANDATORY for everyone EXCEPT CIS. If not specified - set false.
4. Value (cargo_value) and HS Code (hs_code) - MANDATORY. If not specified - set false.
5. Weight (cargo_weight) and Places (cargo_places) - MANDATORY.

RESPONSE FORMAT (JSON):
{{
  "regions": "CIS|Europe|China|Turkey|India/SEA|Other",
  "transport_cat": "Auto|Container|Wagon|Air|Multimodal",
  "route_from": "...", "route_to": "...",
  "loading_address": "...", "customs_address": "...", "clearance_address": "...", "unloading_address": "...",
  "cargo_name": "...", "hs_code": "...", "cargo_value": "...", "cargo_weight": "...", "cargo_places": "...",
  "missing_fields": ["fields that are missing"],
  "next_question": "Your polite question to clarify details",
  "ready_to_publish": boolean,
  "not_logistics": boolean
}}

If `ready_to_publish` = false, in `next_question` you must ask exactly for the fields that are missing.

Today's date: {today}
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
        system_msg = """You are a smart dispatcher. Understand user intent.
- Wants to create/update request -> create_request.
- Wants to find old request -> recall_request.
- Wants to cancel action -> cancel_request.
- Giving a bid -> create_bid.
- Question about DB/stats -> query_database.
- Chat -> chat."""

        tools = [
            {"type": "function", "function": {"name": "create_request", "description": "Create/edit request", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "recall_request", "description": "Find old request", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "cancel_request", "description": "Cancel draft", "parameters": {"type": "object", "properties": {"confirmed": {"type": "boolean"}}, "required": ["confirmed"]}}},
            {"type": "function", "function": {"name": "create_bid", "description": "Submit a bid", "parameters": {"type": "object", "properties": {"route_search": {"type": "string"}, "amount": {"type": "number"}, "currency": {"type": "string"}}, "required": ["route_search", "amount", "currency"]}}},
            {"type": "function", "function": {"name": "query_database", "description": "Search/stats", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
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
        if not self.enabled: return "AI disabled."
        open_reqs = await db.list_requests(status="Open", limit=10)
        stats = await db.get_stats()
        context = f"Stats: {json.dumps(stats, ensure_ascii=False)}\nRequests: {json.dumps(open_reqs, ensure_ascii=False)}"
        try:
            res = await self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": f"You are AGL analyst. Data: {context}"}, {"role": "user", "content": text}],
                temperature=0.3, timeout=15.0
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"DB Query error: {e}"); return "DB error."

    def merge_parsed_data(self, old: dict, new: dict):
        merged = old.copy()
        for k, v in new.items():
            if k in ["missing_fields", "next_question", "not_logistics", "ready_to_publish"]:
                merged[k] = v
                continue
            if v and str(v).strip() not in ("", "-", "not specified", "None"):
                merged[k] = v
        return merged

    def to_request_fields(self, parsed: dict):
        SKIP = {"missing_fields", "next_question", "not_logistics", "ready_to_publish"}
        return {k: str(v) if v is not None else "-" for k, v in parsed.items() if k not in SKIP}

    def build_preview(self, parsed: dict):
        is_sng = parsed.get("regions") == "CIS" or parsed.get("regions") == "СНГ"
        
        lines = [
            f"🌍 **Направление:** {parsed.get('regions', '-')}",
            f"🚛 **Транспорт:** {parsed.get('transport_cat', '-')}",
            f"📍 **Откуда:** {parsed.get('route_from', '-')} | **Куда:** {parsed.get('route_to', '-')}",
            f"💰 **Стоимость:** {parsed.get('cargo_value') or '⚠️ НЕ УКАЗАНА'}",
            f"📦 **Груз:** {parsed.get('cargo_name', '-')}",
            f"⚖️ **Вес:** {parsed.get('cargo_weight', '-')}",
            f"📦 **Места:** {parsed.get('cargo_places', '-')}",
            f"📍 **Погрузка:** {parsed.get('loading_address', '-')}",
            f"📍 **Выгрузка:** {parsed.get('unloading_address', '-')}",
            f"🧾 **ТН ВЭД:** {parsed.get('hs_code') or '⚠️ НЕ УКАЗАН'}"
        ]
        
        customs = parsed.get("customs_address")
        clearance = parsed.get("clearance_address")
        
        if customs and customs != "-":
            lines.append(f"🚩 **Затаможка:** {customs}")
        elif not is_sng:
            lines.append(f"🚩 **Затаможка:** ⚠️ НЕ УКАЗАНА")
            
        if clearance and clearance != "-":
            lines.append(f"🏁 **Растаможка:** {clearance}")
        elif not is_sng:
            lines.append(f"🏁 **Растаможка:** ⚠️ НЕ УКАЗАНА")

        if parsed.get("next_question"):
            lines.append(f"\n❓ {parsed['next_question']}")
        return "\n".join(lines)

ai_assistant = AIAssistant()
