import asyncio
import os
from database import Database

async def check():
    db = Database()
    await db.init_db()
    async with db._pool.acquire() as conn:
        rows = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'requests'")
        for r in rows:
            print(r['column_name'])
    await db.close()

if __name__ == "__main__":
    asyncio.run(check())
