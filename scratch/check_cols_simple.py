import asyncio
import os
import asyncpg

async def check():
    dsn = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'requests'")
    for r in rows:
        print(r['column_name'])
    await conn.close()

if __name__ == "__main__":
    asyncio.run(check())
