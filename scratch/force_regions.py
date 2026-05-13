import asyncio
import os
import json
from database import db

async def force_update_regions():
    await db.init_db()
    new_regions = [
        {"name": "СНГ", "emoji": "🗺️"},
        {"name": "Европа", "emoji": "🇪🇺"},
        {"name": "Китай", "emoji": "🇨🇳"},
        {"name": "Турция", "emoji": "🇹🇷"},
        {"name": "Индия/ЮВА", "emoji": "🇮🇳"},
        {"name": "Америка", "emoji": "🇺🇸"},
        {"name": "ОАЭ", "emoji": "🇦🇪"},
        {"name": "Другое", "emoji": "🌐"}
    ]
    await db.update_setting("regions", new_regions)
    print("✅ Regions successfully updated to the new list!")
    await db.close()

if __name__ == "__main__":
    asyncio.run(force_update_regions())
