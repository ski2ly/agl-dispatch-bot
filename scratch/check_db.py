import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def check():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        return
    conn = await asyncpg.connect(dsn)
    print("--- COMMENTS TABLE SCHEMA ---")
    cols = await conn.fetch("""
        SELECT column_name, is_nullable, column_default, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'comments' 
        AND table_schema = 'public' 
        ORDER BY ordinal_position
    """)
    for c in cols:
        print(f"{c['column_name']}: {c['data_type']} (Nullable: {c['is_nullable']}, Default: {c['column_default']})")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(check())
