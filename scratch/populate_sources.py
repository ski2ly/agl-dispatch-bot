import asyncio
import os
from database import db

async def main():
    await db.init_db()
    sources = [
        "Сарафанное радио", "Instagram", "Facebook", "Google", "Яндекс", 
        "2ГИС", "Партнёр / реферал", "Мероприятие / выставка", 
        "Yellow Pages", "Golden Pages", "Другое"
    ]
    await db.update_setting('sources', sources)
    print(f"✅ Successfully added {len(sources)} sources to settings.")
    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
