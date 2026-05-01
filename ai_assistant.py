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
        # Don't instantiate AsyncOpenAI when key is missing — its constructor raises
        # OpenAIError on None, which would crash the bot at import time even when
        # AI features are intentionally disabled.
        self.client = AsyncOpenAI(api_key=api_key) if self.enabled else None
        if self.enabled:
            logger.info(f"🤖 AI Assistant enabled (model: {self.model})")
        else:
            logger.warning("AI Assistant disabled (OPENAI_API_KEY not set) — voice transcription and /ai will not work")

    def _get_system_prompt(self, settings=None):
        today = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
        extra = settings.get("ai_prompt_extra", "") if settings else ""
        strictness = settings.get("ai_strictness", "medium") if settings else "medium"
        
        strict_note = ""
        if strictness == "high":
            strict_note = "БУДЬ ОЧЕНЬ СТРОГИМ. Не разрешай публикацию, если не хватает хотя бы одного обязательного поля."
        elif strictness == "low":
            strict_note = "БУДЬ ЛОЯЛЬНЫМ. Если пользователь сказал 'это всё', разрешай публикацию даже без веса/объема."

        return f"""Ты — интеллектуальный диспетчер компании AGL.
Твоя цель — ПОЛНОСТЬЮ управлять процессом создания заявки, пока она не будет готова к публикации.

{strict_note}
{extra}

ОБЯЗАТЕЛЬНЫЕ ПОЛЯ (БЕЗ НИХ НЕЛЬЗЯ ПУБЛИКОВАТЬ):
1. 🌍 Регион (regions) и 🚛 Транспорт (transport_cat).
2. 📍 Откуда (route_from) и 📍 Куда (route_to) — ОБЯЗАТЕЛЬНЫ ДЛЯ ВСЕХ НАПРАВЛЕНИЙ (города/страны).
3. 📍 Затаможка (customs_address) и 📍 Растаможка (clearance_address) — ОБЯЗАТЕЛЬНЫ ДЛЯ ВСЕХ, КРОМЕ СНГ.
4. 💰 Стоимость груза (cargo_value) — ВСЕГДА ОБЯЗАТЕЛЬНО.
5. 📝 ТН ВЭД (hs_code) — ВСЕГДА ОБЯЗАТЕЛЬНО.
6. 📦 Наименование груза (cargo_name).
7. ⚖️ Вес (cargo_weight) — ОБЯЗАТЕЛЬНО УКАЗЫВАЙ ЕДИНИЦЫ: «20 тн», «500 кг», «1.5 тн». Никогда не пиши просто число.
8. 📏 Объем/Места (cargo_places) — если указаны, писать «10 м³» или «5 палет».

ПРАВИЛА ОПРЕДЕЛЕНИЯ РЕГИОНА:
- АВТОМАТИЧЕСКИ определяй регион ("regions") по городам и странам. Тебе НЕ НУЖНО спрашивать регион у менеджера.
- "СНГ": перевозки между РФ, Беларусью, Узбекистаном, Казахстаном и др. странами СНГ (например: "из Москвы в Ахангаран", "Минск - Ташкент").
- "Европа": если маршрут включает Европу (например: "Германия - РФ", "Италия - Узбекистан").
- "Китай": если маршрут включает Китай (например: "Гуанчжоу - Москва").
- "Другое": маршруты из остальных стран (Турция, Индия, США и т.д.).

РЕКОМЕНДУЕМЫЕ (Желательно, но можно пропустить):
- Точный адрес погрузки (loading_address) и выгрузки (unloading_address) (если указаны только города, оставляй пустыми).
- Затаможка/Растаможка (только для СНГ).

СПЕЦИФИЧНЫЕ ДЛЯ РЕГИОНА И ТРАНСПОРТА (ОБЯЗАТЕЛЬНО УТОЧНЯТЬ, ЕСЛИ НЕ УКАЗАНЫ):
🌍 ЕВРОПА:
- Условия поставки (delivery_terms_eu): EXW или FCA.
- Маршрут (route_type): «через РФ + КЗ», «через Турцию» или «ТРАСЕКА (Поти)».
- Если Море/Мульти: Порт (ports_list): «Поти → авто/ж/д» или «Констанца → …».

🌍 КИТАЙ:
- Если Авто: Погранпереход (border_crossing_cn): «КЗ (Хоргос)» или «Кашгар (Иркештам)». Тип фуры (road_type_cn): «Целая фура» или «Сборная».
- Если Ж/Д: Маршрут (route_type): «через КЗ (Хоргос)» или «через КЗ (Достык)».

🌍 СНГ:
- Если Авто: Маршрут (route_type): «Прямой» или «Глонас-пломба».

🌍 ТУРЦИЯ:
- Если Авто: Маршрут (route_type): «Прямой», «через Иран» или «через Грузию».
- Если Ж/Д: Маршрут (route_type): «BTK → Иран».

🌍 ИНДИЯ / ЮВА:
- Если Море/Мульти: Порт (ports_list): «Поти (Грузия)», «Иран (Бандар-Аббас)», «Пакистан (Карачи)» или «Владивосток».

✈️ АВИА:
- Рейс (flight_type): «Прямой» или «Транзитный».
- Штабелируемый (stackable): Да / Нет. ПРИМЕЧАНИЕ: Для Авиа МАРШРУТ (через РФ и т.д.) НЕ УКАЗЫВАЕТСЯ.

ФОРМАТ ОТВЕТА (JSON):
{{
  "regions": "Европа | Китай | СНГ | Индия/ЮВА | Турция | Другое",
  "transport_cat": "Авто | Ж/Д | Авиа | Мультимодальная",
  "route_from": "...",
  "route_to": "...",
  "loading_address": "...",
  "customs_address": "...",
  "clearance_address": "...",
  "unloading_address": "...",
  "cargo_name": "...",
  "hs_code": "...",
  "cargo_value": "...",
  "cargo_weight": "...",
  "cargo_places": "...",
  "dangerous_cargo": "...",
  "urgency_type": "...",
  "delivery_terms_eu": "...",
  "route_type": "...",
  "export_decl": "...",
  "origin_cert": "...",
  "road_type_cn": "...",
  "border_crossing_cn": "...",
  "container_owner": "...",
  "glonass_seal": "...",
  "loading_days": "...",
  "customs_days": "...",
  "stackable": "...",
  "flight_type": "...",
  "ports_list": "...",
  "message_text": "...",

  "missing_fields": ["список НЕЗАПОЛНЕННЫХ ОБЯЗАТЕЛЬНЫХ полей"],
  "next_question": "твой вопрос пользователю для получения данных",
  "ready_to_publish": false,
  "not_logistics": false
}}

ПРАВИЛА ПОВЕДЕНИЯ:
- Реагируй на каждое сообщение. Если данных не хватает — спрашивай конкретно.
- Если пользователь говорит "это всё", а обязательных полей (например, стоимости) нет — предупреди: "Без стоимости я не смогу отправить заявку в канал, пожалуйста, укажите её".
- Если город написан с опечаткой — исправь.
- Ты помнишь контекст всего диалога через присланный тебе JSON черновика.
- По СНГ: если нет затаможки/растаможки — это нормально, переходи к другим полям.

Сегодняшняя дата: {today}
"""

    async def parse_request(self, text, current_draft=None, templates=None):
        if not self.enabled:
            return {"error": "AI Assistant disabled"}
        try:
            from database import db
            settings = await db.get_settings()
            
            messages = [
                {"role": "system", "content": self._get_system_prompt(settings)}
            ]
            if templates:
                messages.append({"role": "system", "content": f"Доступные шаблоны (ID и описание): {json.dumps(templates)}"})
            
            messages.append({"role": "user", "content": text})
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=15.0
            )
            
            return json.loads(response.choices[0].message.content)
        except asyncio.TimeoutError:
            logger.error("AI Parse error: Timeout")
            return {"error": "Превышено время ожидания ответа от ИИ. Попробуйте еще раз."}
        except Exception as e:
            if "timeout" in str(e).lower():
                logger.error("AI Parse error: Timeout")
                return {"error": "Серверы ИИ перегружены (таймаут). Попробуйте отправить запрос еще раз."}
            logger.error(f"AI Parse error: {e}")
            return {"error": str(e)}

    async def transcribe_audio(self, file_path: str):
        if not self.enabled:
            return None
        try:
            with open(file_path, "rb") as audio:
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio
                )
                return transcript.text
        except Exception as e:
            logger.error(f"AI Transcribe error: {e}")
            return None

    async def process_intent(self, text):
        if not self.enabled: return {"error": "AI disabled"}
        
        system_msg = """Ты умный диспетчер-роутер. Твоя задача — понять намерение пользователя.
Если он хочет создать или дополнить заявку на груз — вызывай create_request.
Если он логист и хочет дать ставку (цену) на маршрут — вызывай create_bid.
Если он хочет узнать статистику, список открытых заявок или ID заявок — вызывай query_database.
Если это болтовня, не относящаяся к логистике — отвечай текстом (чат)."""

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "create_request",
                    "description": "Создание или редактирование заявки",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "create_bid",
                    "description": "Подача ставки логистом. Примеры: 'ставка москва ташкент 2000 usd', 'ставка #0023 1000 USD', 'ставка на последнюю заявку 500 USD'",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "route_search": {"type": "string", "description": "ID заявки (например '#0023' или '23'), города маршрута (например 'Москва Ташкент'), или 'последняя' для последней заявки"},
                            "amount": {"type": "number", "description": "Сумма ставки"},
                            "currency": {"type": "string", "description": "Валюта (USD, EUR, RUB, CNY, UZS)"}
                        },
                        "required": ["route_search", "amount", "currency"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "query_database",
                    "description": "Поиск заявок, статистика, получение ID открытых заявок",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Суть запроса пользователя"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

        try:
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": text}],
                tools=tools,
                temperature=0.1,
                timeout=15.0
            )
            msg = res.choices[0].message
            if msg.tool_calls:
                call = msg.tool_calls[0]
                return {
                    "intent": call.function.name,
                    "args": json.loads(call.function.arguments)
                }
            else:
                return {"intent": "chat", "text": msg.content}
        except Exception as e:
            logger.error(f"Intent router error: {e}")
            return {"error": str(e)}

    async def answer_db_query(self, text, db):
        if not self.enabled: return "Функции ИИ отключены."
        
        open_reqs = await db.list_requests(status="Открыта", limit=50)
        stats = await db.get_stats()
        
        context_data = "СТАТИСТИКА:\n" + json.dumps(stats, ensure_ascii=False) + "\n\nОТКРЫТЫЕ ЗАЯВКИ:\n"
        for r in open_reqs:
            context_data += f"ID: #{r['id']} | Маршрут: {r['route_from']} -> {r['route_to']} | Груз: {r['cargo_name']} | Регион: {r['regions']}\n"
            
        sys_msg = f"""Ты аналитик логистической компании AGL. Ответь на вопрос пользователя, используя данные из базы.
Отвечай четко, профессионально и коротко. Можешь использовать Markdown.
ДАННЫЕ ИЗ БД:
{context_data}"""

        try:
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": text}],
                temperature=0.3,
                timeout=15.0
            )
            return res.choices[0].message.content
        except Exception as e:
            logger.error(f"DB Query error: {e}")
            return "❌ Ошибка при запросе к базе. Попробуйте позже."

    def merge_parsed_data(self, old: dict, new: dict):
        """Merge new parsed fields into existing draft, avoiding overwriting with empty values."""
        merged = old.copy()
        for k, v in new.items():
            if k in ["missing_fields", "next_question", "not_logistics", "template_match", "ready_to_publish"]:
                merged[k] = v
                continue
            
            # Only update if the new value is meaningful
            if v and str(v).strip() not in ("", "-", "не указано", "None"):
                merged[k] = v
        return merged

    def to_request_fields(self, parsed: dict):
        """Map AI output to DB fields."""
        SKIP_KEYS = {"missing_fields", "next_question", "not_logistics", "template_match", "ready_to_publish"}
        fields = {}
        for k, v in parsed.items():
            if k in SKIP_KEYS:
                continue
            fields[k] = str(v) if v is not None else "-"
        return fields

    def build_preview(self, parsed: dict):
        """Build a professional text preview of the draft."""
        lines = [
            f"🌍 **Направление:** {parsed.get('regions', '-')}",
            f"🚛 **Транспорт:** {parsed.get('transport_cat', '-')}",
            "",
            f"📍 **Откуда:** {parsed.get('route_from', '-')} | **Куда:** {parsed.get('route_to', '-')}",
            f"📍 **Погрузка:** {parsed.get('loading_address') or '-'}",
            f"📍 **Затаможка:** {parsed.get('customs_address') or '-'}",
            f"📍 **Растаможка:** {parsed.get('clearance_address') or '-'}",
            f"📍 **Выгрузка:** {parsed.get('unloading_address') or '-'}",
            "",
            f"📦 **Груз:** {parsed.get('cargo_name', '-')}",
            f"📝 **ТН ВЭД:** {parsed.get('hs_code', '-')}",
            f"💰 **Стоимость:** {parsed.get('cargo_value') or '⚠️ НЕ УКАЗАНА'}",
            "",
            f"⚖️ **Вес:** {parsed.get('cargo_weight', '-')} | **Места/Объем:** {parsed.get('cargo_places', '-')}",
            f"🕒 **Срочность:** {parsed.get('urgency_type', '-')}"
        ]

        # Region/Transport specific fields
        spec_fields = []
        spec_map = {
            "delivery_terms_eu": "Условия", "route_type": "Маршрут", "export_decl": "Экспортная", 
            "origin_cert": "Сертификат", "road_type_cn": "Тип фуры", "border_crossing_cn": "Погранпереход",
            "container_owner": "Контейнер", "glonass_seal": "Пломба", "loading_days": "Дней на погрузку",
            "customs_days": "Дней на затаможку", "stackable": "Штабелируемый", "flight_type": "Рейс", "ports_list": "Порт"
        }
        for k, v in spec_map.items():
            val = parsed.get(k)
            if val and str(val).strip() not in ("-", "", "None"):
                spec_fields.append(f"• **{v}:** {val}")
        
        if spec_fields:
            lines.append("\n📋 **Специфика:**")
            lines.extend(spec_fields)

        missing = parsed.get("missing_fields", [])
        if missing:
            lines.append(f"\n⚠️ **Не хватает:** {', '.join(missing)}")
        
        if parsed.get("next_question"):
            lines.append(f"\n❓ {parsed['next_question']}")
        
        return "\n".join(lines)

ai_assistant = AIAssistant()
