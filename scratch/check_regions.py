import asyncio
from database import db

async def check():
    s = await db.get_settings()
    regions = s.get('regions', [])
    print("--- REGIONS IN DB ---")
    for r in regions:
        print(f"- {r.get('name') if isinstance(r, dict) else r}")
    print("---------------------")

if __name__ == "__main__":
    asyncio.run(check())
