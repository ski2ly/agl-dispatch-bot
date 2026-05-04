import asyncio
import os
from database import db

async def main():
    await db.init_db()
    s = await db.get_settings()
    print("SETTINGS:", s)
    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
